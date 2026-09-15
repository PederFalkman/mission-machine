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
from mission_machine.planning.milp import build_model
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
