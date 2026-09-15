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
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.explain import (
    Recommendation,
    build_recommendation,
    comparison_table,
    trade_offs,
)
from mission_machine.mission.spec import MissionSpec
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
from mission_machine.resilience.failures import FailureEvent, Scenario
from mission_machine.simulation.simulator import SimulationResult, Simulator
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
            "operator_decision_required": self.operator_decision_required,
            "decision_prompt": self.decision_prompt,
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

    def __init__(self, mission: MissionSpec, engine: PlanningEngine | None = None) -> None:
        self.mission = mission
        self.engine = engine or PlanningEngine(mission)
        self.analyst = ResilienceAnalyst(self.engine)
        self.simulator = Simulator(mission, self.engine.environment, self.engine.inventory)
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
        return result

    def project(
        self,
        configuration: Configuration | None = None,
        *,
        events: Sequence[FailureEvent] | None = None,
    ) -> tuple[SimulationResult, ConfigurationMetrics]:
        """Simulate from now to the end of the mission without advancing time."""

        configuration = configuration or (self.selected.configuration if self.selected else None)
        if configuration is None:
            raise OperationsError("no configuration has been selected")
        result = self.simulator.run(
            configuration,
            start_hour=self.current_hour,
            end_hour=self.mission.mission_duration_h,
            initial_state=self.state,
            events=list(self.events if events is None else events),
        )
        metrics = compute_metrics(self.mission, configuration, result, self.engine.inventory)
        return result, metrics

    # -- assessment ---------------------------------------------------------

    def assess(
        self,
        *,
        configuration: Configuration | None = None,
        events: Sequence[FailureEvent] | None = None,
    ) -> MissionAssessment:
        configuration = configuration or (self.selected.configuration if self.selected else None)
        if configuration is None:
            raise OperationsError("no configuration has been selected")
        active_events = list(self.events if events is None else events)
        result, metrics = self.project(configuration, events=active_events)
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
            if not configuration.policy.attempts(load) or load.asset_id in metrics.shed_load_ids
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
            "disclaimer": DEMONSTRATOR_DISCLAIMER,
        }
