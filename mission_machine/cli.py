"""Command-line interface for the Mission Machine demonstrator.

    mission-machine demo          run the full MM-DEMO-001 demonstration flow
    mission-machine mission       show the mission definition and assets
    mission-machine configure     generate and compare configurations
    mission-machine operate       run a configuration and inject a failure
    mission-machine verify        check the plans against the MILP model
    mission-machine export-lp     write the MILP formulation in LP format
    mission-machine serve         start the web UI
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any, Sequence

from mission_machine import __version__
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.assumptions import ASSUMPTIONS
from mission_machine.explainability.explain import (
    build_recommendation,
    comparison_table,
)
from mission_machine.mission.library import DEFAULT_MISSION_ID, list_missions, load_mission
from mission_machine.operations.session import OperationsSession
from mission_machine.planning import milp
from mission_machine.planning.engine import DEFAULT_STRATEGIES, Strategy
from mission_machine.resilience.failures import (
    GENERATOR_B_AND_GRID_LOSS,
    GENERATOR_B_UNAVAILABLE,
    Scenario,
)

RULE = "=" * 78
THIN = "-" * 78

SCENARIOS: dict[str, Scenario] = {
    GENERATOR_B_UNAVAILABLE.scenario_id: GENERATOR_B_UNAVAILABLE,
    GENERATOR_B_AND_GRID_LOSS.scenario_id: GENERATOR_B_AND_GRID_LOSS,
}


# --------------------------------------------------------------------------
# printing helpers
# --------------------------------------------------------------------------


def banner(title: str) -> None:
    print()
    print(RULE)
    print(title)
    print(RULE)


def section(title: str) -> None:
    print()
    print(title)
    print(THIN)


def disclaimer() -> None:
    print(DEMONSTRATOR_DISCLAIMER)


def print_mission(mission) -> None:
    environment = mission.build_environment()
    banner(f"MISSION {mission.mission_id} - {mission.name}")
    disclaimer()
    section("WHAT MUST CONTINUE OPERATING")
    for load in mission.critical_load_assets() + mission.secondary_load_assets():
        klass = "CRITICAL " if load.is_critical else "SECONDARY"
        priority = mission.priority_for(load.asset_id)
        intent = (
            f"{priority.intent} (priority {priority.rank})" if priority else "intent not stated"
        )
        print(f"  [{klass}] {load.asset_id:<12} {load.function}")
        print(f"{'':<16}{intent}")
    section("FOR HOW LONG")
    print(f"  {mission.mission_duration_h:.0f} hours, planned in {mission.time_step_h:.0f} h steps")
    print(f"  Mean critical demand {mission.mean_critical_demand_kw(environment):.1f} kW")
    print(f"  Critical energy required {mission.critical_energy_demand_kwh(environment):.0f} kWh")
    section("WHAT RESOURCES ARE AVAILABLE")
    for asset in mission.inventory:
        if asset.is_load:
            continue
        detail = f"{asset.capacity_kw:.0f} kW"
        if asset.energy_capacity_kwh:
            detail += f" / {asset.energy_capacity_kwh:.0f} kWh"
        optional = " (optional)" if asset.optional else ""
        print(f"  {asset.asset_id:<10} {asset.name:<48} {detail}{optional}")
    section("WHAT CONSTRAINTS APPLY")
    print(f"  Required critical-load availability : {mission.required_availability:.1%}")
    print(
        f"  Minimum energy reserve              : {mission.minimum_reserve.value:.0f} "
        f"({mission.minimum_reserve.type})"
    )
    print(f"  Fuel on site                        : {mission.fuel_limit_l:.0f} L (no resupply)")
    print(f"  Deployment time limit               : {mission.deployment_time_limit_min:.0f} min")
    print(
        f"  Grid availability                   : "
        f"{environment.grid.availability_fraction(mission.mission_duration_h):.0%} of the mission"
    )
    excluded = mission.mobility_excluded_assets()
    if excluded:
        print(f"  Could not move with the node        : {', '.join(excluded)}")
    section("OPERATOR PRIORITIES")
    for priority in sorted(mission.operator_priorities, key=lambda p: p.rank):
        applies = f" [{', '.join(priority.applies_to)}]" if priority.applies_to else ""
        quantity = f" ({priority.quantity})" if priority.quantity else ""
        print(f"  {priority.rank}. {priority.statement}")
        print(f"       -> {priority.intent}{quantity}{applies}")
    advisory = mission.advisory_priorities()
    if advisory:
        print()
        print(
            "  NOT ACTED ON: priorities "
            + ", ".join(str(p.rank) for p in advisory)
            + " are recorded and shown, but the planner has no way to act on them."
        )
    problems = mission.validate()
    if problems:
        section("MISSION DEFINITION PROBLEMS")
        for problem in problems:
            print(f"  ! {problem}")


def print_options(plan) -> None:
    section("GENERATED CONFIGURATIONS")
    for option in plan.options:
        metrics = option.metrics
        print()
        print(f"  {option.label}   [{option.configuration.configuration_id}]")
        print(f"    Intent: {option.configuration.intent}")
        for line in option.configuration.description or option.configuration.describe():
            print(f"      - {line}")
        print(
            f"    Endurance {metrics.endurance_hours:.0f} h | fuel {metrics.fuel_consumption_l:.0f} L | "
            f"critical {metrics.critical_load_coverage:.0%} | secondary {metrics.secondary_load_coverage:.0%} | "
            f"reserve {metrics.energy_reserve_hours_min:.1f} h"
        )
        print(
            f"    Assets {metrics.number_of_active_assets} | setup path "
            f"{metrics.deployment.setup_critical_path_min:.0f} min | SPOF "
            f"{metrics.single_point_of_failure_count} | recovery options "
            f"{metrics.recovery_option_count} | feasible: {'YES' if option.feasible else 'NO'}"
        )
        if metrics.energy_reserve_withheld_kwh > 0.5:
            print(
                f"    Withheld from the reserve: "
                f"{metrics.energy_reserve_withheld_kwh:.0f} kWh the node holds but this "
                "configuration cannot reach."
            )
        for question in metrics.open_questions:
            print(f"    ? {question['question']}")
            print(f"        {question['impact']}")
        for caveat in option.caveats:
            print(f"    ! {caveat}")
    if plan.notes:
        print()
        for note in plan.notes:
            print(f"  NOTE: {note}")


def print_comparison(plan) -> None:
    section("COMPARE")
    options = plan.options
    header = f"  {'Metric':<50}" + "".join(
        f"{option.configuration.configuration_id:>12}" for option in options
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in comparison_table(options):
        cells = ""
        for value in row["values"]:
            raw = value["value"]
            if row["unit"] == "%":
                text = f"{raw:.0%}"
            elif isinstance(raw, float) and raw != int(raw):
                text = f"{raw:.1f}"
            else:
                text = f"{int(raw)}"
            cells += f"{text + ('*' if value['is_best'] else ' '):>12}"
        title = f"{row['title']}" + (f" [{row['unit']}]" if row["unit"] not in ("", "%") else "")
        print(f"  {title:<50}{cells}")
    print("  * best of the options generated on that dimension alone.")
    if plan.discretionary_assessment:
        print()
        print(f"  DISCRETIONARY FUNCTIONS: {plan.discretionary_assessment['statement']}")


def print_recommendation(recommendation) -> None:
    section("RECOMMENDED OPTION")
    print(f"  {recommendation.recommended_label}  [{recommendation.recommended_configuration_id}]")
    print()
    print("  WHY")
    for reason in recommendation.why:
        print(f"    - {reason}")
    if recommendation.trade_offs:
        print()
        print("  TRADE-OFFS")
        for trade in recommendation.trade_offs:
            print(f"    - {trade.statement}")
    if recommendation.confidence:
        confidence = recommendation.confidence
        print()
        print(f"  CONFIDENCE: {confidence.level} ({confidence.critical_assurance_holds_in})")
        print(f"    {confidence.basis}")
        for variant in confidence.variants:
            mark = "holds" if variant["critical_assurance_holds"] else "FAILS"
            print(
                f"    - {variant['variant']:<16} {mark:<6} endurance "
                f"{variant['endurance_hours']:.0f} h  ({variant['description']})"
            )
    if recommendation.caveats:
        print()
        print("  CAVEATS")
        for caveat in recommendation.caveats:
            print(f"    ! {caveat}")
    print()
    print(f"  OPERATOR DECISION: {recommendation.decision_prompt}")


def print_assessment(assessment) -> None:
    print(f"  Status                : {assessment.status}")
    print(
        f"  Assured support       : {assessment.endurance_remaining_h:.0f} h of "
        f"{assessment.mission_remaining_h:.0f} h remaining"
    )
    print(
        f"  Energy reserve        : {assessment.reserve_hours:.1f} h of critical load "
        f"(requirement {assessment.reserve_requirement_hours:.0f} h)"
    )
    print(f"  Fuel remaining        : {assessment.fuel_remaining_l:.0f} L")
    print(f"  Battery state of charge: {assessment.battery_soc:.0%}")
    print(f"  Critical supported    : {', '.join(assessment.critical_functions_supported) or 'none'}")
    if assessment.functions_degraded:
        print(f"  Critical degraded     : {', '.join(assessment.functions_degraded)}")
    if assessment.functions_shed:
        print(f"  Not supported         : {', '.join(assessment.functions_shed)}")
    for alert in assessment.alerts:
        print(f"  ALERT: {alert}")


def print_report(report) -> None:
    section("WHAT CHANGED")
    for line in report.what_changed:
        print(f"  - {line}")
    section("WHY IT MATTERS")
    for line in report.why_it_matters:
        print(f"  - {line}")
    section("MISSION PICTURE NOW")
    print_assessment(report.assessment_after)
    if report.recovery_options:
        section("AVAILABLE ACTIONS ON THE CURRENT CONFIGURATION")
        for option in report.recovery_options:
            restores = " [restores critical assurance]" if option["restores_critical_assurance"] else ""
            print(f"  - {option['action']}{restores}")
            print(f"      {option['description']}")
            print(
                f"      endurance {option['endurance_delta_h']:+.1f} h | fuel "
                f"{option['fuel_delta_l']:+.0f} L | reserve {option['reserve_delta_h']:+.1f} h | "
                f"ready in {option['time_to_effect_min']:.0f} min"
            )
            print(f"      cost: {option['cost_note']}")
    if report.options:
        print_options(report.options)
        print_comparison(report.options)
    if report.recommendation:
        print_recommendation(report.recommendation)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _session(args) -> OperationsSession:
    mission = load_mission(args.mission_id)
    problems = mission.validate()
    if problems:
        print("MissionSpec validation failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(2)
    return OperationsSession(mission)


def _strategies(names: Sequence[str] | None) -> tuple[Strategy, ...]:
    if not names:
        return DEFAULT_STRATEGIES
    return tuple(Strategy(name.upper()) for name in names)


def cmd_mission(args) -> int:
    mission = load_mission(args.mission_id)
    if args.json:
        payload = {
            "mission": mission.to_dict(),
            "inventory": mission.inventory.to_dict(),
            "environment": mission.build_environment().to_dict(),
            "validation": mission.validate(),
        }
        print(json.dumps(payload, indent=2))
        return 0
    print_mission(mission)
    return 0


def cmd_configure(args) -> int:
    session = _session(args)
    plan = session.generate_options(strategies=_strategies(args.strategy))
    recommendation = build_recommendation(plan, session.engine, include_sensitivity=not args.fast)
    if args.json:
        print(
            json.dumps(
                {
                    "plan": plan.to_dict(include_steps=args.steps),
                    "comparison": comparison_table(plan.options),
                    "recommendation": recommendation.to_dict(),
                },
                indent=2,
            )
        )
        return 0
    banner(f"CONFIGURE - {plan.mission_id}")
    disclaimer()
    print(
        f"\n  {plan.candidates_evaluated} candidate configurations evaluated, "
        f"{plan.feasible_candidates} feasible."
    )
    print_options(plan)
    print_comparison(plan)
    print_recommendation(recommendation)
    return 0


def cmd_operate(args) -> int:
    session = _session(args)
    plan = session.generate_options()
    recommendation = build_recommendation(plan, session.engine, include_sensitivity=False)
    selected = args.configuration or recommendation.recommended_configuration_id
    session.select(selected, rationale=args.rationale, recommended=recommendation.recommended_configuration_id)
    banner(f"OPERATE - {plan.mission_id} - {session.selected.label}")
    disclaimer()
    if args.at:
        session.run_to(args.at)
    section(f"MISSION PICTURE AT H+{session.current_hour:.0f}")
    print_assessment(session.assess())
    if args.scenario:
        scenario = SCENARIOS[args.scenario]
        report = session.inject(scenario)
        banner(f"DEGRADED MODE - {scenario.scenario_id} - {scenario.name}")
        print(f"  {scenario.description}")
        print_report(report)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
    return 0


def cmd_optimise(args) -> int:
    """Put the chosen configurations to a real solver and report the difference."""

    from mission_machine.planning.optimal import compare_with_optimum
    from mission_machine.planning.providers import DEFAULT_REGISTRY

    session = _session(args)
    plan = session.generate_options(with_resilience=False)

    banner("OPTIMAL DISPATCH - HOW GOOD ARE THE RULES?")
    disclaimer()
    section("SOLVER BACKENDS")
    for descriptor in DEFAULT_REGISTRY.status_report():
        mark = "available" if descriptor.available else "not available"
        version = f" {descriptor.version}" if descriptor.version else ""
        print(f"  {descriptor.name:<16}{mark:<16}{descriptor.kind}{version}")
        print(f"      {descriptor.detail}")

    comparisons = []
    section("RULE-BASED DISPATCH AGAINST THE OPTIMUM FOR THE SAME CONFIGURATION")
    for option in plan.options:
        comparison = compare_with_optimum(
            session.mission,
            option.configuration,
            option.simulation,
            environment=session.engine.environment,
            inventory=session.engine.inventory,
            time_budget_s=args.budget,
            baseline_fuel_l=option.metrics.fuel_consumption_l,
        )
        comparisons.append(comparison)
        if comparison.trustworthy:
            print(
                f"  {comparison.configuration_id:<8} rules {comparison.baseline_fuel_l:6.1f} L | "
                f"optimum {comparison.optimal_fuel_l:6.1f} L | "
                f"{comparison.saving_fraction:5.1%} left on the table "
                f"({comparison.status}, {comparison.backend}, {comparison.wall_time_s:.0f} s)"
            )
        else:
            print(
                f"  {comparison.configuration_id:<8} no usable optimum: {comparison.status} "
                f"({comparison.backend})"
            )
            print(f"      {comparison.detail}")

    usable = [c for c in comparisons if c.trustworthy]
    if usable:
        section("WHAT THIS DOES AND DOES NOT SAY")
        for caveat in usable[0].caveats:
            print(f"  - {caveat}")
        print()
        print(
            "  Every solver answer above was checked against the same declared constraint set "
            "that checks the simulator's schedules."
        )
    if args.json:
        print(json.dumps([c.to_dict() for c in comparisons], indent=2))
    return 0


def cmd_foresight(args) -> int:
    """How much of the solver's advantage is foresight rather than skill? (RQ-012)"""

    from mission_machine.planning.optimal import compare_with_optimum
    from mission_machine.planning.rolling import run_rolling_horizon

    session = _session(args)
    plan = session.generate_options(with_resilience=False)
    option = (
        plan.option(args.configuration)
        if args.configuration
        else plan.options[0]
    )
    windows = [float(w) for w in (args.window or (6, 12, 24, 48))]

    banner("FORESIGHT - WHAT THE SOLVER'S ADVANTAGE IS MADE OF")
    disclaimer()
    print(f"\n  {option.label} [{option.configuration.configuration_id}]")

    perfect = compare_with_optimum(
        session.mission,
        option.configuration,
        option.simulation,
        environment=session.engine.environment,
        inventory=session.engine.inventory,
        time_budget_s=args.budget,
        baseline_fuel_l=option.metrics.fuel_consumption_l,
    )
    if not perfect.trustworthy:
        section("NO USABLE OPTIMUM")
        print(f"  {perfect.status}: {perfect.detail}")
        return 1

    section("FUEL OVER THE MISSION, BY HOW FAR AHEAD THE CONTROLLER CAN SEE")
    print(
        f"  {'lookahead':>12} {'fuel (L)':>10} {'vs rules':>10} {'gap recovered':>15} {'solves':>8}"
    )
    print("  " + "-" * 60)
    print(
        f"  {'none (rules)':>12} {perfect.baseline_fuel_l:>10.1f} {'-':>10} {'-':>15} {'-':>8}"
    )
    results = []
    for window in windows:
        rolling = run_rolling_horizon(
            session.mission,
            option.configuration,
            option.simulation,
            window_h=window,
            commit_h=args.commit,
            environment=session.engine.environment,
            inventory=session.engine.inventory,
            time_budget_s=args.budget,
            baseline_fuel_l=option.metrics.fuel_consumption_l,
            perfect_foresight_fuel_l=perfect.optimal_fuel_l,
        )
        results.append(rolling)
        if rolling.completed:
            print(
                f"  {window:>10.0f} h {rolling.fuel_l:>10.1f} "
                f"{-rolling.saving_vs_rules_fraction:>9.1%} "
                f"{rolling.gap_recovered_fraction:>14.0%} {rolling.solves:>8}"
            )
        else:
            print(f"  {window:>10.0f} h {'no plan':>10} {rolling.detail[:40]}")
    print(
        f"  {'whole mission':>12} {perfect.optimal_fuel_l:>10.1f} "
        f"{-(perfect.saving_fraction):>9.1%} {1.0:>14.0%} {1:>8}"
    )

    section("WHAT THIS DOES AND DOES NOT SAY")
    for caveat in (results[-1].caveats if results and results[-1].completed else []):
        print(f"  - {caveat}")
    print(f"  - {perfect.caveats[1]}")
    if args.json:
        print(
            json.dumps(
                {
                    "perfect_foresight": perfect.to_dict(),
                    "rolling": [r.to_dict() for r in results],
                },
                indent=2,
            )
        )
    return 0


