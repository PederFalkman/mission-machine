"""OPERATE: running a selected configuration, and reconfiguring after a failure.

The rule this module enforces is the one in the pack brief: when something
changes, the system does not silently decide. It shows

    WHAT CHANGED - WHY IT MATTERS - AVAILABLE OPTIONS - TRADE-OFFS

and waits for the operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from mission_machine.assets.base import FailureState
from mission_machine.assets.inventory import AssetInventory
from mission_machine.environment.model import Environment
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.explain import (
    Recommendation,
    build_recommendation,
    comparison_table,
    trade_offs,
)
from mission_machine.mission.spec import MissionSpec
from mission_machine.operations.premises import (
    DEFAULT_THRESHOLDS,
    PremiseBreach,
    Thresholds,
    check_premises,
)
from mission_machine.planning.configuration import Configuration
from mission_machine.planning.engine import (
    DEFAULT_STRATEGIES,
    PlannedOption,
    PlanningEngine,
    PlanningResult,
    Strategy,
)
from mission_machine.planning.metrics import ConfigurationMetrics, compute_metrics
from mission_machine.resilience.analysis import ResilienceAnalyst
from mission_machine.resilience.dependencies import (
    DEFAULT_PROPAGATION_REGISTRY,
    PropagationResult,
    build_graph,
    states_from_events,
    states_from_step,
)
from mission_machine.resilience.failures import FailureEvent, Scenario
from mission_machine.simulation.simulator import SimulationResult, Simulator, effective_state
from mission_machine.simulation.state import NodeState


class OperationsError(RuntimeError):
    pass


@dataclass
class MissionAssessment:
    """The state of the mission as currently understood, at one point in time."""

    mission_id: str
    configuration_id: str
    at_hour: float
    status: str                      # ASSURED | AT_RISK | DEGRADED | FAILED
    endurance_remaining_h: float
    mission_remaining_h: float
    critical_functions_supported: list[str] = field(default_factory=list)
    functions_degraded: list[str] = field(default_factory=list)
    functions_shed: list[str] = field(default_factory=list)
    reserve_hours: float = 0.0
    reserve_requirement_hours: float = 0.0
    fuel_remaining_l: float = 0.0
    battery_soc: float = 0.0
    unavailable_assets: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    metrics: ConfigurationMetrics | None = None
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "configuration_id": self.configuration_id,
            "at_hour": self.at_hour,
            "status": self.status,
            "endurance_remaining_h": round(self.endurance_remaining_h, 2),
            "mission_remaining_h": round(self.mission_remaining_h, 2),
            "critical_functions_supported": list(self.critical_functions_supported),
            "functions_degraded": list(self.functions_degraded),
            "functions_shed": list(self.functions_shed),
            "reserve_hours": round(self.reserve_hours, 2),
            "reserve_requirement_hours": self.reserve_requirement_hours,
            "fuel_remaining_l": round(self.fuel_remaining_l, 1),
            "battery_soc": round(self.battery_soc, 4),
            "unavailable_assets": list(self.unavailable_assets),
            "alerts": list(self.alerts),
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "data_labels": list(self.data_labels),
        }


@dataclass
class ReconfigurationReport:
    """What the operator is shown after a disruption."""

    mission_id: str
    event_hour: float
    events: list[FailureEvent] = field(default_factory=list)
    what_changed: list[str] = field(default_factory=list)
    why_it_matters: list[str] = field(default_factory=list)
    assessment_before: MissionAssessment | None = None
    assessment_after: MissionAssessment | None = None
    options: PlanningResult | None = None
    comparison: list[dict[str, Any]] = field(default_factory=list)
    trade_offs: list[dict[str, Any]] = field(default_factory=list)
    recommendation: Recommendation | None = None
    recovery_options: list[dict[str, Any]] = field(default_factory=list)
    propagation: PropagationResult | None = None
    mechanism: list[str] = field(default_factory=list)
    """What the affected functions are short of, and why (RQ-005).

    ``why_it_matters`` says what has been lost. This says what it was lost
    *to* - the difference between "COMMS-01 is at risk" and "COMMS-01 is short
    of the cooling it needs because ECS-MIN-01 is at 60 % of its service".
    """
    operator_decision_required: bool = True
    decision_prompt: str = (
        "Operator decision required. Continue with the current configuration, or select a "
        "reconfiguration option."
    )
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def to_dict(self, include_steps: bool = False) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "event_hour": self.event_hour,
            "events": [e.to_dict() for e in self.events],
            "what_changed": list(self.what_changed),
            "why_it_matters": list(self.why_it_matters),
            "assessment_before": self.assessment_before.to_dict() if self.assessment_before else None,
            "assessment_after": self.assessment_after.to_dict() if self.assessment_after else None,
            "options": self.options.to_dict(include_steps=include_steps) if self.options else None,
            "comparison": list(self.comparison),
            "trade_offs": list(self.trade_offs),
            "recommendation": self.recommendation.to_dict() if self.recommendation else None,
            "recovery_options": list(self.recovery_options),
            "propagation": self.propagation.to_dict() if self.propagation else None,
            "mechanism": list(self.mechanism),
            "operator_decision_required": self.operator_decision_required,
            "decision_prompt": self.decision_prompt,
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


#: What the panel is doing about one contradicted premise, and the reason the
#: distinction exists: RQ-018 found the same true contradiction raised 71 times
#: in one mission - once an hour, every hour, carrying no new information after
#: the first. A contradiction that is still true is not news the second time.
RAISED = "RAISED"        # new, or newly worse: worth interrupting somebody for
STANDING = "STANDING"    # still true, already told, nothing further crossed
NOTED = "NOTED"          # contradicted, but it crosses no line the mission states
RESOLVED = "RESOLVED"    # was raised, and the world has come back to the premise


@dataclass
class StandingAlarm:
    """What the operator has already been told about one premise, and when.

    The record is what makes a second alarm distinguishable from a second
    hour of the first one, and what lets a handover carry "we know, and we
    decided to keep planning on the stated premise" across a shift boundary.
    """

    key: str
    first_raised_hour: float
    last_raised_hour: float
    crossed: tuple[str, ...] = ()
    times_raised: int = 1
    dismissed_at_hour: float | None = None
    dismissed_rationale: str = ""
    resolved_at_hour: float | None = None
    resolution_reason: str = ""

    @property
    def dismissed(self) -> bool:
        return self.dismissed_at_hour is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "first_raised_hour": self.first_raised_hour,
            "last_raised_hour": self.last_raised_hour,
            "crossed": list(self.crossed),
            "times_raised": self.times_raised,
            "dismissed": self.dismissed,
            "dismissed_at_hour": self.dismissed_at_hour,
            "dismissed_rationale": self.dismissed_rationale,
            "resolved_at_hour": self.resolved_at_hour,
            "resolution_reason": self.resolution_reason,
        }


@dataclass
class PremiseConsequence:
    """One contradicted premise, and what planning against the truth would mean."""

    breach: "PremiseBreach"
    assessment_on_stated_premise: MissionAssessment | None = None
    assessment_on_revised_premise: MissionAssessment | None = None
    matters_because: list[str] = field(default_factory=list)
    state: str = RAISED
    crossed: tuple[str, ...] = ()
    alarm: "StandingAlarm | None" = None
    material: bool = True
    """Does planning on the revision change the operator's picture at all?

    A premise can be contradicted beyond argument and still not be worth
    interrupting anybody about. RQ-017 measured the weather-yield detector
    firing on a sky that was genuinely half as bright as forecast and changing
    nothing the operator would act on. Those are reported as notes rather than
    raised as alarms - demoted, not hidden, because the operator can still
    choose to plan on them.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "breach": self.breach.to_dict(),
            "material": self.material,
            "state": self.state,
            "crossed": list(self.crossed),
            "alarm": self.alarm.to_dict() if self.alarm else None,
            "assessment_on_stated_premise": (
                self.assessment_on_stated_premise.to_dict()
                if self.assessment_on_stated_premise
                else None
            ),
            "assessment_on_revised_premise": (
                self.assessment_on_revised_premise.to_dict()
                if self.assessment_on_revised_premise
                else None
            ),
            "matters_because": list(self.matters_because),
        }


