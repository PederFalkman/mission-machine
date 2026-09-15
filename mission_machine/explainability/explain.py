"""Explanation, comparison, sensitivity and recommendation.

The rule this module exists to enforce: the system may recommend, and must
always say why, what else was possible, what the recommendation costs, and how
much of it rests on assumptions. It never decides. Every object produced here
carries ``operator_decision_required = True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.assumptions import ASSUMPTIONS, Assumption
from mission_machine.mission.spec import MINIMISABLE_QUANTITIES, MissionSpec
from mission_machine.planning.configuration import SecondaryPolicy
from mission_machine.planning.engine import PlannedOption, PlanningEngine, PlanningResult
from mission_machine.planning.metrics import ConfigurationMetrics

#: Metrics shown side by side on the COMPARE screen, in operator order.
COMPARISON_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("endurance_hours", "Endurance", "h", "higher"),
    ("critical_load_coverage", "Critical-load coverage", "%", "higher"),
    ("secondary_load_coverage", "Secondary-load coverage", "%", "higher"),
    ("fuel_consumption_l", "Fuel used", "L", "lower"),
    ("energy_reserve_hours_min", "Minimum reachable energy reserve", "h of critical load", "higher"),
    ("n_minus_1_ride_through_h", "Ride-through after largest generator lost", "h", "higher"),
    ("grid_dependence", "Grid dependence", "%", "lower"),
    ("number_of_active_assets", "Active assets", "", "lower"),
    ("single_point_of_failure_count", "Single points of failure", "", "lower"),
    ("recovery_option_count", "Recovery options", "", "higher"),
)


@dataclass
class TradeOff:
    """One concrete difference between two options, in operator language."""

    against: str
    statement: str
    metric: str
    delta: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "against": self.against,
            "statement": self.statement,
            "metric": self.metric,
            "delta": round(self.delta, 3),
        }


@dataclass
class Confidence:
    """How much the recommendation moves when the assumptions move."""

    level: str                      # HIGH | MEDIUM | LOW
    basis: str
    variants: list[dict[str, Any]] = field(default_factory=list)
    endurance_range_h: tuple[float, float] = (0.0, 0.0)
    critical_assurance_holds_in: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "basis": self.basis,
            "variants": list(self.variants),
            "endurance_range_h": [round(v, 2) for v in self.endurance_range_h],
            "critical_assurance_holds_in": self.critical_assurance_holds_in,
            "data_labels": ["SIMULATED", "ASSUMED", "UNVALIDATED"],
        }


@dataclass
class Recommendation:
    """A recommendation - explicitly not a decision."""

    mission_id: str
    recommended_configuration_id: str
    recommended_label: str
    why: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    trade_offs: list[TradeOff] = field(default_factory=list)
    confidence: Confidence | None = None
    assumptions: list[Assumption] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    operator_decision_required: bool = True
    decision_prompt: str = "Operator decision required. Select a configuration to proceed."
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "recommended_configuration_id": self.recommended_configuration_id,
            "recommended_label": self.recommended_label,
            "why": list(self.why),
            "alternatives": list(self.alternatives),
            "trade_offs": [t.to_dict() for t in self.trade_offs],
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "assumptions": [a.to_dict() for a in self.assumptions],
            "caveats": list(self.caveats),
            "operator_decision_required": self.operator_decision_required,
            "decision_prompt": self.decision_prompt,
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _metric_value(metrics: ConfigurationMetrics, key: str) -> float:
    value = getattr(metrics, key)
    return float(value)


def comparison_table(options: Sequence[PlannedOption]) -> list[dict[str, Any]]:
    """Side-by-side rows for the COMPARE screen."""

    rows: list[dict[str, Any]] = []
    for key, title, unit, better in COMPARISON_ROWS:
        values = [_metric_value(option.metrics, key) for option in options]
        if not values:
            continue
        best = max(values) if better == "higher" else min(values)
        rows.append(
            {
                "metric": key,
                "title": title,
                "unit": unit,
                "better": better,
                "values": [
                    {
                        "configuration_id": option.configuration.configuration_id,
                        "label": option.label,
                        "value": round(value, 4),
                        "is_best": abs(value - best) < 1e-9,
                    }
                    for option, value in zip(options, values)
                ],
            }
        )
    return rows


def trade_offs(option: PlannedOption, others: Sequence[PlannedOption]) -> list[TradeOff]:
    """What choosing ``option`` gives up compared with each alternative."""

    result: list[TradeOff] = []
    for other in others:
        if other.configuration.configuration_id == option.configuration.configuration_id:
            continue
        a, b = option.metrics, other.metrics
        if b.fuel_consumption_l < a.fuel_consumption_l - 1.0:
            result.append(
                TradeOff(
                    against=other.label,
                    statement=(
                        f"Uses {a.fuel_consumption_l - b.fuel_consumption_l:.0f} L more fuel than "
                        f"{other.label}."
                    ),
                    metric="fuel_consumption_l",
                    delta=a.fuel_consumption_l - b.fuel_consumption_l,
                )
            )
        if b.number_of_active_assets < a.number_of_active_assets:
            result.append(
                TradeOff(
                    against=other.label,
                    statement=(
                        f"Requires {a.number_of_active_assets - b.number_of_active_assets} more "
                        f"asset(s) to deploy and supervise than {other.label}."
                    ),
                    metric="number_of_active_assets",
                    delta=a.number_of_active_assets - b.number_of_active_assets,
                )
            )
        if b.n_minus_1_ride_through_h > a.n_minus_1_ride_through_h + 0.5:
            result.append(
                TradeOff(
                    against=other.label,
                    statement=(
                        f"Holds critical load for only {a.n_minus_1_ride_through_h:.1f} h after "
                        f"losing its largest generator, against {b.n_minus_1_ride_through_h:.1f} h "
                        f"for {other.label}."
                    ),
                    metric="n_minus_1_ride_through_h",
                    delta=a.n_minus_1_ride_through_h - b.n_minus_1_ride_through_h,
                )
            )
        if b.secondary_load_coverage > a.secondary_load_coverage + 0.01:
            result.append(
                TradeOff(
                    against=other.label,
                    statement=(
                        f"Supports {b.secondary_load_coverage - a.secondary_load_coverage:.0%} "
                        f"less discretionary load than {other.label}."
                    ),
                    metric="secondary_load_coverage",
                    delta=a.secondary_load_coverage - b.secondary_load_coverage,
                )
            )
        if b.energy_reserve_hours_min > a.energy_reserve_hours_min + 0.5:
            result.append(
                TradeOff(
                    against=other.label,
                    statement=(
                        f"Keeps {b.energy_reserve_hours_min - a.energy_reserve_hours_min:.1f} h "
                        f"less minimum energy reserve than {other.label}."
                    ),
                    metric="energy_reserve_hours_min",
                    delta=a.energy_reserve_hours_min - b.energy_reserve_hours_min,
                )
            )
    return result


def why_this_option(
    option: PlannedOption,
    others: Sequence[PlannedOption],
    mission: MissionSpec | None = None,
) -> list[str]:
    """Reasons an operator can check, drawn from the metrics, not from prose."""

    metrics = option.metrics
    reasons: list[str] = [option.configuration.intent] if option.configuration.intent else []
    if metrics.completes_mission:
        reasons.append(
            f"Holds every critical function for the full {metrics.mission_duration_h:.0f} h "
            f"({metrics.critical_load_coverage:.1%} critical-load coverage, requirement "
            f"{metrics.required_availability:.0%})."
        )
    else:
        reasons.append(
            f"Assured critical support ends after {metrics.endurance_hours:.0f} h "
            f"({metrics.endurance_limited_by.replace('_', ' ').lower()})."
        )
    pool = [o.metrics for o in others]
    if pool and metrics.fuel_consumption_l <= min(m.fuel_consumption_l for m in pool):
        reasons.append(
            f"Lowest fuel use of the options generated: {metrics.fuel_consumption_l:.0f} L of "
            f"{metrics.fuel_limit_l:.0f} L on site."
        )
    if pool and metrics.n_minus_1_ride_through_h >= max(
        m.n_minus_1_ride_through_h for m in pool
    ):
        if metrics.n_minus_1_ride_through_h > 0:
            reasons.append(
                f"Best tolerance to a generator failure: critical load held for "
                f"{metrics.n_minus_1_ride_through_h:.1f} h after losing the largest set."
            )
    if pool and metrics.number_of_active_assets <= min(m.number_of_active_assets for m in pool):
        reasons.append(
            f"Fewest assets to move, connect and supervise ({metrics.number_of_active_assets}), "
            f"setup critical path {metrics.deployment.setup_critical_path_min:.0f} min."
        )
    reserve_line = (
        f"Minimum energy reserve {metrics.energy_reserve_hours_min:.1f} h of critical load "
        f"against a requirement of {metrics.reserve_requirement_hours:.0f} h."
    )
    if metrics.energy_reserve_withheld_kwh > 0.5:
        reserve_line += (
            f" A further {metrics.energy_reserve_withheld_kwh:.0f} kWh is on the node but "
            "cannot be reached in this configuration, and is not counted."
        )
    reasons.append(reserve_line)

    # Cite the operator's own words wherever they decided something.
    if mission is not None:
        protected = mission.never_interrupt_loads()
        if protected and metrics.n_minus_1_ride_through_h >= mission.ride_through_target_h:
            priority = mission.priority_for(protected[0])
            reasons.append(
                f"Holds {', '.join(protected)} for "
                f"{metrics.n_minus_1_ride_through_h:.1f} h after losing its largest generator, "
                f"against the {mission.ride_through_target_h:.0f} h it takes to deploy this node - "
                f'so operator priority {priority.rank if priority else 1} '
                f'("{priority.statement if priority else ""}") survives a single failure, not '
                "only the plan as drawn."
            )
        honoured = [
            load_id
            for load_id in mission.serve_if_affordable_loads()
            if load_id not in metrics.unhonoured_priorities
        ]
        if honoured:
            by_rank = {
                priority.rank: priority
                for priority in (mission.priority_for(load_id) for load_id in honoured)
                if priority is not None
            }
            for priority in sorted(by_rank.values(), key=lambda p: p.rank):
                reasons.append(
                    f"Serves {', '.join(priority.applies_to)} without threatening the critical "
                    f'functions, as operator priority {priority.rank} asks: "{priority.statement}"'
                )
        for rank, quantity in mission.minimise_preferences():
            attribute = MINIMISABLE_QUANTITIES.get(quantity)
            if attribute is None or not pool:
                continue
            value = float(getattr(metrics, attribute))
            if value <= min(float(getattr(m, attribute)) for m in pool):
                statement = next(
                    (p.statement for p in mission.operator_priorities if p.rank == rank), ""
                )
                reasons.append(
                    f"Lowest {quantity.replace('_', ' ')} of the options generated, which is what "
                    f'operator priority {rank} asks for: "{statement}"'
                )
        if option.preauthorised_degradations:
            reasons.append(
                "Uses the degraded mode the operator pre-authorised for "
                + ", ".join(option.preauthorised_degradations)
                + ". Nothing else feasible was found; the authorisation was given in advance and "
                "should be confirmed before this option is selected."
            )
    if metrics.secondary_load_coverage < 0.01:
        reasons.append(
            "No discretionary load is served: every secondary function is shed to protect fuel "
            "and reserve."
        )
    return reasons


# --------------------------------------------------------------------------
# sensitivity / confidence
# --------------------------------------------------------------------------


def sensitivity(
    engine: PlanningEngine,
    option: PlannedOption,
    *,
    load_scale: float = 1.2,
    temperature_offset_c: float = 6.0,
) -> Confidence:
    """Re-run one option under perturbed assumptions (RQ-004).

    Four variants, each changing one thing: heavier load than planned, hotter
    weather, much cloudier weather, and no grid at all.
    """

    from mission_machine.assets.loads import Load, LoadProfile
    from mission_machine.planning.metrics import compute_metrics
    from mission_machine.simulation.simulator import Simulator

    mission = engine.mission
    configuration = option.configuration
    variants: list[dict[str, Any]] = []

    def scaled_inventory(scale: float):
        inventory = engine.inventory.copy()
        for asset in inventory:
            if isinstance(asset, Load):
                profile = asset.profile
                asset.profile = LoadProfile(
                    type=profile.type,
                    kw=profile.kw * scale,
                    values=[v * scale for v in profile.values],
                    base_kw=profile.base_kw * scale,
                    swing_kw=profile.swing_kw * scale,
                    peak_hour=profile.peak_hour,
                    kw_per_degc=profile.kw_per_degc * scale,
                    reference_c=profile.reference_c,
                    windows=[[w[0], w[1], w[2] * scale] for w in profile.windows],
                    max_kw=profile.max_kw,
                )
        return inventory

    cases = [
        (
            "LOAD_HIGH",
            f"Every load {load_scale - 1:.0%} heavier than planned",
            engine.environment,
            scaled_inventory(load_scale),
        ),
        (
            "WEATHER_HOT",
            f"Ambient {temperature_offset_c:+.0f} degC on the planned profile",
            engine.environment.variant(
                name="hot", temperature_offset_c=temperature_offset_c
            ),
            engine.inventory,
        ),
        (
            "WEATHER_OVERCAST",
            "Persistent overcast: solar yield cut to 25 % of the planned profile",
            engine.environment.variant(name="overcast", cloud_scale=0.25),
            engine.inventory,
        ),
        (
            "GRID_ABSENT",
            "Host-nation supply never available",
            engine.environment.variant(name="nogrid", grid_available=False),
            engine.inventory,
        ),
    ]

    endurances: list[float] = []
    holds = 0
    for case_id, description, environment, inventory in cases:
        simulator = Simulator(mission, environment, inventory)
        result = simulator.run(configuration, start_hour=configuration.start_hour)
        metrics = compute_metrics(mission, configuration, result, inventory)
        endurances.append(metrics.endurance_hours)
        assured = metrics.availability_met and metrics.completes_mission
        holds += int(assured)
        variants.append(
            {
                "variant": case_id,
                "description": description,
                "critical_assurance_holds": assured,
                "endurance_hours": round(metrics.endurance_hours, 2),
                "critical_load_coverage": round(metrics.critical_load_coverage, 4),
                "fuel_consumption_l": round(metrics.fuel_consumption_l, 1),
                "energy_reserve_hours_min": round(metrics.energy_reserve_hours_min, 2),
            }
        )

    if holds == len(cases):
        level = "HIGH"
        basis = "Critical functions are assured in every perturbation tested."
    elif holds >= len(cases) - 1:
        level = "MEDIUM"
        basis = (
            "Critical functions are assured in all but one perturbation; the failing case is "
            "listed below and should drive the contingency plan."
        )
    else:
        level = "LOW"
        basis = (
            "Critical functions fail in more than one perturbation. This configuration depends "
            "heavily on the planned conditions holding."
        )
    return Confidence(
        level=level,
        basis=basis,
        variants=variants,
        endurance_range_h=(min(endurances, default=0.0), max(endurances, default=0.0)),
        critical_assurance_holds_in=f"{holds}/{len(cases)} perturbations",
    )


# --------------------------------------------------------------------------
# recommendation
# --------------------------------------------------------------------------


def _recommendation_key(option: PlannedOption, mission: MissionSpec | None = None) -> tuple:
    """Which option is put forward first, and why - in the operator's own order.

    Feasible first, then critical-load coverage, then whether the option serves
    every function the operator marked SERVE_IF_AFFORDABLE. After that the
    mission's own MINIMISE priorities decide, in the rank the operator gave
    them - but a function the operator said must *never* be interrupted first
    puts configurations that survive a single generator loss ahead of those that
    do not, because that is what "never" asks for. After that the MINIMISE
    preferences decide, in the rank the operator gave them, so an operator who wrote "minimise fuel resupply exposure" at rank 5
    gets the option that does that. Only when the mission states no preference
    the planner can read does it fall back to its own ordering: tolerance to
    losing the largest generator, then reserve, then least fuel.

    The operator can still override it with any option on the screen.
    """

    metrics = option.metrics
    key: list[float] = [
        float(option.feasible),
        metrics.critical_load_coverage,
        float(not metrics.unhonoured_priorities),
    ]
    if mission is not None and mission.never_interrupt_loads():
        # "Never interrupted" has to mean through a failure, not only in the
        # plan as drawn. The target comes from the mission's own deployment
        # time limit - long enough to do something about the failure.
        key.append(
            float(metrics.n_minus_1_ride_through_h >= mission.ride_through_target_h)
        )
    for _rank, quantity in (mission.minimise_preferences() if mission else []):
        attribute = MINIMISABLE_QUANTITIES.get(quantity)
        if attribute is not None:
            key.append(-float(getattr(metrics, attribute)))
    key.extend(
        [
            metrics.n_minus_1_ride_through_h,
            metrics.energy_reserve_hours_min,
            -metrics.fuel_consumption_l,
        ]
    )
    return tuple(key)


def _more_robust_alternative(
    engine: PlanningEngine,
    best: PlannedOption,
    others: Sequence[PlannedOption],
    confidence: Confidence,
) -> tuple[PlannedOption, str] | None:
    """The alternative that survives most perturbations, if it beats the leader."""

    best_holds = sum(1 for v in confidence.variants if v["critical_assurance_holds"])
    ranked: list[tuple[int, PlannedOption, str]] = []
    for option in others:
        other = sensitivity(engine, option)
        holds = sum(1 for v in other.variants if v["critical_assurance_holds"])
        ranked.append((holds, option, other.critical_assurance_holds_in))
    if not ranked:
        return None
    holds, option, summary = max(ranked, key=lambda item: item[0])
    return (option, summary) if holds > best_holds else None


def build_recommendation(
    plan: PlanningResult,
    engine: PlanningEngine | None = None,
    *,
    include_sensitivity: bool = True,
) -> Recommendation:
    """Assemble RECOMMENDED / WHY / ALTERNATIVES / TRADE-OFFS / CONFIDENCE."""

    if not plan.options:
        raise ValueError("cannot recommend from an empty plan")

    mission = engine.mission if engine is not None else None
    best = max(plan.options, key=lambda option: _recommendation_key(option, mission))
    others = [o for o in plan.options if o is not best]

    confidence = None
    if include_sensitivity and engine is not None:
        confidence = sensitivity(engine, best)

    caveats = list(best.caveats)
    caveats.extend(plan.notes)
    if plan.discretionary_assessment.get("available"):
        caveats.append(plan.discretionary_assessment["statement"])

    # Following the operator's stated ranking can land on a less robust option.
    # The machine does not overrule them for it, and does not let it pass
    # quietly either: if another option on the screen survives more of the
    # perturbations, name it.
    if confidence is not None and confidence.level != "HIGH" and engine is not None:
        sturdier = _more_robust_alternative(engine, best, others, confidence)
        if sturdier is not None:
            alternative, holds = sturdier
            caveats.append(
                f"This option holds critical assurance in only "
                f"{confidence.critical_assurance_holds_in}, against {holds} for "
                f"{alternative.label}. It leads here because it is what the operator's own "
                "priorities ask for; whether robustness outranks that is the operator's call."
            )

    return Recommendation(
        mission_id=plan.mission_id,
        recommended_configuration_id=best.configuration.configuration_id,
        recommended_label=best.label,
        why=why_this_option(best, others, mission),
        alternatives=[
            {
                "configuration_id": other.configuration.configuration_id,
                "label": other.label,
                "intent": other.configuration.intent,
                "feasible": other.feasible,
                "endurance_hours": round(other.metrics.endurance_hours, 2),
                "fuel_consumption_l": round(other.metrics.fuel_consumption_l, 1),
                "secondary_load_coverage": round(other.metrics.secondary_load_coverage, 3),
                "number_of_active_assets": other.metrics.number_of_active_assets,
                "why": why_this_option(
                    other, [o for o in plan.options if o is not other], mission
                ),
            }
            for other in others
        ],
        trade_offs=trade_offs(best, others),
        confidence=confidence,
        assumptions=list(ASSUMPTIONS),
        caveats=caveats,
    )