def cmd_forecast(args) -> int:
    """What a wrong forecast costs a rolling controller (RQ-014)."""

    from mission_machine.planning.rolling import run_closed_loop, run_rules_only

    session = _session(args)
    plan = session.generate_options(with_resilience=False)
    option = plan.option(args.configuration) if args.configuration else plan.options[0]
    nominal = session.engine.environment
    forecast_windows = nominal.grid.available_windows
    worlds: list[tuple[str, list]] = [("as forecast", [list(w) for w in forecast_windows])]
    for delay in (args.delay or (4.0, 8.0, 1e6)):
        if delay >= 1e6:
            worlds.append(("never returns", [list(forecast_windows[0])]))
            continue
        shifted = [list(forecast_windows[0])]
        for window in forecast_windows[1:]:
            shifted.append([window[0] + delay, window[1]])
        worlds.append((f"{delay:.0f} h late", shifted))

    banner("FORECAST ERROR - WHAT A WRONG PLAN COSTS")
    disclaimer()
    print(f"\n  {option.label} [{option.configuration.configuration_id}]")
    print(
        f"  The plan always expects host-nation supply to return at "
        f"H+{forecast_windows[1][0]:.0f}. The rows are what actually happened."
    )
    section("FUEL AND CRITICAL COVERAGE IN THE WORLD THAT HAPPENED")
    print(
        f"  {'grid returns':>16} {'rules only':>22} {'correct forecast':>22} {'nominal forecast':>22}"
    )
    print("  " + "-" * 84)

    rows = []
    for label, windows in worlds:
        realised = nominal.with_grid_windows(
            windows, name=label.replace(" ", "_"), note=f"Host-nation supply {label}."
        )
        rules = run_rules_only(
            session.mission,
            option.configuration,
            realised_environment=realised,
            inventory=session.engine.inventory,
        )
        correct = run_closed_loop(
            session.mission, option.configuration, option.simulation,
            realised_environment=realised, forecast_environment=realised,
            window_h=args.window, commit_h=args.commit,
            inventory=session.engine.inventory, time_budget_s=args.budget,
            arm="correct forecast",
        )
        wrong = run_closed_loop(
            session.mission, option.configuration, option.simulation,
            realised_environment=realised, forecast_environment=nominal,
            window_h=args.window, commit_h=args.commit,
            inventory=session.engine.inventory, time_budget_s=args.budget,
            arm="nominal forecast",
        )
        rows.append({"world": label, "rules": rules, "correct": correct, "wrong": wrong})

        def cell(result) -> str:
            if not result.completed:
                return f"{result.fuel_l:6.0f} L  FAILS @{result.endurance_h:.0f}h"
            mark = f" x{result.plan_overrides}" if result.plan_overrides else ""
            return f"{result.fuel_l:6.1f} L cov {result.critical_coverage:.2f}{mark}"

        print(
            f"  {label:>16} {cell(rules):>22} {cell(correct):>22} {cell(wrong):>22}"
        )

    section("WHAT THIS SAYS")
    print(
        "  'correct forecast' is the same controller told the truth: it separates the world\n"
        "  getting harder from the forecast being wrong. 'x N' counts windows where the plan\n"
        "  could not be carried out at all and the node reverted to its dispatch rules."
    )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "world": row["world"],
                        "rules": row["rules"].to_dict(),
                        "correct_forecast": row["correct"].to_dict(),
                        "nominal_forecast": row["wrong"].to_dict(),
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
    return 0