@dataclass
class PremiseReport:
    """What the operator is shown when the world has left the plan's assumptions."""

    mission_id: str
    at_hour: float
    consequences: list[PremiseConsequence] = field(default_factory=list)
    resolved: list["StandingAlarm"] = field(default_factory=list)
    operator_decision_required: bool = True
    decision_prompt: str = (
        "The node's own observations contradict a premise the plan rests on. Decide which "
        "premise to plan against. The machine will not revise a mission assumption by itself."
    )
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def raised(self) -> list[PremiseConsequence]:
        """New, or newly worse. The only kind that interrupts anybody."""

        return [c for c in self.consequences if c.state == RAISED]

    @property
    def standing(self) -> list[PremiseConsequence]:
        """Still true, already told. Shown, never re-announced."""

        return [c for c in self.consequences if c.state == STANDING]

    @property
    def noted(self) -> list[PremiseConsequence]:
        """Contradicted, but planning on the revision changes nothing."""

        return [c for c in self.consequences if c.state == NOTED]

    @property
    def clear(self) -> bool:
        """Is there anything new to interrupt the operator with?

        Deliberately not "is everything fine": a standing contradiction the
        operator has already been shown leaves the report clear, because
        telling them again is not information. It is still in ``standing``,
        still on the panel, and still in the handover.
        """

        return not self.raised

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "at_hour": self.at_hour,
            "clear": self.clear,
            "consequences": [c.to_dict() for c in self.consequences],
            "raised": [c.to_dict() for c in self.raised],
            "standing": [c.to_dict() for c in self.standing],
            "noted": [c.to_dict() for c in self.noted],
            "resolved": [alarm.to_dict() for alarm in self.resolved],
            "operator_decision_required": self.operator_decision_required,
            "decision_prompt": self.decision_prompt,
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


