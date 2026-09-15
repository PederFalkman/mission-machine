"""Rolling-horizon dispatch - the solver with only the foresight a node would have.

``planning/optimal.py`` measures the dispatch rules against a solver that knows
every hour of the mission in advance. That number is real but it flatters the
solver: a controller on a node cannot see the weather three days out, and the
gap it opens up is partly "the rules are crude" and partly "the rules cannot see
the future". Nothing separated the two.

This module separates them. The mission is replanned on a fixed cadence over a
finite lookahead window: solve for the next ``window_h`` hours, commit the first
``commit_h`` of that plan, advance the node state, and solve again. With a
window as long as the mission it reproduces the perfect-foresight answer
exactly; with a short one it is close to myopic. Sweeping the window says how
much of the gap foresight was buying.

Two things matter for the result to mean anything:

* **The end effect is handled.** A finite window with a fuel objective will
  empty the battery on its last step, because energy it cannot see a use for
  costs nothing to spend. Every window that does not reach the end of the
  mission credits its closing stored energy at the best generator's fuel rate,
  which is the standard treatment and keeps a short horizon from being punished
  for a modelling artefact.
* **The service floor is per window.** The discretionary energy the rules
  delivered in those hours is required of the solver in those hours, so a
  rolling plan cannot win by deferring service past the end of the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mission_machine.assets.inventory import AssetInventory
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import Configuration
from mission_machine.planning.milp import build_model, variable_name
from mission_machine.planning.providers import (
    DEFAULT_REGISTRY,
    DISPATCH_MILP,
    ProviderRegistry,
    SolverStatus,
)
from mission_machine.simulation.simulator import SimulationResult

FORESIGHT_CAVEAT = (
    "Within its window the controller still has a perfect forecast. This measures the value of "
    "lookahead length, not of forecast accuracy - a node whose forecast is wrong will do worse "
    "than any figure here."
)


@dataclass
class WindowRecord:
    """One replan: what was solved, and what was committed from it."""

    start_hour: float
    end_hour: float
    committed_to_hour: float
    status: SolverStatus
    wall_time_s: float
    fuel_burned_l: float
    stored_after_kwh: float
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "committed_to_hour": self.committed_to_hour,
            "status": str(self.status),
            "wall_time_s": round(self.wall_time_s, 2),
            "fuel_burned_l": round(self.fuel_burned_l, 2),
            "stored_after_kwh": round(self.stored_after_kwh, 2),
            "verified": self.verified,
        }


@dataclass
class RollingHorizonResult:
    """What a controller with ``window_h`` hours of lookahead actually achieved."""

    configuration_id: str
    label: str
    window_h: float
    commit_h: float
    backend: str
    completed: bool = False
    fuel_l: float | None = None
    baseline_fuel_l: float = 0.0
    perfect_foresight_fuel_l: float | None = None
    windows: list[WindowRecord] = field(default_factory=list)
    detail: str = ""
    caveats: list[str] = field(default_factory=list)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def solves(self) -> int:
        return len(self.windows)

    @property
    def wall_time_s(self) -> float:
        return sum(window.wall_time_s for window in self.windows)

    @property
    def saving_vs_rules_l(self) -> float | None:
        if self.fuel_l is None:
            return None
        return self.baseline_fuel_l - self.fuel_l

    @property
    def saving_vs_rules_fraction(self) -> float | None:
        saving = self.saving_vs_rules_l
        if saving is None or self.baseline_fuel_l <= 0:
            return None
        return saving / self.baseline_fuel_l

    @property
    def gap_recovered_fraction(self) -> float | None:
        """Share of the perfect-foresight gap this much lookahead recovers."""

        if self.fuel_l is None or self.perfect_foresight_fuel_l is None:
            return None
        whole = self.baseline_fuel_l - self.perfect_foresight_fuel_l
        if whole <= 1e-9:
            return None
        return (self.baseline_fuel_l - self.fuel_l) / whole

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "label": self.label,
            "window_h": self.window_h,
            "commit_h": self.commit_h,
            "backend": self.backend,
            "completed": self.completed,
            "fuel_l": round(self.fuel_l, 1) if self.fuel_l is not None else None,
            "baseline_fuel_l": round(self.baseline_fuel_l, 1),
            "perfect_foresight_fuel_l": (
                round(self.perfect_foresight_fuel_l, 1)
                if self.perfect_foresight_fuel_l is not None
                else None
            ),
            "saving_vs_rules_l": (
                round(self.saving_vs_rules_l, 1) if self.saving_vs_rules_l is not None else None
            ),
            "saving_vs_rules_fraction": (
                round(self.saving_vs_rules_fraction, 4)
                if self.saving_vs_rules_fraction is not None
                else None
            ),
            "gap_recovered_fraction": (
                round(self.gap_recovered_fraction, 4)
                if self.gap_recovered_fraction is not None
                else None
            ),
            "solves": self.solves,
            "wall_time_s": round(self.wall_time_s, 1),
            "windows": [window.to_dict() for window in self.windows],
            "detail": self.detail,
            "caveats": list(self.caveats),
            "data_labels": list(self.data_labels),
        }


def run_rolling_horizon(
    mission: MissionSpec,
    configuration: Configuration,
    simulation: SimulationResult,
    *,
    window_h: float,
    commit_h: float = 6.0,
    environment: Environment | None = None,
    inventory: AssetInventory | None = None,
    registry: ProviderRegistry | None = None,
    time_budget_s: float = 20.0,
    baseline_fuel_l: float | None = None,
    perfect_foresight_fuel_l: float | None = None,
) -> RollingHorizonResult:
    """Replan every ``commit_h`` hours over a ``window_h`` lookahead, and total the fuel."""

    registry = registry or DEFAULT_REGISTRY
    inventory = inventory or mission.inventory
    duration = mission.mission_duration_h
    dt = mission.time_step_h
    battery = inventory.batteries[0] if inventory.batteries else None

    result = RollingHorizonResult(
        configuration_id=configuration.configuration_id,
        label=configuration.label or configuration.configuration_id,
        window_h=window_h,
        commit_h=commit_h,
        backend="none",
        baseline_fuel_l=(
            baseline_fuel_l
            if baseline_fuel_l is not None
            else sum(step.fuel_used_l for step in simulation.steps)
        ),
        perfect_foresight_fuel_l=perfect_foresight_fuel_l,
    )

    stored_kwh = (
        battery.initial_state_of_charge * battery.energy_capacity_kwh
        if battery is not None and configuration.policy.use_battery
        else 0.0
    )
    fuel_remaining = mission.fuel_limit_l
    total_fuel = 0.0
    hour = 0.0

    while hour < duration - 1e-9:
        window_end = min(hour + window_h, duration)
        reaches_end = window_end >= duration - 1e-9
        model = build_model(
            mission,
            environment,
            inventory,
            configuration=configuration,
            service_floor=simulation,
            service_floor_window=True,
            start_hour=hour,
            end_hour=window_end,
            initial_stored_kwh=stored_kwh,
            initial_fuel_l=fuel_remaining,
            terminal_storage_value=not reaches_end,
        )
        outcome = registry.solve(
            model, problem_class=DISPATCH_MILP, time_budget_s=time_budget_s
        )
        result.backend = outcome.backend
        if not outcome.is_answer:
            result.detail = (
                f"Replan at H+{hour:.0f} returned {outcome.status}: {outcome.detail}"
            )
            result.caveats.append(
                "The controller could not produce a plan for one of its windows, so no fuel "
                "figure is reported. A lookahead this short can strand the node in a state "
                "from which its own horizon offers no feasible continuation."
            )
            return result

        violations = model.verify(outcome.assignment, tolerance=1e-3)
        committed_steps = max(1, int(round(min(commit_h, window_end - hour) / dt)))
        committed_to = min(hour + committed_steps * dt, window_end)

        closing = _state_after(outcome.assignment, committed_steps - 1, battery)
        burned = fuel_remaining - closing["fuel"]
        total_fuel += burned
        fuel_remaining = closing["fuel"]
        if battery is not None and configuration.policy.use_battery:
            stored_kwh = closing["soc"]

        result.windows.append(
            WindowRecord(
                start_hour=hour,
                end_hour=window_end,
                committed_to_hour=committed_to,
                status=outcome.status,
                wall_time_s=outcome.wall_time_s,
                fuel_burned_l=burned,
                stored_after_kwh=stored_kwh,
                verified=not violations,
            )
        )
        if violations:
            result.detail = (
                f"The solver's answer for the window at H+{hour:.0f} does not satisfy the "
                "declared constraint set, so no fuel figure is reported."
            )
            return result
        hour = committed_to

    result.completed = True
    result.fuel_l = total_fuel
    result.caveats.append(FORESIGHT_CAVEAT)
    result.caveats.append(
        "Windows that do not reach the end of the mission credit their closing stored energy at "
        "the best generator's fuel rate, so that a short horizon is not punished for emptying a "
        "battery it cannot see a use for."
    )
    if any(window.status is SolverStatus.FEASIBLE for window in result.windows):
        result.caveats.append(
            "At least one replan stopped at its time budget without proving optimality, which is "
            "what a real controller on a clock would also do."
        )
    return result


def _state_after(assignment: dict[str, float], index: int, battery) -> dict[str, float]:
    """Node state at the end of committed step ``index`` of a solved window."""

    def value(prefix: str) -> float:
        return assignment.get(f"{prefix}_{index}", 0.0)

    return {
        "fuel": value("fuel"),
        "soc": value("soc") if battery is not None else 0.0,
    }


# --------------------------------------------------------------------------
# closed loop: planning against one world and living in another
# --------------------------------------------------------------------------


@dataclass
class ClosedLoopResult:
    """What a controller achieved in the world that actually happened."""

    arm: str
    configuration_id: str
    window_h: float
    commit_h: float
    forecast: str
    realised: str
    completed: bool = False
    fuel_l: float = 0.0
    critical_coverage: float = 0.0
    endurance_h: float = 0.0
    plan_overrides: int = 0
    unplannable_windows: int = 0
    solves: int = 0
    wall_time_s: float = 0.0
    detail: str = ""
    caveats: list[str] = field(default_factory=list)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def assured(self) -> bool:
        return self.completed and self.critical_coverage >= 0.99 - 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "configuration_id": self.configuration_id,
            "window_h": self.window_h,
            "commit_h": self.commit_h,
            "forecast": self.forecast,
            "realised": self.realised,
            "completed": self.completed,
            "assured": self.assured,
            "fuel_l": round(self.fuel_l, 1),
            "critical_coverage": round(self.critical_coverage, 4),
            "endurance_h": round(self.endurance_h, 1),
            "plan_overrides": self.plan_overrides,
            "unplannable_windows": self.unplannable_windows,
            "solves": self.solves,
            "wall_time_s": round(self.wall_time_s, 1),
            "detail": self.detail,
            "caveats": list(self.caveats),
            "data_labels": list(self.data_labels),
        }


def _summarise(steps: list, mission: MissionSpec, start_hour: float) -> dict[str, float]:
    demand = sum(step.critical_demand_kw * step.duration_h for step in steps)
    served = sum(step.critical_served_kw * step.duration_h for step in steps)
    shortfall = [step for step in steps if step.unserved_critical_kw > 1e-6]
    return {
        "fuel": sum(step.fuel_used_l for step in steps),
        "coverage": served / demand if demand > 1e-9 else 1.0,
        "endurance": (
            shortfall[0].hour - start_hour
            if shortfall
            else mission.mission_duration_h - start_hour
        ),
        "completed": not shortfall,
        "overrides": sum(
            1 for step in steps if any("did not commit" in note for note in step.notes)
        ),
    }


def _fall_back(simulator, configuration, state, start: float, end: float):
    """Run a window on the dispatch rules, and report what it consumed."""

    run = simulator.run(
        configuration, start_hour=start, end_hour=end, initial_state=state
    )
    shortfall = next(
        (step.hour for step in run.steps if step.unserved_critical_kw > 1e-6), None
    )
    return run.final_state, {
        "fuel": sum(step.fuel_used_l for step in run.steps),
        "demand": sum(step.critical_demand_kw * step.duration_h for step in run.steps),
        "served": sum(step.critical_served_kw * step.duration_h for step in run.steps),
        "shortfall": shortfall,
    }


def _fix_commitment(model, assignment, generators, steps: int) -> None:
    """Pin a window's commitment to what the plan decided."""

    from mission_machine.planning.milp import LinearConstraint

    for gen in generators:
        for index in range(steps):
            name = variable_name("u", gen.asset_id, index)
            if name not in model.variables:
                continue
            planned = 1.0 if assignment.get(name, 0.0) > 0.5 else 0.0
            model.add_constraint(
                LinearConstraint(
                    name=f"fixed_{name}",
                    terms={name: 1.0},
                    sense="==",
                    rhs=planned,
                    category="requirement",
                    description=(
                        f"{gen.asset_id} commitment at step {index} is what the plan decided; "
                        "real-time dispatch may not re-commit."
                    ),
                )
            )