def cmd_scaling(args) -> int:
    """Measure where the enumerate-and-simulate baseline stops being tractable (RQ-009)."""

    import time

    from mission_machine.assets.base import FailureState
    from mission_machine.planning.engine import PlanningEngine

    mission = load_mission(args.mission_id)
    banner("CANDIDATE-SPACE SCALING (RQ-009)")
    disclaimer()
    print()
    print(f"  {'generators':>10} {'candidates':>12} {'evaluate (s)':>14} {'per candidate':>15}")
    print("  " + "-" * 54)
    template = mission.inventory.generators[0]
    for extra in range(0, args.max_extra_generators + 1):
        spec = load_mission(args.mission_id)
        for index in range(extra):
            clone = copy.deepcopy(template)
            clone.asset_id = f"GEN-X{index}"
            clone.name = f"Synthetic generator X{index}"
            clone.failure_state = FailureState.NOMINAL
            spec.inventory.assets.append(clone)
        engine = PlanningEngine(spec)
        candidates = len(engine.candidate_policies(0.0, []))
        started = time.monotonic()
        if candidates <= args.evaluate_limit:
            engine.generate_options()
            elapsed = time.monotonic() - started
            per = f"{elapsed / candidates * 1000:.1f} ms"
            elapsed_text = f"{elapsed:.1f}"
        else:
            elapsed_text = "not run"
            per = f"> {args.evaluate_limit} candidates"
        print(
            f"  {len(spec.inventory.generators):>10} {candidates:>12} {elapsed_text:>14} {per:>15}"
        )
    print()
    print(
        "  The candidate space doubles with every dispatchable asset added: the enumeration is\n"
        "  exponential in the number of assets and linear in the mission length. It is fine at\n"
        "  demonstrator scale and will not stay fine."
    )
    return 0