@dataclass
class HandoverBrief:
    """What one shift hands the next. Assembled from the record, not authored."""

    mission_id: str
    at_hour: float
    outgoing: str = ""
    incoming: str = ""
    assessment: "MissionAssessment | None" = None
    standing: list["StandingAlarm"] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    decisions: list["OperatorDecision"] = field(default_factory=list)
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def open_decisions(self) -> list["StandingAlarm"]:
        """Standing alarms nobody has decided about. The incoming shift's list."""

        return [alarm for alarm in self.standing if not alarm.dismissed]

    @property
    def carried_decisions(self) -> list["StandingAlarm"]:
        """Standing alarms the outgoing shift decided to leave, and why."""

        return [alarm for alarm in self.standing if alarm.dismissed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "at_hour": self.at_hour,
            "outgoing": self.outgoing,
            "incoming": self.incoming,
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "standing": [alarm.to_dict() for alarm in self.standing],
            "open_decisions": [alarm.to_dict() for alarm in self.open_decisions],
            "carried_decisions": [alarm.to_dict() for alarm in self.carried_decisions],
            "revisions": [dict(revision) for revision in self.revisions],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


@dataclass
class OperatorDecision:
    """A record of what the human chose, and when. Kept for the evidence trail."""

    at_hour: float
    decision: str
    configuration_id: str
    rationale: str = ""
    recommended_configuration_id: str = ""
    followed_recommendation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_hour": self.at_hour,
            "decision": self.decision,
            "configuration_id": self.configuration_id,
            "rationale": self.rationale,
            "recommended_configuration_id": self.recommended_configuration_id,
            "followed_recommendation": self.followed_recommendation,
        }