def run_closed_loop(
    mission: MissionSpec,
    configuration: Configuration,
    simulation: SimulationResult,
    *,
    realised_environment: Environment,
    forecast_environment: Environment | None = None,
    window_h: float = 12.0,
    commit_h: float = 6.0,
    inventory: AssetInventory | None = None,
    registry: ProviderRegistry | None = None,
    time_budget_s: float = 15.0,
    arm: str = "planned",
) -> ClosedLoopResult:
    """Plan against ``forecast_environment``; live in ``realised_environment``.

    Each replan solves over its lookahead window using the forecast, and commits
    only the *generator commitment* - which machines are synchronised in each of
    the committed hours. Those hours are then simulated in the world that
    actually happened, under the same transparent dispatch rules as everywhere
    else, which balance in real time within the commitment they were handed.

    The plan hands over two things: which machines are committed, and the
    stored-energy trajectory it intended. Commitment alone turned out to be
    incoherent - a plan commits a generator in an hour because it means to run
    it hard and bank the surplus, and a dispatcher given only the commitment
    pays the no-load fuel without banking anything, which made a correct
    forecast *worse* than no plan at all.

    That division is deliberate and is how such systems are actually run:
    committing a machine is a decision with lead time and is planned ahead;
    balancing is done on the spot against what is really there. A generator the
    plan did not commit is started only if the hour needs it, and each such
    override is counted - it is the visible cost of having planned against the
    wrong world.
    """

    from mission_machine.simulation.simulator import Simulator

    registry = registry or DEFAULT_REGISTRY
    inventory = inventory or mission.inventory
    forecast_environment = forecast_environment or realised_environment
    duration = mission.mission_duration_h
    dt = mission.time_step_h
    battery = inventory.batteries[0] if inventory.batteries else None
    generators = [
        gen for gen in inventory.generators if gen.asset_id in configuration.policy.generator_ids
    ]
    fallback_simulator = Simulator(mission, realised_environment, inventory)

    result = ClosedLoopResult(
        arm=arm,
        configuration_id=configuration.configuration_id,
        window_h=window_h,
        commit_h=commit_h,
        forecast=forecast_environment.environment_id,
        realised=realised_environment.environment_id,
    )

    state = fallback_simulator.initial_state()
    total_fuel = 0.0
    served_kwh = 0.0
    demand_kwh = 0.0
    first_shortfall: float | None = None
    hour = 0.0

    while hour < duration - 1e-9:
        window_end = min(hour + window_h, duration)
        stored = (
            state.battery_soc * battery.energy_capacity_kwh
            if battery is not None and configuration.policy.use_battery
            else 0.0
        )
        common = dict(
            configuration=configuration,
            service_floor=simulation,
            service_floor_window=True,
            initial_stored_kwh=stored,
            initial_fuel_l=state.fuel_remaining_l,
        )

        # 1. Plan the window against the forecast.
        plan_model = build_model(
            mission,
            forecast_environment,
            inventory,
            start_hour=hour,
            end_hour=window_end,
            terminal_storage_value=window_end < duration - 1e-9,
            **common,
        )
        plan = registry.solve(plan_model, problem_class=DISPATCH_MILP, time_budget_s=time_budget_s)
        result.solves += 1
        result.wall_time_s += plan.wall_time_s

        committed_steps = max(1, int(round(min(commit_h, window_end - hour) / dt)))
        committed_to = min(hour + committed_steps * dt, window_end)

        if not plan.is_answer:
            # No plan exists for this window - usually because the mission
            # itself cannot be met from here, and the model requires the
            # critical loads in full while the rules may shed. A node does not
            # stop planning and wait; it runs on its rules. Counted, and the run
            # continues, because "the solver had nothing to offer" is an answer.
            result.unplannable_windows += 1
            state, consumed = _fall_back(
                fallback_simulator, configuration, state, hour, committed_to
            )
            total_fuel += consumed["fuel"]
            demand_kwh += consumed["demand"]
            served_kwh += consumed["served"]
            if consumed["shortfall"] is not None and first_shortfall is None:
                first_shortfall = consumed["shortfall"]
            hour = committed_to
            continue

        # 2. Carry it out in the world that actually happened. Commitment is
        #    fixed to what the plan decided - a machine cannot be synchronised
        #    retrospectively - and everything else re-balances. That is economic
        #    dispatch, and it is an LP once the binaries are pinned.
        realised_model = build_model(
            mission,
            realised_environment,
            inventory,
            start_hour=hour,
            end_hour=committed_to,
            # The realisation is a finite horizon too: without the terminal
            # credit it empties the battery inside the committed window and
            # strands the next one.
            terminal_storage_value=committed_to < duration - 1e-9,
            **common,
        )
        _fix_commitment(realised_model, plan.assignment, generators, committed_steps)
        realised = registry.solve(
            realised_model, problem_class=DISPATCH_MILP, time_budget_s=time_budget_s
        )
        result.solves += 1
        result.wall_time_s += realised.wall_time_s

        if realised.is_answer:
            closing_fuel = realised.assignment.get(
                variable_name("fuel", committed_steps - 1), state.fuel_remaining_l
            )
            total_fuel += state.fuel_remaining_l - closing_fuel
            state.fuel_remaining_l = closing_fuel
            if battery is not None and configuration.policy.use_battery:
                closing_soc = realised.assignment.get(
                    variable_name("soc", committed_steps - 1), stored
                )
                state.battery_soc = closing_soc / battery.energy_capacity_kwh
            for gen in generators:
                running = (
                    realised.assignment.get(
                        variable_name("g", gen.asset_id, committed_steps - 1), 0.0
                    )
                    > 1e-6
                )
                state.generator_running[gen.asset_id] = running
            for index in range(committed_steps):
                ambient = realised_environment.temperature_c(hour + index * dt)
                step_demand = mission.critical_demand_kw(hour + index * dt, ambient) * dt
                demand_kwh += step_demand
                served_kwh += step_demand  # the model requires critical load in full
        else:
            # 3. The plan could not be carried out. A node does not stop; it
            #    reverts to its dispatch rules, which can shed. Recorded, because
            #    this is the sharp end of having planned against the wrong world.
            result.plan_overrides += 1
            state, consumed = _fall_back(
                fallback_simulator, configuration, state, hour, committed_to
            )
            total_fuel += consumed["fuel"]
            demand_kwh += consumed["demand"]
            served_kwh += consumed["served"]
            if consumed["shortfall"] is not None and first_shortfall is None:
                first_shortfall = consumed["shortfall"]

        hour = committed_to

    result.completed = first_shortfall is None
    result.fuel_l = total_fuel
    result.critical_coverage = served_kwh / demand_kwh if demand_kwh > 1e-9 else 1.0
    result.endurance_h = first_shortfall if first_shortfall is not None else duration
    if result.unplannable_windows:
        result.caveats.append(
            f"No plan existed for {result.unplannable_windows} of the windows - the mission "
            "cannot be met in full from that state, and the model requires the critical loads "
            "where the rules may shed. Those windows ran on the rules."
        )
    result.caveats.append(
        "The plan fixes the generator commitment - a machine cannot be synchronised "
        "retrospectively - and the rest re-balances against the world that actually happened. "
        "Where even that is impossible the node reverts to its dispatch rules, and each such "
        "window is counted."
    )
    return result


def run_rules_only(
    mission: MissionSpec,
    configuration: Configuration,
    *,
    realised_environment: Environment,
    inventory: AssetInventory | None = None,
) -> ClosedLoopResult:
    """The same world, with no solver at all - the arm everything is measured against."""

    from mission_machine.simulation.simulator import Simulator

    inventory = inventory or mission.inventory
    simulator = Simulator(mission, realised_environment, inventory)
    run = simulator.run(configuration)
    summary = _summarise(list(run.steps), mission, 0.0)
    return ClosedLoopResult(
        arm="rules only",
        configuration_id=configuration.configuration_id,
        window_h=0.0,
        commit_h=0.0,
        forecast="none",
        realised=realised_environment.environment_id,
        completed=bool(summary["completed"]),
        fuel_l=summary["fuel"],
        critical_coverage=summary["coverage"],
        endurance_h=summary["endurance"],
    )