def cmd_verify(args) -> int:
    session = _session(args)
    plan = session.generate_options(with_resilience=False)
    model = milp.build_model(session.mission, session.engine.environment, session.engine.inventory)
    banner("MILP VERIFICATION")
    disclaimer()
    print(f"\n  Model {model.name}")
    for key, value in model.size().items():
        print(f"    {key:<26} {value}")
    print(f"    solver backends available  {milp.available_backends() or ['none']}")
    section("SCHEDULE CHECK")
    failures = 0
    for option in plan.options:
        report = milp.verify_simulation(
            session.mission,
            option.simulation,
            session.engine.environment,
            session.engine.inventory,
            model=model,
        )
        physics = "PASS" if report["physically_consistent"] else "FAIL"
        requirements = "PASS" if report["meets_requirements"] else "FAIL"
        print(
            f"  {option.configuration.configuration_id:<8} physics {physics}  "
            f"requirements {requirements}   {option.label}"
        )
        for violation in report["physics_violations"][:5]:
            print(f"      physics: {violation['constraint']} by {violation['amount']:.4f}")
        for violation in report["requirement_violations"][:5]:
            print(f"      requirement: {violation['constraint']} by {violation['amount']:.4f}")
        failures += report["physics_violation_count"]
    print()
    print(
        "  Every option's dispatch schedule was checked against the declared MILP "
        "constraint set."
    )
    return 1 if failures else 0