class OperationsSession:
    """Holds the operating picture: selected configuration, elapsed time, events."""

    def __init__(
        self,
        mission: MissionSpec,
        engine: PlanningEngine | None = None,
        realised_environment: Environment | None = None,
        realised_inventory: AssetInventory | None = None,
    ) -> None:
        """Hold the operating picture.

        ``realised_environment`` and ``realised_inventory`` describe the world
        the node actually lives in, which need not be the one the mission
        asserts - the supply may not come back, or the functions may draw more
        than the profiles say. Planning and projection run on
        the mission's premise - that is what the operator believes - while
        ``run_to`` advances through the world that is really there. Where the
        two disagree, :meth:`check_premises` is what notices.
        """

        self.mission = mission
        self.engine = engine or PlanningEngine(mission)
        self.analyst = ResilienceAnalyst(self.engine)
        self.planned_environment = self.engine.environment
        self.realised_environment = realised_environment or self.engine.environment
        self.realised_inventory = realised_inventory or self.engine.inventory
        self.simulator = Simulator(mission, self.realised_environment, self.realised_inventory)
        self.projector = Simulator(mission, self.planned_environment, self.engine.inventory)
        self.observed: list = []
        self.premise_revisions: list = []
        self.premise_alarms: dict[str, StandingAlarm] = {}
        self.plan: PlanningResult | None = None
        self.selected: PlannedOption | None = None
        self.events: list[FailureEvent] = []
        self.current_hour: float = 0.0
        self.state: NodeState = self.simulator.initial_state()
        self.decisions: list[OperatorDecision] = []

    # -- planning -----------------------------------------------------------

    def generate_options(
        self, strategies: Sequence[Strategy] = DEFAULT_STRATEGIES, *, with_resilience: bool = True
    ) -> PlanningResult:
        plan = self.engine.generate_options(
            strategies=strategies,
            start_hour=self.current_hour,
            initial_state=self.state if self.current_hour > 0 else None,
            events=self.events,
            id_prefix=f"CFG-H{int(self.current_hour)}" if self.current_hour else "CFG",
        )
        if with_resilience:
            self.attach_resilience(plan)
        self.plan = plan
        return plan

    def attach_resilience(self, plan: PlanningResult) -> None:
        """Fill in the SINGLE_POINTS_OF_FAILURE and RECOVERY_OPTIONS metrics."""

        for option in plan.options:
            impacts = self.analyst.single_points_of_failure(
                option.configuration,
                start_hour=plan.start_hour,
                end_hour=plan.end_hour,
                initial_state=self.state if plan.start_hour > 0 else None,
                events=self.events,
                baseline=option.metrics,
            )
            option.metrics.single_points_of_failure = [
                impact.to_dict() for impact in impacts if impact.is_single_point_of_failure
            ]
            option.metrics.recovery_options = [
                recovery.to_dict()
                for recovery in self.analyst.recovery_options(
                    option.configuration,
                    start_hour=plan.start_hour,
                    end_hour=plan.end_hour,
                    initial_state=self.state if plan.start_hour > 0 else None,
                    events=self.events,
                    baseline=option.metrics,
                )
            ]

    def select(self, configuration_id: str, rationale: str = "", recommended: str = "") -> PlannedOption:
        if self.plan is None:
            raise OperationsError("no plan has been generated yet")
        option = self.plan.option(configuration_id)
        self.selected = option
        self.decisions.append(
            OperatorDecision(
                at_hour=self.current_hour,
                decision="SELECT_CONFIGURATION",
                configuration_id=configuration_id,
                rationale=rationale,
                recommended_configuration_id=recommended,
                followed_recommendation=(not recommended) or recommended == configuration_id,
            )
        )
        return option

    # -- running ------------------------------------------------------------

    def run_to(self, hour: float) -> SimulationResult:
        """Advance the node to ``hour`` under the selected configuration."""

        if self.selected is None:
            raise OperationsError("no configuration has been selected")
        if hour < self.current_hour:
            raise OperationsError("cannot run backwards")
        result = self.simulator.run(
            self.selected.configuration,
            start_hour=self.current_hour,
            end_hour=hour,
            initial_state=self.state,
            events=self.events,
        )
        self.state = result.final_state.copy()
        self.current_hour = hour
        self.observed.extend(result.steps)
        return result

    def project(
        self,
        configuration: Configuration | None = None,
        *,
        events: Sequence[FailureEvent] | None = None,
        environment: Environment | None = None,
        inventory: AssetInventory | None = None,
    ) -> tuple[SimulationResult, ConfigurationMetrics]:
        """Simulate from now to the end of the mission without advancing time.

        On the mission's own premise by default - projecting on the realised
        world would hand the operator knowledge of a future they do not have.
        Pass ``environment`` or ``inventory`` to project on a revised premise.
        """

        configuration = configuration or (self.selected.configuration if self.selected else None)
        if configuration is None:
            raise OperationsError("no configuration has been selected")
        projector = (
            Simulator(self.mission, environment or self.planned_environment,
                      inventory or self.engine.inventory)
            if environment is not None or inventory is not None
            else self.projector
        )
        result = projector.run(
            configuration,
            start_hour=self.current_hour,
            end_hour=self.mission.mission_duration_h,
            initial_state=self.state,
            events=list(self.events if events is None else events),
        )
        metrics = compute_metrics(
            self.mission, configuration, result, inventory or self.engine.inventory
        )
        return result, metrics

    # -- assessment ---------------------------------------------------------

    def assess(
        self,
        *,
        configuration: Configuration | None = None,
        events: Sequence[FailureEvent] | None = None,
        environment: Environment | None = None,
        inventory: AssetInventory | None = None,
    ) -> MissionAssessment:
        configuration = configuration or (self.selected.configuration if self.selected else None)
        if configuration is None:
            raise OperationsError("no configuration has been selected")
        active_events = list(self.events if events is None else events)
        result, metrics = self.project(
            configuration, events=active_events, environment=environment, inventory=inventory
        )
        metrics.single_points_of_failure = [
            impact.to_dict()
            for impact in self.analyst.single_points_of_failure(
                configuration,
                start_hour=self.current_hour,
                end_hour=self.mission.mission_duration_h,
                initial_state=self.state,
                events=active_events,
                baseline=metrics,
            )
            if impact.is_single_point_of_failure
        ]

        supported: list[str] = []
        degraded: list[str] = []
        for load in self.mission.critical_load_assets():
            unserved = any(
                step.load_demand_kw.get(load.asset_id, 0.0)
                - step.load_served_kw.get(load.asset_id, 0.0)
                > 1e-3
                for step in result.steps
            )
            (degraded if unserved else supported).append(load.asset_id)

        shed = [
            load.asset_id
            for load in self.mission.secondary_load_assets()
            if not configuration.policy.attempts(load, self.mission)
            or load.asset_id in metrics.shed_load_ids
        ]

        unavailable = [
            asset.asset_id
            for asset in self.engine.inventory
            if any(
                event.asset_id == asset.asset_id
                and event.active_at(self.current_hour)
                and event.state is FailureState.UNAVAILABLE
                for event in active_events
            )
        ]

        remaining = self.mission.mission_duration_h - self.current_hour
        if degraded:
            status = "FAILED" if metrics.critical_load_coverage < 0.5 else "DEGRADED"
        elif not metrics.reserve_requirement_met or metrics.endurance_hours < remaining:
            status = "AT_RISK"
        else:
            status = "ASSURED"

        alerts: list[str] = []
        if unavailable:
            alerts.append(f"Assets unavailable: {', '.join(unavailable)}.")
        if degraded:
            alerts.append(
                f"Critical functions that cannot be fully supported: {', '.join(degraded)}."
            )
        if not metrics.reserve_requirement_met:
            alerts.append(
                f"Energy reserve falls to {metrics.energy_reserve_hours_min:.1f} h against a "
                f"{self.mission.minimum_reserve_hours:.0f} h requirement."
            )
        if metrics.endurance_hours < remaining:
            alerts.append(
                f"Assured support ends {remaining - metrics.endurance_hours:.0f} h before the end "
                "of the mission."
            )
        if metrics.single_points_of_failure:
            alerts.append(
                "Single points of failure: "
                + ", ".join(spof["asset_id"] for spof in metrics.single_points_of_failure)
                + "."
            )

        return MissionAssessment(
            mission_id=self.mission.mission_id,
            configuration_id=configuration.configuration_id,
            at_hour=self.current_hour,
            status=status,
            endurance_remaining_h=metrics.endurance_hours,
            mission_remaining_h=remaining,
            critical_functions_supported=supported,
            functions_degraded=degraded,
            functions_shed=shed,
            reserve_hours=metrics.energy_reserve_hours_min,
            reserve_requirement_hours=self.mission.minimum_reserve_hours,
            fuel_remaining_l=self.state.fuel_remaining_l,
            battery_soc=self.state.battery_soc,
            unavailable_assets=unavailable,
            alerts=alerts,
            metrics=metrics,
        )

    # -- premises -----------------------------------------------------------

    def check_premises(self, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> PremiseReport:
        """Has the world left the assumptions the plan is still working from?

        Compares only what the node has already observed against what the
        mission said would happen. Where the two have parted company, the
        mission is re-projected on the revised premise so that the operator can
        see what accepting it would mean - and then the machine stops, because
        revising a mission assumption is their decision.
        """

        report = PremiseReport(mission_id=self.mission.mission_id, at_hour=self.current_hour)
        if self.selected is None or not self.observed:
            return report

        still_matters: set[str] = set()
        for breach in check_premises(
            self.mission, self.planned_environment, self.observed, self.current_hour, thresholds
        ):
            if breach.premise.key in {r["key"] for r in self.premise_revisions}:
                continue
            consequence = PremiseConsequence(breach=breach)
            consequence.assessment_on_stated_premise = self.assess()
            if breach.has_revision:
                consequence.assessment_on_revised_premise = self.assess(
                    environment=breach.revised_environment,
                    inventory=breach.revised_inventory,
                )
            consequence.matters_because, consequence.crossed = self._premise_matters(consequence)
            consequence.material = bool(consequence.crossed)
            if consequence.material:
                still_matters.add(breach.premise.key)
            consequence.state = self._alarm_state(consequence)
            consequence.alarm = self.premise_alarms.get(breach.premise.key)
            report.consequences.append(consequence)

        report.resolved = self._resolve_alarms(still_matters)
        return report

    def _alarm_state(self, consequence: PremiseConsequence) -> str:
        """Is this news, or the same news again?

        The rule mirrors the one that decides whether to raise at all: a
        contradiction is raised when it crosses a line the mission states, and
        raised *again* only when it crosses a line it had not crossed before.
        Anything else is standing - still true, still on the panel, not
        announced a second time.

        Deliberately idempotent within an hour: asking twice at the same hour
        gives the same answer, so refreshing a screen is not an event and does
        not quietly turn an alarm into old news.
        """

        key = consequence.breach.premise.key
        crossed = consequence.crossed
        if not crossed:
            return NOTED

        alarm = self.premise_alarms.get(key)
        if alarm is None or alarm.resolved_at_hour is not None:
            self.premise_alarms[key] = StandingAlarm(
                key=key,
                first_raised_hour=self.current_hour,
                last_raised_hour=self.current_hour,
                crossed=crossed,
            )
            return RAISED
        if alarm.last_raised_hour == self.current_hour:
            return RAISED
        if set(crossed) - set(alarm.crossed):
            alarm.crossed = tuple(sorted(set(alarm.crossed) | set(crossed)))
            alarm.last_raised_hour = self.current_hour
            alarm.times_raised += 1
            return RAISED
        return STANDING

    def _resolve_alarms(self, still_matters: set[str]) -> list[StandingAlarm]:
        """Alarms that have stopped mattering, reported once, with which reason.

        An alarm that simply stops appearing is its own kind of dishonesty: the
        operator cannot tell "the world came back to the premise" from "the
        window it was about has passed, and the plan is wrong about nothing
        that is still ahead". RQ-018 found the grid alarm going quiet at H+44
        for the second reason, with no word to anybody. Both are resolutions,
        they mean different things, and the difference is stated.
        """

        resolved: list[StandingAlarm] = []
        contradicted = {
            breach.premise.key
            for breach in check_premises(
                self.mission, self.planned_environment, self.observed, self.current_hour
            )
        }
        for key, alarm in self.premise_alarms.items():
            if key in still_matters:
                continue
            if alarm.resolved_at_hour is None:
                alarm.resolved_at_hour = self.current_hour
                alarm.resolution_reason = (
                    "The contradiction stands, but it no longer crosses any line the mission "
                    "states - what it was about is behind the node now."
                    if key in contradicted
                    else "What the node observes agrees with the mission's premise again."
                )
            if alarm.resolved_at_hour == self.current_hour:
                resolved.append(alarm)
        return resolved

    def dismiss_premise_revision(self, key: str, rationale: str = "") -> StandingAlarm:
        """Record that an operator saw this and is keeping the stated premise.

        Not a mute button. The alarm stays standing and stays on the panel; if
        it later crosses a line it had not crossed, it is raised again. What
        this adds is a record, because "the outgoing shift knew and decided to
        wait" is evidence, and its absence is what makes a handover guesswork.
        """

        alarm = self.premise_alarms.get(key)
        if alarm is None:
            raise OperationsError(f"no alarm has been raised for premise {key!r}")
        alarm.dismissed_at_hour = self.current_hour
        alarm.dismissed_rationale = rationale
        self.decisions.append(
            OperatorDecision(
                at_hour=self.current_hour,
                decision="DISMISS_PREMISE_REVISION",
                configuration_id=(
                    self.selected.configuration.configuration_id if self.selected else ""
                ),
                rationale=rationale or f"Keeping the stated premise for {key}.",
            )
        )
        return alarm

    def _premise_matters(
        self, consequence: PremiseConsequence
    ) -> tuple[list[str], tuple[str, ...]]:
        """What accepting the revision would change, and whether it changes anything.

        The second half of the answer is what stops the panel crying wolf, and
        the rule it uses is the one thing that keeps it honest: a difference is
        *raised* when it crosses a line the mission itself states - the mission
        status changes, a critical function is no longer safe, assured support
        no longer covers what is left, or a stated requirement goes into
        breach. A difference that merely moves a number is reported as a note.

        RQ-017 is where that rule comes from. The weather-yield detector fired
        on a sky genuinely half as bright as forecast and moved the projected
        reserve from 14.0 h to 12.4 h against an 8 h requirement: real,
        correct, and nothing an operator would do anything about. Raising it
        spends the attention that the next alarm needs.

        Two limits, both measured and both at RQ-017. The rule trusts the
        revision the machine itself offered, so a revision that understates the
        change can quieten an alarm that mattered. And it cannot help with a
        contradiction that is real on everything observed so far and turns out
        transient - at the hour of the alarm, that one is indistinguishable
        from the real thing.
        """

        stated = consequence.assessment_on_stated_premise
        revised = consequence.assessment_on_revised_premise
        if stated is None or revised is None:
            return (
                [
                    "The premise is contradicted, but the planner has no revised world to "
                    "project against, so the consequence is not quantified."
                ],
                ("unquantified",),
            )
        lines: list[str] = []
        if revised.status != stated.status:
            lines.append(
                f"The mission reads {stated.status} on the premise as stated and "
                f"{revised.status} on what the node has actually seen."
            )
        endurance = revised.endurance_remaining_h - stated.endurance_remaining_h
        if abs(endurance) > 0.5:
            lines.append(
                f"Assured support from here is {endurance:+.0f} h against the projection the "
                f"plan is working from ({revised.endurance_remaining_h:.0f} h of "
                f"{revised.mission_remaining_h:.0f} h remaining)."
            )
        reserve = revised.reserve_hours - stated.reserve_hours
        if abs(reserve) > 0.5:
            lines.append(f"Minimum energy reserve is {reserve:+.1f} h lower than projected.")
        newly = [
            load_id
            for load_id in revised.functions_degraded
            if load_id not in stated.functions_degraded
        ]
        if newly:
            lines.append(
                f"Critical functions that the stated premise hides as safe: {', '.join(newly)}."
            )
        if not lines:
            return (
                [
                    "Accepting the revised premise does not change the mission picture. The "
                    "premise is wrong, and on this configuration it does not yet matter."
                ],
                (),
            )

        # Which stated lines it crosses, not merely that it crosses one. The
        # set is what distinguishes a contradiction that has got worse - and is
        # worth saying again - from the same one an hour later (RQ-018).
        requirement = revised.reserve_requirement_hours
        crossed: list[str] = []
        if revised.status != stated.status:
            crossed.append(f"status:{revised.status}")
        for load_id in newly:
            crossed.append(f"critical:{load_id}")
        if endurance < -0.5 and revised.endurance_remaining_h < revised.mission_remaining_h:
            crossed.append("assured-support")
        if reserve < -0.5 and revised.reserve_hours < requirement:
            crossed.append("reserve-requirement")

        if not crossed:
            lines.append(
                "None of that crosses a line the mission states - the status, the critical "
                "functions, the assured support and the reserve requirement all hold on the "
                "revised premise - so it is noted rather than raised."
            )
        return lines, tuple(crossed)

    def handover(self, outgoing: str = "", incoming: str = "") -> "HandoverBrief":
        """What the incoming shift needs to know that the screen does not say.

        A premise alarm is not an event, it is a state, and a 72-hour rotation
        has two or three shift boundaries in it. The screen shows the state; it
        does not show that the outgoing shift was told at H+33, decided to wait,
        and wrote down why. Without that, the incoming shift either re-decides
        from scratch or assumes somebody must have handled it.

        The brief is assembled, not authored: every line is a record the
        session already holds. The machine does not advise the incoming shift
        what to do about any of it.
        """

        brief = HandoverBrief(
            mission_id=self.mission.mission_id,
            at_hour=self.current_hour,
            outgoing=outgoing,
            incoming=incoming,
            assessment=self.assess() if self.selected else None,
            standing=[
                alarm for alarm in self.premise_alarms.values()
                if alarm.resolved_at_hour is None
            ],
            revisions=[dict(revision) for revision in self.premise_revisions],
            decisions=list(self.decisions),
        )
        self.decisions.append(
            OperatorDecision(
                at_hour=self.current_hour,
                decision="HANDOVER",
                configuration_id=(
                    self.selected.configuration.configuration_id if self.selected else ""
                ),
                rationale=(
                    f"{outgoing or 'outgoing shift'} to {incoming or 'incoming shift'}: "
                    f"{len(brief.standing)} standing premise alarm(s) carried across."
                ),
            )
        )
        return brief

    def accept_premise_revision(
        self, key: str, rationale: str = "", thresholds: Thresholds = DEFAULT_THRESHOLDS
    ) -> PremiseReport:
        """Adopt a revised premise for planning. Only an operator may call this."""

        report = self.check_premises(thresholds)
        for consequence in report.consequences:
            breach = consequence.breach
            if breach.premise.key != key:
                continue
            if breach.revised_environment is not None:
                self.planned_environment = breach.revised_environment
                self.engine.environment = breach.revised_environment
                self.engine.simulator = Simulator(
                    self.mission, breach.revised_environment, self.engine.inventory
                )
            if breach.revised_inventory is not None:
                self.engine.inventory = breach.revised_inventory
                self.engine.simulator = Simulator(
                    self.mission, self.planned_environment, breach.revised_inventory
                )
            self.projector = Simulator(
                self.mission, self.planned_environment, self.engine.inventory
            )
            self.analyst = ResilienceAnalyst(self.engine)
            self.premise_revisions.append(
                {"key": key, "at_hour": self.current_hour, "statement": breach.revision_statement}
            )
            self.decisions.append(
                OperatorDecision(
                    at_hour=self.current_hour,
                    decision="ACCEPT_PREMISE_REVISION",
                    configuration_id=(
                        self.selected.configuration.configuration_id if self.selected else ""
                    ),
                    rationale=rationale or breach.revision_statement,
                )
            )
            return self.check_premises(thresholds)
        raise OperationsError(f"no contradicted premise with key {key!r}")

    # -- disruption ---------------------------------------------------------

    def inject(
        self,
        events: Sequence[FailureEvent] | Scenario,
        *,
        advance_to_event: bool = True,
        regenerate: bool = True,
    ) -> ReconfigurationReport:
        """Apply a disruption and produce the operator's reconfiguration picture."""

        if self.selected is None:
            raise OperationsError("no configuration has been selected")
        scenario_events = list(events.events) if isinstance(events, Scenario) else list(events)
        if not scenario_events:
            raise OperationsError("no failure events supplied")
        event_hour = min(event.hour for event in scenario_events)

        if advance_to_event and event_hour > self.current_hour:
            self.run_to(event_hour)

        before = self.assess()
        self.events.extend(scenario_events)
        after = self.assess()

        report = ReconfigurationReport(
            mission_id=self.mission.mission_id,
            event_hour=event_hour,
            events=scenario_events,
            assessment_before=before,
            assessment_after=after,
        )
        report.what_changed = self._what_changed(scenario_events, before, after)
        report.why_it_matters = self._why_it_matters(before, after)
        report.propagation = self.propagate_consequences()
        report.mechanism = self._mechanism(report.propagation)

        # Recovery actions available on the *current* configuration, before any
        # decision to reconfigure.
        report.recovery_options = [
            option.to_dict()
            for option in self.analyst.recovery_options(
                self.selected.configuration,
                start_hour=self.current_hour,
                end_hour=self.mission.mission_duration_h,
                initial_state=self.state,
                events=self.events,
                baseline=after.metrics,
            )
        ]

        if regenerate:
            plan = self.generate_options()
            report.options = plan
            report.comparison = comparison_table(plan.options)
            if plan.options:
                report.recommendation = build_recommendation(plan, self.engine)
                best = plan.option(report.recommendation.recommended_configuration_id)
                report.trade_offs = [
                    t.to_dict()
                    for t in trade_offs(best, [o for o in plan.options if o is not best])
                ]
        return report

    # -- consequence propagation (RQ-005) -----------------------------------

    def propagate_consequences(self, at_hour: float | None = None) -> PropagationResult:
        """What each function is short of, and why, on the current picture.

        Reads the operating states off the hour the node is actually in rather
        than off a hand-written scenario, so the chain explains a run.
        """

        graph = build_graph(self.mission, self.engine.inventory)
        unavailable = [
            asset.asset_id
            for asset in self.engine.inventory
            if effective_state(asset, self.current_hour, self.events)
            is FailureState.UNAVAILABLE
        ]
        step, projected = self._worst_hour(at_hour)
        states = (
            states_from_step(self.engine.inventory, step, unavailable)
            if step is not None
            else states_from_events(self.engine.inventory, unavailable)
        )
        result = DEFAULT_PROPAGATION_REGISTRY.propagate(graph, states)
        result.at_hour = step.hour if step is not None else self.current_hour
        result.projected = projected
        return result

    def _worst_hour(self, at_hour: float | None) -> tuple[Any, bool]:
        """The hour worth explaining, which is rarely the hour you are in.

        At the moment a generator fails nothing is short yet, so propagating on
        the present says "nothing unmet" and is useless. The operator's question
        is what will be short and of what, so the hour chosen is the worst one
        in the projection from here - and the result says it is a projection.
        """

        if at_hour is not None:
            pool = [s for s in self.observed if s.hour <= at_hour]
            return (max(pool, key=lambda s: s.hour) if pool else None), False
        if self.selected is None:
            return None, False
        try:
            result, _ = self.project()
        except OperationsError:
            return None, False
        steps = list(result.steps)
        if not steps:
            return None, False
        worst = max(
            steps,
            key=lambda s: (
                s.unserved_critical_kw,
                len(s.shed_load_ids),
                -s.reserve_hours,
            ),
        )
        if worst.unserved_critical_kw <= 1e-9 and not worst.shed_load_ids:
            return (steps[0], True)
        return worst, True

    def _mechanism(self, propagation: PropagationResult) -> list[str]:
        """Name the mechanism, not the outcome.

        Pack 1 could say that a function was not supported. It could not say
        *what it was short of*, because the only evidence was a number that came
        out zero. These lines are the difference, and they are restricted to the
        mission's critical functions because that is what the panel is for.
        """

        critical = set(self.mission.critical_loads)
        lines: list[str] = []
        for consequence in propagation.consequences.values():
            if consequence.asset_id not in critical or not consequence.because:
                continue
            lines.extend(consequence.because)
        for note in propagation.unknown:
            lines.append(
                f"The dependency graph says nothing about {note}, so nothing is claimed "
                "about what it depends on."
            )
        seen: list[str] = []
        for line in lines:
            if line not in seen:
                seen.append(line)
        return seen

    # -- narrative helpers --------------------------------------------------

    def _what_changed(
        self,
        events: Sequence[FailureEvent],
        before: MissionAssessment,
        after: MissionAssessment,
    ) -> list[str]:
        lines: list[str] = []
        for event in events:
            asset = self.engine.inventory.find(event.asset_id)
            name = asset.name if asset else event.asset_id
            lines.append(
                f"H+{event.hour:.0f}: {name} ({event.asset_id}) is {str(event.state).lower()}."
            )
        if after.status != before.status:
            lines.append(f"Mission status moved from {before.status} to {after.status}.")
        endurance_delta = after.endurance_remaining_h - before.endurance_remaining_h
        if abs(endurance_delta) > 0.05:
            lines.append(
                f"Assured support from here changed by {endurance_delta:+.1f} h "
                f"({before.endurance_remaining_h:.0f} h to {after.endurance_remaining_h:.0f} h "
                f"against {after.mission_remaining_h:.0f} h of mission remaining)."
            )
        reserve_delta = after.reserve_hours - before.reserve_hours
        if abs(reserve_delta) > 0.05:
            lines.append(
                f"Minimum energy reserve changed by {reserve_delta:+.1f} h "
                f"(now {after.reserve_hours:.1f} h of critical load)."
            )
        newly_degraded = [f for f in after.functions_degraded if f not in before.functions_degraded]
        if newly_degraded:
            lines.append(f"Critical functions newly at risk: {', '.join(newly_degraded)}.")
        newly_shed = [f for f in after.functions_shed if f not in before.functions_shed]
        if newly_shed:
            lines.append(f"Functions now unsupported: {', '.join(newly_shed)}.")
        before_ride = before.metrics.n_minus_1_ride_through_h if before.metrics else 0.0
        after_ride = after.metrics.n_minus_1_ride_through_h if after.metrics else 0.0
        if abs(after_ride - before_ride) > 0.1:
            lines.append(
                f"Tolerance to losing the largest remaining generator fell from "
                f"{before_ride:.1f} h to {after_ride:.1f} h."
            )
        if not lines:
            lines.append("No measurable change to the mission picture.")
        return lines

    def _why_it_matters(
        self, before: MissionAssessment, after: MissionAssessment
    ) -> list[str]:
        lines: list[str] = []
        if after.functions_degraded:
            lines.append(
                "Critical functions named in the mission cannot be held for the rest of the "
                "mission on the current configuration. This is a mission-assurance failure, not "
                "a comfort issue."
            )
        else:
            lines.append(
                "Critical functions are still supported on the current configuration; what has "
                "been lost is margin, not capability."
            )
        if after.metrics and not after.metrics.reserve_requirement_met:
            lines.append(
                f"The node no longer holds the {self.mission.minimum_reserve_hours:.0f} h energy "
                "reserve the mission asks for, so there is less to absorb the next failure."
            )
        after_ride = after.metrics.n_minus_1_ride_through_h if after.metrics else 0.0
        if after_ride < 6.0:
            lines.append(
                f"If the largest remaining generator also stops, critical load can be held for "
                f"only {after_ride:.1f} h on stored energy - less than the time needed for most "
                "recovery actions."
            )
        if after.metrics and after.metrics.single_points_of_failure:
            spofs = ", ".join(s["asset_id"] for s in after.metrics.single_points_of_failure)
            lines.append(
                f"The mission now depends on {spofs}: losing any of them ends assured critical "
                "support."
            )
        if after.endurance_remaining_h < after.mission_remaining_h:
            lines.append(
                f"On the current configuration the node runs out {after.mission_remaining_h - after.endurance_remaining_h:.0f} h "
                "short of the end of the mission."
            )
        return lines

    # -- serialisation ------------------------------------------------------

    def status_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission.mission_id,
            "current_hour": self.current_hour,
            "selected_configuration_id": (
                self.selected.configuration.configuration_id if self.selected else None
            ),
            "state": self.state.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "premise_revisions": list(self.premise_revisions),
            "disclaimer": DEMONSTRATOR_DISCLAIMER,
        }
