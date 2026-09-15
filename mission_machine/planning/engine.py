"""Deterministic planning engine.

The engine does three things, in this order, and nothing else:

1. **Enumerate** a small, explicit space of candidate configurations (which
   assets are used, and under what dispatch policy).
2. **Evaluate** each candidate with the deterministic simulator.
3. **Rank** the candidates once per strategy, using a stated lexicographic
   ordering.

It does not decide. It returns several feasible options with their trade-offs
so that an operator can choose. If no candidate is feasible, it says so and
returns the least-bad options with the constraint that was violated named
explicitly - it never silently relaxes a mission requirement.

Why enumerate-and-simulate rather than call a MILP solver? Pack 1 needs a
baseline that runs anywhere with no solver installed, and that produces
schedules an operator can read line by line. The MILP formulation of the same
problem is written out in ``planning/milp.py``; every schedule this engine
produces is checked against that constraint set, so the baseline cannot drift
away from the declared model. Replacing this ranking step with a real MILP/CP
solve is the first Pack 2 task.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from mission_machine.assets.base import AssetKind, FailureState
from mission_machine.assets.inventory import AssetInventory
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import (
    Configuration,
    DispatchPolicy,
    GeneratorMode,
    SecondaryPolicy,
)
from mission_machine.planning.metrics import ConfigurationMetrics, compute_metrics
from mission_machine.resilience.failures import FailureEvent
from mission_machine.simulation.simulator import SimulationResult, Simulator, effective_state
from mission_machine.simulation.state import NodeState


class Strategy(str, Enum):
    """The planning intents Pack 1 offers. One option is produced per strategy."""

    MAX_ENDURANCE = "MAX_ENDURANCE"
    MIN_FUEL = "MIN_FUEL"
    MIN_LOGISTICS = "MIN_LOGISTICS"
    #: Not offered by default. Available when the operator asks what it would
    #: take to keep the discretionary functions running (operator priority 4).
    MAX_SUPPORTED_FUNCTIONS = "MAX_SUPPORTED_FUNCTIONS"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


STRATEGY_INTENT = {
    Strategy.MAX_ENDURANCE: (
        "Hold the critical functions for as long as possible, and keep the largest "
        "energy reserve. Discretionary load is sacrificed first."
    ),
    Strategy.MIN_FUEL: (
        "Complete the mission on the least fuel, so that what is left in the tanks "
        "is available for whatever comes next."
    ),
    Strategy.MIN_LOGISTICS: (
        "Complete the mission with the fewest assets to move, set up and supervise, "
        "accepting higher fuel use and less redundancy."
    ),
    Strategy.MAX_SUPPORTED_FUNCTIONS: (
        "Keep as many secondary functions running as the mission requirements allow, "
        "without breaching critical-load assurance or the energy reserve."
    ),
}

STRATEGY_LABEL = {
    Strategy.MAX_ENDURANCE: "OPTION A - Maximum endurance",
    Strategy.MIN_FUEL: "OPTION B - Minimum fuel use",
    Strategy.MIN_LOGISTICS: "OPTION C - Minimum logistics burden",
    Strategy.MAX_SUPPORTED_FUNCTIONS: "OPTION D - Maximum supported functions",
}

#: The three configuration types CONFIGURE generates by default.
DEFAULT_STRATEGIES = (Strategy.MAX_ENDURANCE, Strategy.MIN_FUEL, Strategy.MIN_LOGISTICS)

#: LOGISTICS_BURDEN = fuel lifts (100 L each) + deployment points + active assets.
#: Stated here rather than buried in a weighting vector.
FUEL_LIFT_L = 100.0


def _relabel(label: str, letter: str) -> str:
    """Keep option letters contiguous even when a strategy subset is requested."""

    head, _, tail = label.partition(" - ")
    return f"OPTION {letter} - {tail}" if tail else label


@dataclass
class PlannedOption:
    """One ranked candidate: the configuration, what it delivers, and why."""

    strategy: Strategy
    configuration: Configuration
    metrics: ConfigurationMetrics
    simulation: SimulationResult
    feasible: bool = False
    caveats: list[str] = field(default_factory=list)
    score_components: dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.configuration.label

    def logistics_burden(self) -> float:
        return (
            self.metrics.fuel_consumption_l / FUEL_LIFT_L
            + self.metrics.deployment.points
            + self.metrics.number_of_active_assets
        )

    def to_dict(self, include_steps: bool = False) -> dict[str, Any]:
        return {
            "strategy": str(self.strategy),
            "intent": STRATEGY_INTENT[self.strategy],
            "configuration": self.configuration.to_dict(),
            "metrics": self.metrics.to_dict(),
            "feasible": self.feasible,
            "caveats": list(self.caveats),
            "logistics_burden": round(self.logistics_burden(), 2),
            "simulation": self.simulation.to_dict(include_steps=include_steps),
        }


@dataclass
class PlanningResult:
    """What the CONFIGURE step produces."""

    mission_id: str
    start_hour: float = 0.0
    end_hour: float = 0.0
    options: list[PlannedOption] = field(default_factory=list)
    candidates_evaluated: int = 0
    feasible_candidates: int = 0
    excluded_assets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discretionary_assessment: dict[str, Any] = field(default_factory=dict)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def option(self, configuration_id: str) -> PlannedOption:
        for option in self.options:
            if option.configuration.configuration_id == configuration_id:
                return option
        raise KeyError(f"no such configuration in this plan: {configuration_id!r}")

    def to_dict(self, include_steps: bool = False) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "candidates_evaluated": self.candidates_evaluated,
            "feasible_candidates": self.feasible_candidates,
            "excluded_assets": list(self.excluded_assets),
            "notes": list(self.notes),
            "discretionary_assessment": dict(self.discretionary_assessment),
            "options": [o.to_dict(include_steps=include_steps) for o in self.options],
            "data_labels": list(self.data_labels),
        }


class PlanningEngine:
    """Enumerate, simulate, rank."""

    #: Battery reserve floors offered to the search (fraction of nameplate energy).
    RESERVE_FLOORS = (0.15, 0.30, 0.45)

    def __init__(
        self,
        mission: MissionSpec,
        environment: Environment | None = None,
        inventory: AssetInventory | None = None,
    ) -> None:
        self.mission = mission
        self.environment = environment or mission.build_environment()
        self.inventory = inventory or mission.inventory
        self.simulator = Simulator(mission, self.environment, self.inventory)

    # -- candidate space ----------------------------------------------------

    def candidate_policies(
        self, start_hour: float, events: Sequence[FailureEvent]
    ) -> list[DispatchPolicy]:
        inv = self.inventory
        usable_gens = [
            g.asset_id
            for g in inv.generators
            if effective_state(g, start_hour, events) is not FailureState.UNAVAILABLE
        ]
        grid = inv.grid_connections[0] if inv.grid_connections else None
        pv = inv.pv_arrays[0] if inv.pv_arrays else None
        battery = inv.batteries[0] if inv.batteries else None

        grid_options = [True, False] if grid and effective_state(
            grid, start_hour, events
        ) is not FailureState.UNAVAILABLE else [False]
        pv_options = [True, False] if pv and effective_state(
            pv, start_hour, events
        ) is not FailureState.UNAVAILABLE else [False]
        battery_options = [True, False] if battery and effective_state(
            battery, start_hour, events
        ) is not FailureState.UNAVAILABLE else [False]

        gen_subsets: list[tuple[str, ...]] = []
        for size in range(len(usable_gens) + 1):
            gen_subsets.extend(itertools.combinations(usable_gens, size))

        policies: list[DispatchPolicy] = []
        for gens, use_grid, deploy_pv, use_battery, secondary, mode in itertools.product(
            gen_subsets,
            grid_options,
            pv_options,
            battery_options,
            list(SecondaryPolicy),
            list(GeneratorMode),
        ):
            if not gens and not use_grid:
                continue  # nothing can supply the node
            if not gens and mode is GeneratorMode.CONTINUOUS:
                continue  # generator mode is meaningless with no generator
            reserve_floors = self.RESERVE_FLOORS if use_battery else (0.0,)
            for reserve in reserve_floors:
                policies.append(
                    DispatchPolicy(
                        generator_ids=tuple(gens),
                        use_grid=use_grid,
                        deploy_pv=deploy_pv,
                        use_battery=use_battery,
                        battery_reserve_soc=reserve,
                        secondary_policy=secondary,
                        generator_mode=mode,
                    )
                )
        return policies

    def active_assets_for(self, policy: DispatchPolicy) -> tuple[str, ...]:
        """Which assets this policy requires to be deployed and connected."""

        inv = self.inventory
        active: list[str] = []
        active.extend(unit.asset_id for unit in inv.power_conversion)
        if policy.use_grid and inv.grid_connections:
            active.append(inv.grid_connections[0].asset_id)
        active.extend(policy.generator_ids)
        if policy.use_battery and inv.batteries:
            active.append(inv.batteries[0].asset_id)
        if policy.deploy_pv and inv.pv_arrays:
            active.append(inv.pv_arrays[0].asset_id)
        active.extend(load.asset_id for load in self.mission.critical_load_assets())
        active.extend(
            load.asset_id for load in self.mission.secondary_load_assets() if policy.attempts(load)
        )
        return tuple(active)

    # -- evaluation ---------------------------------------------------------

    def evaluate(
        self,
        policy: DispatchPolicy,
        *,
        configuration_id: str,
        start_hour: float = 0.0,
        end_hour: float | None = None,
        initial_state: NodeState | None = None,
        events: Sequence[FailureEvent] = (),
        label: str = "",
        strategy: str = "",
        intent: str = "",
        derived_from: str | None = None,
    ) -> tuple[Configuration, ConfigurationMetrics, SimulationResult]:
        configuration = Configuration(
            configuration_id=configuration_id,
            label=label,
            strategy=strategy,
            intent=intent,
            policy=policy,
            active_asset_ids=self.active_assets_for(policy),
            start_hour=start_hour,
            derived_from=derived_from,
        )
        result = self.simulator.run(
            configuration,
            start_hour=start_hour,
            end_hour=end_hour,
            initial_state=initial_state,
            events=events,
        )
        metrics = compute_metrics(self.mission, configuration, result, self.inventory)
        return configuration, metrics, result

    # -- ranking ------------------------------------------------------------

    @staticmethod
    def _rank_key(strategy: Strategy, metrics: ConfigurationMetrics) -> tuple:
        """Lexicographic ranking key per strategy. Larger is better."""

        if strategy is Strategy.MAX_ENDURANCE:
            # Endurance first; then the ability to survive the loss of the
            # largest generator, because endurance that one failure removes is
            # not endurance. Discretionary service is the last tie-break, so
            # this option will shed discretionary load to buy hours.
            return (
                metrics.endurance_hours,
                metrics.critical_load_coverage,
                round(metrics.n_minus_1_ride_through_h, 1),
                metrics.projected_endurance_hours,
                metrics.energy_reserve_hours_min,
                metrics.secondary_load_coverage,
                -metrics.fuel_consumption_l,
            )
        if strategy is Strategy.MIN_FUEL:
            return (
                -metrics.fuel_consumption_l,
                metrics.secondary_load_coverage,
                metrics.energy_reserve_hours_min,
                metrics.endurance_hours,
            )
        if strategy is Strategy.MAX_SUPPORTED_FUNCTIONS:
            return (
                metrics.secondary_load_coverage,
                metrics.energy_reserve_hours_min,
                -metrics.fuel_consumption_l,
                round(metrics.n_minus_1_ride_through_h, 1),
            )
        burden = (
            metrics.fuel_consumption_l / FUEL_LIFT_L
            + metrics.deployment.points
            + metrics.number_of_active_assets
        )
        return (
            -burden,
            metrics.energy_reserve_hours_min,
            metrics.secondary_load_coverage,
            -metrics.fuel_consumption_l,
        )

    @staticmethod
    def _score_components(strategy: Strategy, metrics: ConfigurationMetrics) -> dict[str, float]:
        return {
            "endurance_hours": round(metrics.endurance_hours, 2),
            "fuel_consumption_l": round(metrics.fuel_consumption_l, 1),
            "reserve_hours_min": round(metrics.energy_reserve_hours_min, 2),
            "secondary_coverage": round(metrics.secondary_load_coverage, 3),
            "n_minus_1_ride_through_h": round(metrics.n_minus_1_ride_through_h, 2),
            "logistics_burden": round(
                metrics.fuel_consumption_l / FUEL_LIFT_L
                + metrics.deployment.points
                + metrics.number_of_active_assets,
                2,
            ),
        }

    # -- the public entry point --------------------------------------------

    def generate_options(
        self,
        *,
        strategies: Iterable[Strategy] = DEFAULT_STRATEGIES,
        start_hour: float = 0.0,
        end_hour: float | None = None,
        initial_state: NodeState | None = None,
        events: Sequence[FailureEvent] = (),
        id_prefix: str = "CFG",
        derived_from: str | None = None,
    ) -> PlanningResult:
        mission = self.mission
        end = mission.mission_duration_h if end_hour is None else end_hour
        events = list(events)
        strategies = list(strategies)

        result = PlanningResult(
            mission_id=mission.mission_id,
            start_hour=start_hour,
            end_hour=end,
            excluded_assets=[
                asset.asset_id
                for asset in self.inventory
                if effective_state(asset, start_hour, events) is FailureState.UNAVAILABLE
            ],
        )

        evaluated: list[tuple[DispatchPolicy, Configuration, ConfigurationMetrics, SimulationResult]] = []
        for index, policy in enumerate(self.candidate_policies(start_hour, events)):
            configuration, metrics, sim = self.evaluate(
                policy,
                configuration_id=f"{id_prefix}-CAND-{index:03d}",
                start_hour=start_hour,
                end_hour=end,
                initial_state=initial_state,
                events=events,
                derived_from=derived_from,
            )
            evaluated.append((policy, configuration, metrics, sim))

        result.candidates_evaluated = len(evaluated)
        feasible = [item for item in evaluated if item[2].feasible]
        reserve_ok = [item for item in feasible if item[2].reserve_requirement_met]
        result.feasible_candidates = len(feasible)

        pool = reserve_ok or feasible or evaluated
        relaxations: list[str] = []
        if not feasible:
            relaxations.append(
                "No candidate configuration assures the critical loads for the whole "
                "horizon. Options below are the least-bad available and are NOT feasible "
                "against the mission requirement."
            )
        elif not reserve_ok:
            relaxations.append(
                f"No candidate holds the required {mission.minimum_reserve_hours:.0f} h "
                "energy reserve throughout. Options below meet critical-load assurance "
                "but breach the reserve requirement at some point."
            )
        result.notes.extend(relaxations)

        letters = "ABCDEFGH"
        for position, strategy in enumerate(strategies):
            best = max(pool, key=lambda item: self._rank_key(strategy, item[2]))
            policy, _, metrics, sim = best
            configuration_id = f"{id_prefix}-{letters[position]}"
            configuration, metrics, sim = self.evaluate(
                policy,
                configuration_id=configuration_id,
                start_hour=start_hour,
                end_hour=end,
                initial_state=initial_state,
                events=events,
                label=_relabel(STRATEGY_LABEL[strategy], letters[position]),
                strategy=str(strategy),
                intent=STRATEGY_INTENT[strategy],
                derived_from=derived_from,
            )
            caveats = list(relaxations)
            if not metrics.availability_met:
                caveats.append(
                    f"Critical-load coverage {metrics.critical_load_coverage:.1%} is below the "
                    f"required {mission.required_availability:.1%}."
                )
            if not metrics.completes_mission:
                caveats.append(
                    f"Critical load first unserved at H+{sim.first_critical_shortfall_hour:.0f} "
                    f"({metrics.endurance_limited_by})."
                )
            if not metrics.reserve_requirement_met:
                caveats.append(
                    f"Energy reserve falls to {metrics.energy_reserve_hours_min:.1f} h of critical "
                    f"load, below the required {mission.minimum_reserve_hours:.0f} h."
                )
            if not metrics.deployment.within_time_limit:
                caveats.append(
                    f"Setup critical path {metrics.deployment.setup_critical_path_min:.0f} min "
                    f"exceeds the {mission.deployment_time_limit_min:.0f} min deployment limit."
                )
            result.options.append(
                PlannedOption(
                    strategy=strategy,
                    configuration=configuration,
                    metrics=metrics,
                    simulation=sim,
                    feasible=metrics.feasible,
                    caveats=caveats,
                    score_components=self._score_components(strategy, metrics),
                )
            )

        result.discretionary_assessment = self._discretionary_assessment(pool, result.options)
        return result

    def _discretionary_assessment(
        self,
        pool: list,
        options: list[PlannedOption],
    ) -> dict[str, Any]:
        """Answer the question the three default options do not answer.

        Every objective the operator can state in terms of endurance, fuel or
        logistics sheds discretionary load, because serving it always costs
        energy. The operator's priorities nevertheless mention discretionary
        functions, so the engine reports separately whether they *could* be
        supported and what that would cost. It does not decide for them.
        """

        if not pool or not options:
            return {}
        best = max(pool, key=lambda item: self._rank_key(Strategy.MAX_SUPPORTED_FUNCTIONS, item[2]))
        best_metrics = best[2]
        baseline = options[0].metrics
        supported = [
            load.asset_id
            for load in self.mission.secondary_load_assets()
            if best[0].attempts(load)
        ]
        available = best_metrics.secondary_load_coverage > baseline.secondary_load_coverage + 1e-6
        if available:
            statement = (
                f"Discretionary functions {', '.join(supported)} could be supported "
                f"({best_metrics.secondary_load_coverage:.0%} secondary coverage) at a cost of "
                f"{best_metrics.fuel_consumption_l - baseline.fuel_consumption_l:+.0f} L of fuel and "
                f"{best_metrics.energy_reserve_hours_min - baseline.energy_reserve_hours_min:+.1f} h "
                f"of minimum energy reserve compared with {options[0].label}. "
                "Request OPTION D (maximum supported functions) if that trade is acceptable."
            )
        else:
            statement = (
                "No feasible configuration supports more discretionary load than the options "
                "shown. Within this fuel limit the secondary functions cannot be assured."
            )
        return {
            "available": available,
            "best_secondary_coverage": round(best_metrics.secondary_load_coverage, 4),
            "baseline_secondary_coverage": round(baseline.secondary_load_coverage, 4),
            "supportable_functions": supported,
            "fuel_delta_l": round(
                best_metrics.fuel_consumption_l - baseline.fuel_consumption_l, 1
            ),
            "reserve_delta_h": round(
                best_metrics.energy_reserve_hours_min - baseline.energy_reserve_hours_min, 2
            ),
            "endurance_delta_h": round(
                best_metrics.endurance_hours - baseline.endurance_hours, 2
            ),
            "baseline_option": options[0].label,
            "statement": statement,
        }
