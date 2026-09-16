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
from mission_machine.mission.spec import PriorityIntent
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
    preauthorised_degradations: list[str] = field(default_factory=list)

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
            "preauthorised_degradations": list(self.preauthorised_degradations),
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
    relies_on_preauthorised_degradation: bool = False
    degraded_loads: list[str] = field(default_factory=list)
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
            "relies_on_preauthorised_degradation": self.relies_on_preauthorised_degradation,
            "degraded_loads": list(self.degraded_loads),
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
        self._degraded_engine: PlanningEngine | None = None
        self._context: dict[str, Any] = {}

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
            load.asset_id
            for load in self.mission.secondary_load_assets()
            if policy.attempts(load, self.mission)
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
            # Resolved here, where the mission is in hand, so that the
            # description names the functions the operator actually asked for.
            description=tuple(policy.describe(self.mission)),
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

        self._context = {
            "start_hour": start_hour,
            "end_hour": end,
            "initial_state": initial_state,
            "events": events,
            "derived_from": derived_from,
        }
        evaluated = self._evaluate_all(f"{id_prefix}-CAND")

        result.candidates_evaluated = len(evaluated)
        feasible = [item for item in evaluated if item[2].feasible]
        result.feasible_candidates = len(feasible)

        pool, relaxations, degraded_note = self._select_pool(evaluated, feasible)
        if degraded_note:
            result.relies_on_preauthorised_degradation = True
            result.degraded_loads = list(mission.degradable_loads())
        result.notes.extend(relaxations)

        letters = "ABCDEFGH"
        for position, strategy in enumerate(strategies):
            best = max(pool, key=lambda item: self._rank_key(strategy, item[2]))
            policy, _, metrics, sim, owner = best
            configuration_id = f"{id_prefix}-{letters[position]}"
            configuration, metrics, sim = owner.evaluate(
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
            if metrics.unhonoured_priorities:
                caveats.append(
                    "Does not serve "
                    + ", ".join(metrics.unhonoured_priorities)
                    + ", which the operator asked for whenever it is affordable."
                )
            if degraded_note:
                caveats.append(
                    "Relies on the degraded mode the operator pre-authorised for "
                    + ", ".join(mission.degradable_loads())
                    + ". Confirm that authorisation still stands."
                )
            immobile = mission.immobile_assets_relied_on(configuration)
            if immobile:
                caveats.append(
                    "Relies on "
                    + ", ".join(immobile)
                    + f", which cannot displace inside the mission's "
                    f"{mission.mobility_requirement.max_displacement_time_min:.0f} min limit. "
                    "Confirm that the relocation requirement is lifted before choosing this."
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
                    preauthorised_degradations=(
                        list(mission.degradable_loads()) if degraded_note else []
                    ),
                )
            )

        # Asked of every feasible candidate, not only the ones the strategies
        # ranked over: the question is what the mission *could* support, and the
        # pool has already been narrowed to what the operator prioritised.
        result.discretionary_assessment = self._discretionary_assessment(
            feasible or evaluated, result.options
        )
        result.notes.extend(self._indistinguishable_note(result, strategies))
        return result

    #: The metrics an operator would use to tell two options apart, and the
    #: smallest difference on each that is worth calling a difference. Chosen,
    #: not derived, and registered as AS-025.
    _DISTINGUISHING: tuple[tuple[str, float], ...] = (
        ("fuel_consumption_l", 5.0),
        ("endurance_hours", 0.5),
        ("energy_reserve_hours_min", 0.5),
        ("secondary_load_coverage", 0.02),
        ("critical_load_coverage", 0.001),
        ("number_of_active_assets", 0.5),
        ("single_point_of_failure_count", 0.5),
        ("grid_dependence", 0.02),
    )

    def _indistinguishable_note(
        self, result: PlanningResult, strategies: Sequence[Strategy]
    ) -> list[str]:
        """Say when the options offered are the same option under three names.

        MM-DEMO-003 (RQ-001) is a mission whose binding constraint is heat
        rather than fuel. The three default strategies are shaped for a
        fuel-limited node, so on that mission they returned three options
        identical on every metric the mission cares about, while
        MAX_SUPPORTED_FUNCTIONS - already implemented, simply not in the
        default set - returned one that served every function for fuel the
        mission had spare. An operator reading three near-identical options has
        no way to know the fourth exists.

        This does not choose the strategy set for them. It says the set they
        used did not separate anything, and names what was not tried.
        """

        options = result.options
        if len(options) < 2:
            return []
        same: list[str] = []
        for first in range(len(options)):
            for second in range(first + 1, len(options)):
                if self._materially_same(options[first].metrics, options[second].metrics):
                    same.append(
                        f"{options[first].label} and {options[second].label}"
                    )
        if not same:
            return []

        note = (
            "These options are materially the same: "
            + "; ".join(same)
            + ". They differ by less than the smallest difference worth reporting on every "
            "metric, so the choice between them is not a trade-off."
        )
        untried = [s for s in Strategy if s not in set(strategies)]
        if untried:
            note += (
                " Not generated: "
                + ", ".join(s.value for s in untried)
                + ". On a mission whose binding constraint is not the one the default "
                "strategies are shaped around, one of those may be the option worth seeing."
            )
        return [note]

    def _materially_same(
        self, first: ConfigurationMetrics, second: ConfigurationMetrics
    ) -> bool:
        for name, tolerance in self._DISTINGUISHING:
            left = getattr(first, name, None)
            right = getattr(second, name, None)
            if left is None or right is None:
                continue
            if abs(float(left) - float(right)) > tolerance:
                return False
        return True

    def _select_pool(self, evaluated: list, feasible: list) -> tuple[list, list[str], bool]:
        """Choose which candidates the strategies rank over, and say what was given up.

        The ladder, best first:

        1. feasible, holds the reserve requirement, and honours every function the
           operator marked SERVE_IF_AFFORDABLE;
        2. feasible and honours the operator, but breaches the reserve;
        3. feasible and holds the reserve, but drops a prioritised function;
        4. feasible;
        5. nothing feasible - the least-bad candidates, clearly labelled.

        Dropping a rung is never silent. An option that sheds a function the
        operator asked for, when a feasible option could have served it, does not
        honour the operator - so honouring outranks the reserve requirement here,
        and the note says which rung the options came from.
        """

        mission = self.mission
        wanted = mission.serve_if_affordable_loads()

        def honours(item) -> bool:
            return not item[2].unhonoured_priorities

        def reserve_ok(item) -> bool:
            return item[2].reserve_requirement_met

        relaxations: list[str] = []

        if feasible:
            best = [item for item in feasible if honours(item) and reserve_ok(item)]
            if best:
                return best, relaxations, False
            honouring = [item for item in feasible if honours(item)]
            holding = [item for item in feasible if reserve_ok(item)]
            if honouring:
                relaxations.append(
                    f"No candidate both holds the required {mission.minimum_reserve_hours:.0f} h "
                    "energy reserve and serves every function the operator prioritised. Options "
                    "below serve the prioritised functions and breach the reserve requirement at "
                    "some point."
                )
                return honouring, relaxations, False
            if wanted:
                relaxations.append(
                    "No feasible candidate can serve "
                    + ", ".join(wanted)
                    + " (operator priority). Options below drop "
                    + ("it" if len(wanted) == 1 else "them")
                    + " to keep the critical functions assured."
                )
            if holding:
                return holding, relaxations, False
            relaxations.append(
                f"No candidate holds the required {mission.minimum_reserve_hours:.0f} h "
                "energy reserve throughout. Options below meet critical-load assurance "
                "but breach the reserve requirement at some point."
            )
            return feasible, relaxations, False

        # Nothing is feasible as the node stands. Before reporting failure, use
        # any degradation the operator pre-authorised in the MissionSpec - and
        # say so loudly. The authorisation is theirs; the planner is not
        # deciding to degrade anything.
        degraded = self._evaluate_degraded_candidates()
        degraded_feasible = [item for item in degraded if item[2].feasible]
        if degraded_feasible:
            relaxations.append(
                "No configuration of the equipment as it stands can assure the critical loads. "
                "Options below rely on a degraded mode the operator pre-authorised for "
                + ", ".join(mission.degradable_loads())
                + " (operator priority "
                + ", ".join(
                    str(p.rank)
                    for p in mission.operator_priorities
                    if p.intent is PriorityIntent.DEGRADE_ACCEPTABLE
                )
                + "). Confirm the degradation still stands before selecting one."
            )
            best = [item for item in degraded_feasible if honours(item)] or degraded_feasible
            return best, relaxations, True

        relaxations.append(
            "No candidate configuration assures the critical loads for the whole "
            "horizon. Options below are the least-bad available and are NOT feasible "
            "against the mission requirement."
        )
        return evaluated, relaxations, False

    def _evaluate_all(self, id_prefix: str) -> list:
        """Simulate every candidate policy, tagged with the engine that ran it."""

        context = self._context
        evaluated = []
        for index, policy in enumerate(
            self.candidate_policies(context["start_hour"], context["events"])
        ):
            configuration, metrics, sim = self.evaluate(
                policy,
                configuration_id=f"{id_prefix}-{index:03d}",
                start_hour=context["start_hour"],
                end_hour=context["end_hour"],
                initial_state=context["initial_state"],
                events=context["events"],
                derived_from=context["derived_from"],
            )
            evaluated.append((policy, configuration, metrics, sim, self))
        return evaluated

    def _evaluate_degraded_candidates(self) -> list:
        """Re-enumerate with the operator's pre-authorised degradations applied."""

        if not self.mission.degradable_loads():
            return []
        if self._degraded_engine is None:
            self._degraded_engine = PlanningEngine(
                self.mission, self.environment, self.mission.degraded_inventory()
            )
        self._degraded_engine._context = dict(self._context)
        return self._degraded_engine._evaluate_all(id_prefix="DEG")

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
        baseline_metrics = options[0].metrics
        supported = [
            load.asset_id
            for load in self.mission.secondary_load_assets()
            if best[2].load_coverage.get(load.asset_id, 0.0) >= 0.5
            and baseline_metrics.load_coverage.get(load.asset_id, 0.0) < 0.5
        ]
        available = (
            best_metrics.secondary_load_coverage > baseline.secondary_load_coverage + 1e-6
            and bool(supported)
        )
        if available:
            statement = (
                f"Discretionary functions {', '.join(supported)} could also be supported "
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