def cmd_export_lp(args) -> int:
    mission = load_mission(args.mission_id)
    model = milp.build_model(mission)
    text = model.to_lp_string()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Wrote {args.out} ({len(text)} bytes, {model.size()['variables']} variables)")
    else:
        print(text)
    return 0


def cmd_assumptions(args) -> int:
    if args.json:
        print(json.dumps([a.to_dict() for a in ASSUMPTIONS], indent=2))
        return 0
    banner("ASSUMPTION REGISTER")
    disclaimer()
    for assumption in ASSUMPTIONS:
        print()
        print(f"  {assumption.assumption_id} [{assumption.label}] ({assumption.category})")
        print(f"    {assumption.statement}")
        print(f"    If wrong: {assumption.impact_if_wrong}")
        print(f"    Where:    {assumption.where}")
    return 0


def cmd_serve(args) -> int:
    from mission_machine.ui.server import serve

    serve(host=args.host, port=args.port, mission_id=args.mission_id)
    return 0


def cmd_demo(args) -> int:
    """The full Pack 1 demonstration flow, in order."""

    session = _session(args)
    mission = session.mission

    banner("MISSION MACHINE - MM-DEMO-001 DEMONSTRATION")
    disclaimer()
    print(f"\n  mission-machine {__version__}")

    # 1-2. open the mission, show the assets
    print_mission(mission)

    # 3. generate configurations
    banner("STEP 3 - GENERATE CONFIGURATIONS")
    plan = session.generate_options()
    print(
        f"\n  {plan.candidates_evaluated} candidate configurations evaluated, "
        f"{plan.feasible_candidates} feasible against the mission requirements."
    )
    print_options(plan)

    # 4. compare
    banner("STEP 4 - COMPARE")
    print_comparison(plan)
    recommendation = build_recommendation(plan, session.engine)
    print_recommendation(recommendation)

    # 5. operator selects
    banner("STEP 5 - OPERATOR SELECTS A CONFIGURATION")
    selected = args.configuration or recommendation.recommended_configuration_id
    option = session.select(
        selected,
        rationale=args.rationale or "Demonstration: operator accepts the recommended option.",
        recommended=recommendation.recommended_configuration_id,
    )
    print(f"\n  Operator selected {option.label} [{option.configuration.configuration_id}]")
    print(
        "  The system records the decision, whether or not it matched the recommendation. "
        "The operator is the decision authority."
    )

    # 6. simulate normal operation
    scenario = SCENARIOS[args.scenario]
    event_hour = min(event.hour for event in scenario.events)
    banner(f"STEP 6 - SIMULATE NORMAL OPERATION TO H+{event_hour:.0f}")
    session.run_to(event_hour)
    print_assessment(session.assess())

    # 7-10. disruption, consequences, alternatives
    banner(f"STEP 7 - DISRUPTION: {scenario.name.upper()}")
    print(f"  {scenario.scenario_id}: {scenario.description}")
    report = session.inject(scenario, advance_to_event=False)
    print_report(report)

    # 11. operator responds
    banner("STEP 11 - OPERATOR SELECTS A RESPONSE")
    if report.recommendation and report.options:
        response = report.recommendation.recommended_configuration_id
        session.select(
            response,
            rationale="Demonstration: operator accepts the recommended reconfiguration.",
            recommended=response,
        )
        print(f"\n  Operator selected {report.recommendation.recommended_label} [{response}]")
    section("DECISION LOG")
    for decision in session.decisions:
        print(
            f"  H+{decision.at_hour:>4.0f}  {decision.decision:<22} {decision.configuration_id:<14} "
            f"followed recommendation: {decision.followed_recommendation}"
        )
    section("END OF DEMONSTRATION")
    disclaimer()
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mission-machine",
        description=(
            "Mission Machine - mission-support infrastructure planning and reconfiguration "
            "research demonstrator (Pack 1). All data SYNTHETIC, all results SIMULATED and "
            "UNVALIDATED."
        ),
    )
    parser.add_argument("--version", action="version", version=f"mission-machine {__version__}")
    parser.add_argument(
        "--mission-id",
        default=DEFAULT_MISSION_ID,
        help=f"mission to load (available: {', '.join(list_missions()) or 'none'})",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mission", help="show the mission definition").set_defaults(func=cmd_mission)

    configure = sub.add_parser("configure", help="generate and compare configurations")
    configure.add_argument(
        "--strategy",
        action="append",
        choices=[str(s) for s in Strategy],
        help="planning strategy (repeatable; default: the three Pack 1 options)",
    )
    configure.add_argument("--fast", action="store_true", help="skip the sensitivity sweep")
    configure.add_argument("--steps", action="store_true", help="include hourly steps in JSON")
    configure.set_defaults(func=cmd_configure)

    operate = sub.add_parser("operate", help="run a configuration and inject a failure")
    operate.add_argument("--configuration", help="configuration id to run (default: recommended)")
    operate.add_argument("--at", type=float, default=0.0, help="advance to this mission hour")
    operate.add_argument("--scenario", choices=sorted(SCENARIOS), help="disruption to inject")
    operate.add_argument("--rationale", default="", help="operator's reason for the selection")
    operate.set_defaults(func=cmd_operate)

    sub.add_parser("verify", help="check plans against the MILP model").set_defaults(func=cmd_verify)

    optimise = sub.add_parser(
        "optimise", help="solve the chosen configurations exactly and compare with the rules"
    )
    optimise.add_argument(
        "--budget", type=float, default=60.0, help="solver time budget per configuration, seconds"
    )
    optimise.set_defaults(func=cmd_optimise)

    foresight = sub.add_parser(
        "foresight", help="how much of the solver's advantage is lookahead (RQ-012)"
    )
    foresight.add_argument("--configuration", help="configuration id (default: the first option)")
    foresight.add_argument(
        "--window", type=float, action="append", help="lookahead in hours (repeatable)"
    )
    foresight.add_argument("--commit", type=float, default=6.0, help="replan cadence, hours")
    foresight.add_argument("--budget", type=float, default=15.0, help="solver budget per solve")
    foresight.set_defaults(func=cmd_foresight)

    forecast = sub.add_parser(
        "forecast", help="what a wrong forecast costs a rolling controller (RQ-014)"
    )
    forecast.add_argument("--configuration", help="configuration id (default: the first option)")
    forecast.add_argument(
        "--delay", type=float, action="append",
        help="hours late the grid actually returns; 1e6 for never (repeatable)",
    )
    forecast.add_argument("--window", type=float, default=12.0, help="lookahead, hours")
    forecast.add_argument("--commit", type=float, default=6.0, help="replan cadence, hours")
    forecast.add_argument("--budget", type=float, default=10.0, help="solver budget per solve")
    forecast.set_defaults(func=cmd_forecast)

    scaling = sub.add_parser("scaling", help="measure candidate-space growth (RQ-009)")
    scaling.add_argument("--max-extra-generators", type=int, default=4)
    scaling.add_argument("--evaluate-limit", type=int, default=2600)
    scaling.set_defaults(func=cmd_scaling)

    export = sub.add_parser("export-lp", help="write the MILP formulation in LP format")
    export.add_argument("--out", help="output file (default: stdout)")
    export.set_defaults(func=cmd_export_lp)

    sub.add_parser("assumptions", help="print the assumption register").set_defaults(
        func=cmd_assumptions
    )

    serve = sub.add_parser("serve", help="start the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="run the full MM-DEMO-001 demonstration flow")
    demo.add_argument("--configuration", help="configuration to select (default: recommended)")
    demo.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=GENERATOR_B_UNAVAILABLE.scenario_id,
        help="disruption to inject",
    )
    demo.add_argument("--rationale", default="", help="operator's reason for the selection")
    demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
