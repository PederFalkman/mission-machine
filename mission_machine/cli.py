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
from mission_machine.operations.premises import GRID_BREACH_HOURS
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.engine import PlanningEngine
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
    if report.mechanism:
        at = report.propagation.at_hour if report.propagation else None
        when = f"H+{at:.0f}" if at is not None else "now"
        projected = report.propagation.projected if report.propagation else False
        section(
            f"BY WHAT MECHANISM - {'worst projected hour' if projected else 'observed at'} {when}"
        )
        for line in report.mechanism:
            print(f"  {line}")
        print(
            "\n  Propagation reports that a dependency is unmet. It does not simulate the\n"
            "  consequence, and the dispatch is unchanged by any of it (AS-027)."
        )
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
    if getattr(args, "coast", False):
        # Let the search use the RQ-015 stop rule. Off by default because its
        # price is generator starts and this model prices one at nothing
        # (AS-026), not because the rule is worse - see `mission-machine rules`.
        from mission_machine.planning.configuration import GeneratorMode

        session.engine.generator_modes = tuple(GeneratorMode)
        print(
            "\n  Planning with the RQ-015 stop rule available. Its cost is generator starts,\n"
            "  which this model does not price (AS-026): read the options with that in hand."
        )
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


def cmd_alarms(args) -> int:
    """What a false alarm costs, and what a true one is worth (RQ-017)."""

    from mission_machine.operations.alarms import (
        HARMFUL,
        NUISANCE,
        WORTH_RAISING,
        Thresholds,
        demonstrator_worlds,
        evaluate_world,
        sweep_thresholds,
    )

    mission = load_mission(args.mission_id)
    engine = PlanningEngine(mission)
    worlds = demonstrator_worlds(mission, engine)
    if args.world:
        wanted = set(args.world)
        worlds = [world for world in worlds if world.key in wanted]
        if not worlds:
            print(f"  No world matches {', '.join(sorted(wanted))}.")
            return 1

    banner("PREMISE ALARMS - WHAT A FALSE ONE COSTS, WHAT A TRUE ONE IS WORTH")
    disclaimer()
    print(
        "\n  Each world is planned on the premise the mission states and then lived in.\n"
        "  Where the panel speaks, three continuations are compared from that hour,\n"
        "  differing only in which premise they plan against:\n"
        "    IGNORE  the premise as stated - the operator dismissed the panel\n"
        "    ACCEPT  the revision the machine offered\n"
        "    TRUTH   the world that is really there - nobody has this\n"
    )

    if args.load:
        from mission_machine.operations.alarms import alarm_load_table

        section("HOW OFTEN THE PANEL SPEAKS OVER A WHOLE MISSION")
        print(
            "  Nothing is accepted or dismissed along the way: this is the load on an\n"
            "  operator who reads every alarm and acts on none of it.\n"
        )
        rows = alarm_load_table(mission, worlds, engine=engine)
        print(
            f"  {'world':<18} {'interrupts':>10} {'every hour':>11} {'standing':>9} "
            f"{'resolved':>9}   raised at"
        )
        for row in rows:
            print(
                f"  {row.world_key:<18} {row.interruptions:>10} {row.contradiction_hours:>11} "
                f"{row.standing_hours:>9} {row.resolutions:>9}   "
                f"{', '.join('H+%.0f' % hour for hour in row.raised_at) or '-'}"
            )
        print(
            f"  {'TOTAL':<18} {sum(r.interruptions for r in rows):>10} "
            f"{sum(r.contradiction_hours for r in rows):>11} "
            f"{sum(r.standing_hours for r in rows):>9} "
            f"{sum(r.resolutions for r in rows):>9}"
        )
        print(
            "\n  'every hour' is what the panel would have said without the standing rule -\n"
            "  one alarm per hour for as long as the contradiction lasted. Both columns come\n"
            "  from the same run. What the difference costs an operator's attention is the\n"
            "  part no simulation here can price: RQ-018."
        )
        if args.json:
            print(json.dumps([row.to_dict() for row in rows], indent=2))
        return 0

    if args.sweep:
        ledger = sweep_thresholds(mission, tuple(args.sweep), worlds=worlds, engine=engine)
        section("WHERE TO DRAW THE LINE - CONSECUTIVE HOURS OF MISSING SUPPLY")
        # The latency column follows one world deliberately. "Earliest alarm of
        # any kind" is not a latency: the load detector fires at H+1 whatever
        # the grid threshold is, so that column would read H+1 on every row and
        # hide the thing being traded.
        watched = "grid-never" if any(w.key == "grid-never" for w in worlds) else worlds[0].key
        print(
            f"  {'threshold':>9}  {'raised':>6}  {'worth it':>8}  {'nuisance':>8}  "
            f"{'harmful':>7}  {'missed':>6}   {watched} caught"
        )
        for hours in args.sweep:
            cases = ledger.for_threshold(hours)
            counts = ledger.counts(cases)
            fired = [case for case in cases if case.fired]
            latency = next(
                (
                    case.detected_at_hour
                    for case in cases
                    if case.world.key == watched and case.detected_at_hour is not None
                ),
                None,
            )
            print(
                f"  {hours:>7.0f} h  {len(fired):>6}  {counts.get(WORTH_RAISING, 0):>8}  "
                f"{counts.get(NUISANCE, 0):>8}  {counts.get(HARMFUL, 0):>7}  "
                f"{counts.get('MISSED', 0):>6}   "
                f"{('H+%.0f' % latency) if latency is not None else 'not caught'}"
            )
        print(
            "\n  Raising the threshold buys quiet and costs time. The exchange rate is the\n"
            "  question; the columns are the answer for this mission and these worlds."
        )
        if args.json:
            print(json.dumps(ledger.to_dict(), indent=2))
        return 0

    thresholds = Thresholds(grid_hours=args.grid_hours)
    section(f"EVERY WORLD AT {thresholds.grid_hours:.0f} H OF MISSING SUPPLY")
    cache: dict = {}
    cases = []
    for world in worlds:
        case = evaluate_world(mission, world, thresholds, engine=engine, cache=cache)
        cases.append(case)
        headline = (
            f"H+{case.detected_at_hour:.0f} {case.premise_key}" if case.fired else "silent"
        )
        print(f"\n  {case.world.key:<18} {headline:<40} {case.verdict}")
        print(f"    {case.world.description}")
        if case.fired:
            print(f"    offered: {case.revision_statement}")
        ignore = case.outcomes.get("IGNORE")
        for arm in ("IGNORE", "ACCEPT", "TRUTH"):
            outcome = case.outcomes.get(arm)
            if outcome is None:
                continue
            print(
                f"    {arm:<7} critical short {outcome.critical_shortfall_kwh:6.1f} kWh | "
                f"discretionary {outcome.discretionary_served_kwh:6.1f} kWh | "
                f"fuel {outcome.fuel_used_l:6.1f} L"
            )
        if case.cost_of_acting and ignore is not None:
            print(f"    acting on it: {', '.join(case.cost_of_acting)} against ignoring it")

    section("WHAT THIS DOES AND DOES NOT ANSWER")
    tally: dict[str, int] = {}
    for case in cases:
        tally[case.verdict] = tally.get(case.verdict, 0) + 1
    print("  " + ", ".join(f"{count} {verdict}" for verdict, count in sorted(tally.items())))
    print(
        "\n  Answered: how often the panel speaks where there was nothing to gain, and what\n"
        "  acting on it costs in fuel and in service the node need not have given up.\n"
        "  Not answered: what a false alarm costs an operator's trust - whether the second\n"
        "  wrong alarm makes them close the panel and miss the third, true one. That is a\n"
        "  question about people and needs people. See RQ-017."
    )
    if args.json:
        print(json.dumps([case.to_dict() for case in cases], indent=2))
    return 0


def cmd_rules(args) -> int:
    """Can the dispatch rules close the solver's gap without a solver? (RQ-015)"""

    from dataclasses import replace

    from mission_machine.planning.configuration import GeneratorMode
    from mission_machine.planning.metrics import compute_metrics
    from mission_machine.simulation.simulator import Simulator

    mission = load_mission(args.mission_id)
    engine = PlanningEngine(mission)
    plan = engine.generate_options()
    min_runs = args.min_run or [0.0, 2.0, 3.0, 4.0, 6.0]

    banner("DISPATCH RULES - CAN THEY CLOSE THE GAP WITHOUT A SOLVER?")
    disclaimer()
    print(
        "\n  The shipped rule starts a set only when it is needed and runs it hard. The rule\n"
        "  measured here adds two clauses an operator can predict:\n"
        "    1. stop the set as soon as the battery can carry the node, not merely do not\n"
        "       start one;\n"
        "    2. never start a *second* set just to refill the battery - a recharge is worth\n"
        "       loading a running machine harder, not another machine's no-load fuel.\n"
        "  A third clause, a minimum run time, is what stops the first one cycling the set.\n"
    )

    def measure(configuration, mode, min_run):
        candidate = replace(
            configuration,
            policy=replace(configuration.policy, generator_mode=mode, min_run_hours=min_run),
        )
        result = Simulator(mission, engine.environment, engine.inventory).run(
            candidate, end_hour=mission.mission_duration_h
        )
        metrics = compute_metrics(mission, candidate, result, engine.inventory)
        starts, running = 0, False
        for step in result.steps:
            now = sum(step.generator_kw.values()) > 0.01
            starts += 1 if (now and not running) else 0
            running = now
        return metrics, starts

    for option in plan.options:
        configuration = option.configuration
        if not configuration.policy.generator_ids:
            continue
        base, base_starts = measure(configuration, GeneratorMode.CYCLED, 0.0)
        section(option.label)
        print(
            f"  as shipped         {base.fuel_consumption_l:8.1f} L  {base_starts:>3} starts  "
            f"reserve {base.energy_reserve_hours_min:5.1f} h  critical "
            f"{base.critical_load_coverage:.4f}"
        )
        for min_run in min_runs:
            metrics, starts = measure(configuration, GeneratorMode.COAST, min_run)
            share = (
                (metrics.fuel_consumption_l - base.fuel_consumption_l)
                / base.fuel_consumption_l * 100
                if base.fuel_consumption_l
                else 0.0
            )
            print(
                f"  stop + min run {min_run:>2.0f} h {metrics.fuel_consumption_l:8.1f} L "
                f"({share:+5.1f} %)  {starts:>3} starts  "
                f"reserve {metrics.energy_reserve_hours_min:5.1f} h  critical "
                f"{metrics.critical_load_coverage:.4f}"
            )

    section("WHAT THIS DOES AND DOES NOT SETTLE")
    print(
        "  The fuel is measured. The starts are counted and not costed: this model prices a\n"
        "  start at the fuel burned in the step it happens, and at nothing else (AS-026).\n"
        "  That is why the rule is offered and not adopted - publishing the saving while its\n"
        "  price sits outside the model would be the wrong way round. See RQ-015."
    )
    if args.json:
        print(json.dumps({"mission_id": mission.mission_id, "min_runs": min_runs}, indent=2))
    return 0


def cmd_depends(args) -> int:
    """What depends on what, and what a failure does to it (RQ-005)."""

    from mission_machine.resilience.dependencies import (
        DEFAULT_PROPAGATION_REGISTRY,
        DependencyKind,
        build_graph,
    )

    mission = load_mission(args.mission_id)
    session = OperationsSession(mission)
    plan = session.generate_options()
    recommendation = build_recommendation(plan, session.engine)
    chosen = args.configuration or recommendation.recommended_configuration_id
    session.select(chosen, recommended=recommendation.recommended_configuration_id)

    graph = build_graph(mission, session.engine.inventory)

    banner("DEPENDENCIES - WHAT A FAILURE DOES, AND BY WHAT MECHANISM")
    disclaimer()
    print(
        "\n  Pack 1 could say a function was not supported. It could not say what it was\n"
        "  short of, because the only evidence was a number that came out zero. Every edge\n"
        "  below carries a capacity as well as a state, which is what makes a shortfall\n"
        "  distinguishable from an outage.\n"
    )

    section("THE GRAPH")
    print(f"  {len(graph.nodes)} nodes, {len(graph.edges)} edges, derived from the asset set.")
    for kind in DependencyKind:
        edges = [e for e in graph.edges if e.kind is kind]
        if not edges:
            continue
        print(f"\n  {str(kind):<11} {len(edges)} edges")
        for edge in edges:
            need = (
                "any one of them"
                if edge.required_fraction <= 0.0
                else f"needs {edge.required_fraction:.0%} of it"
            )
            print(f"    {edge.dependant:<12} <- {edge.provider:<12} {need}")
    for note in graph.notes:
        print(f"\n  NOTE  {note}")

    if not args.scenario:
        section("BACKENDS")
        for descriptor in DEFAULT_PROPAGATION_REGISTRY.status_report():
            mark = "wired" if descriptor.available else "declared, not wired"
            print(f"  {descriptor.name:<8} {mark:<20} {descriptor.detail}")
        if args.json:
            print(json.dumps(graph.to_dict(), indent=2))
        return 0

    scenario = SCENARIOS[args.scenario]
    report = session.inject(scenario, regenerate=False)
    propagation = report.propagation

    section(f"{scenario.scenario_id} - {scenario.name}")
    when = f"H+{propagation.at_hour:.0f}" if propagation.at_hour is not None else "now"
    if propagation.projected:
        print(
            f"  The worst hour in the projection from H+{report.event_hour:.0f} is {when}.\n"
            "  At the moment a set fails nothing is short yet, so propagating on the present\n"
            "  says nothing is unmet and is useless. This is a forecast, and it is labelled\n"
            "  one rather than read as a statement about the hour the node is in."
        )
    else:
        print(f"  Observed at {when}.")

    if not report.mechanism:
        print(
            "\n  Nothing the mission calls critical is short of anything at that hour. That is\n"
            "  an answer, not an empty result: the remaining sources carry the node."
        )
    else:
        print()
        for line in report.mechanism:
            print(f"  {line}")

    stopped = [c.asset_id for c in propagation.stopped()]
    short = [f"{c.asset_id} at {c.supported_fraction:.0%}" for c in propagation.short()]
    section("SUMMARY")
    print(f"  stopped   {', '.join(stopped) if stopped else 'none'}")
    print(f"  short     {', '.join(short) if short else 'none'}")
    print(f"  unknown   {', '.join(propagation.unknown) if propagation.unknown else 'none'}")
    print(
        "\n  What this does not do: propagation reports that a dependency is unmet. It does\n"
        "  not simulate the consequence - nothing here makes the communications load trip on\n"
        "  temperature, and the dispatch is unchanged by any of it (AS-027). The node's\n"
        "  physics stay in the simulator, where they can be checked."
    )
    if args.json:
        print(json.dumps(propagation.to_dict(), indent=2))
    return 0


def cmd_handover(args) -> int:
    """What a premise alarm looks like across a shift boundary (RQ-018)."""

    from mission_machine.explainability.explain import build_recommendation

    mission = load_mission(args.mission_id)
    engine = PlanningEngine(mission)
    windows = engine.environment.grid.available_windows
    realised = engine.environment.with_grid_windows(
        [list(windows[0])], name="observed", note="Host-nation supply never returned."
    )
    session = OperationsSession(mission, engine, realised_environment=realised)

    banner("SHIFT HANDOVER - WHAT THE NEXT WATCH IS TOLD")
    disclaimer()
    print(
        "\n  A premise alarm is a state, not an event, and a 72-hour rotation has shift\n"
        "  boundaries in it. This is the same contradicted premise as `premise`, followed\n"
        "  through one handover.\n"
    )

    plan = session.generate_options(with_resilience=False)
    recommendation = build_recommendation(plan, engine, include_sensitivity=False)
    session.select(
        recommendation.recommended_configuration_id,
        recommended=recommendation.recommended_configuration_id,
    )

    first_raised: float | None = None
    interruptions = 0
    hour = 1.0
    while hour <= args.handover_at:
        session.run_to(hour)
        report = session.check_premises()
        if report.raised:
            interruptions += 1
            if first_raised is None:
                first_raised = hour
                section(f"H+{hour:.0f} - THE PANEL SPEAKS")
                consequence = report.raised[0]
                print(f"  {consequence.breach.premise.key}: {consequence.breach.evidence[0]}")
                for line in consequence.matters_because:
                    print(f"    - {line}")
                print(f"\n  REVISION OFFERED: {consequence.breach.revision_statement}")
                print(
                    "\n  The outgoing shift reads it and decides to wait for the next "
                    "resupply window\n  before replanning. That decision is recorded."
                )
                session.dismiss_premise_revision(
                    consequence.breach.premise.key,
                    rationale=args.rationale,
                )
        hour += 1.0

    section(f"H+{first_raised or 0:.0f} TO H+{args.handover_at:.0f} - WHAT THE PANEL DID NEXT")
    print(
        f"  The premise stayed contradicted for every one of those hours. The panel raised\n"
        f"  it {interruptions} time(s) and carried it as STANDING for the rest, because saying the\n"
        f"  same true thing every hour is how an operator learns to stop reading it."
    )

    brief = session.handover(outgoing=args.outgoing, incoming=args.incoming)
    section(f"HANDOVER AT H+{brief.at_hour:.0f} - {args.outgoing} TO {args.incoming}")
    if brief.assessment:
        print(f"  Mission status        : {brief.assessment.status}")
        print(f"  Assured support       : {brief.assessment.endurance_remaining_h:.0f} h of "
              f"{brief.assessment.mission_remaining_h:.0f} h remaining")
        print(f"  Fuel remaining        : {brief.assessment.fuel_remaining_l:.0f} L")

    print("\n  DECIDED AND CARRIED (the outgoing shift knew about these):")
    for alarm in brief.carried_decisions:
        print(
            f"    {alarm.key}: raised H+{alarm.first_raised_hour:.0f}, "
            f"stood for {brief.at_hour - alarm.first_raised_hour:.0f} h"
        )
        print(f"      Outgoing shift: \"{alarm.dismissed_rationale}\" (H+{alarm.dismissed_at_hour:.0f})")
    if not brief.carried_decisions:
        print("    none")

    print("\n  OPEN - NOBODY HAS DECIDED THESE:")
    for alarm in brief.open_decisions:
        print(f"    {alarm.key}: raised H+{alarm.first_raised_hour:.0f}, no decision recorded")
    if not brief.open_decisions:
        print("    none")

    print("\n  DECISION LOG CARRIED ACROSS:")
    for decision in brief.decisions:
        print(f"    H+{decision.at_hour:>4.0f}  {decision.decision:<26} {decision.rationale[:44]}")

    print(
        "\n  What the machine does not do: tell the incoming shift what to do about any of\n"
        "  it. The brief is assembled from the record. The decision is still theirs."
    )

    if args.accept:
        section("THE INCOMING SHIFT DECIDES DIFFERENTLY")
        key = brief.carried_decisions[0].key if brief.carried_decisions else None
        if key is None:
            print("  Nothing standing to decide.")
            return 0
        session.accept_premise_revision(
            key, rationale=f"{args.incoming}: replanning on the observed premise."
        )
        replan = session.generate_options(with_resilience=False)
        for option in replan.options:
            print(
                f"  {option.configuration.configuration_id:<12} {option.label:<34} "
                f"fuel {option.metrics.fuel_consumption_l:5.0f} L | feasible: "
                f"{'YES' if option.feasible else 'NO'}"
            )
        print(
            "\n  Both decisions are in the log, with the shift that made each one. Neither\n"
            "  shift was wrong on what they could see; they saw different amounts of it."
        )
    if args.json:
        print(json.dumps(brief.to_dict(), indent=2))
    return 0


def _print_noted(report) -> None:
    """Contradicted premises that cross no line the mission states.

    Printed, quietly, rather than raised. RQ-017 measured what raising them
    costs; dropping them altogether would be the other mistake.
    """

    if not report.noted:
        return
    print()
    for consequence in report.noted:
        premise = consequence.breach.premise
        print(f"  NOTED (not raised) - {premise.key}: {consequence.breach.evidence[0]}")
        print(f"    {consequence.matters_because[-1]}")


def cmd_premise(args) -> int:
    """Has the world left the assumptions the plan is still working from? (RQ-016)"""

    from mission_machine.explainability.explain import build_recommendation

    mission = load_mission(args.mission_id)
    engine = PlanningEngine(mission)
    windows = engine.environment.grid.available_windows
    if args.grid_returns is None:
        realised_windows = [list(windows[0])]
        described = "never returns"
    else:
        realised_windows = [list(windows[0])] + [
            [windows[1][0] + args.grid_returns, windows[1][1]]
        ]
        described = f"returns {args.grid_returns:.0f} h late"
    realised = engine.environment.with_grid_windows(
        realised_windows, name="observed", note=f"Host-nation supply {described}."
    )
    session = OperationsSession(mission, engine, realised_environment=realised)

    banner("PREMISE CHECK - IS THE PLAN STILL WORKING FROM THE RIGHT WORLD?")
    disclaimer()
    print(f"\n  The mission states host-nation supply returns at H+{windows[1][0]:.0f}.")
    print(f"  In the world the node is living in, it {described}.")

    plan = session.generate_options(with_resilience=False)
    recommendation = build_recommendation(plan, engine, include_sensitivity=False)
    session.select(
        recommendation.recommended_configuration_id,
        recommended=recommendation.recommended_configuration_id,
    )
    session.run_to(args.at)

    section(f"MISSION PICTURE AT H+{session.current_hour:.0f}, ON THE PREMISE AS STATED")
    print_assessment(session.assess())

    report = session.check_premises()
    if report.clear:
        section("PREMISE CHECK")
        print("  Nothing the node has observed contradicts the mission's premises.")
        _print_noted(report)
        return 0

    for consequence in report.raised:
        breach = consequence.breach
        section(f"PREMISE CONTRADICTED - {breach.premise.key}")
        print(f"  The mission says: {breach.premise.statement}")
        print(f"  Source: {breach.premise.source}" + (
            f" ({breach.premise.assumption_id})" if breach.premise.assumption_id else ""
        ))
        print()
        for line in breach.evidence:
            print(f"  OBSERVED: {line}")
        print()
        print("  WHY IT MATTERS")
        for line in consequence.matters_because:
            print(f"    - {line}")
        print()
        print(f"  REVISION OFFERED: {breach.revision_statement}")
        if breach.conservative:
            print(
                "    Conservative on purpose: supply that has not appeared when it was due is "
                "not assumed to appear later."
            )
    _print_noted(report)
    print()
    print(f"  OPERATOR DECISION: {report.decision_prompt}")

    if args.accept:
        key = report.raised[0].breach.premise.key
        session.accept_premise_revision(
            key, rationale="Demonstration: operator accepts the revised premise."
        )
        section("OPERATOR ACCEPTED THE REVISION - REPLANNED ON WHAT IS ACTUALLY THERE")
        replan = session.generate_options(with_resilience=False)
        for option in replan.options:
            metrics = option.metrics
            print(
                f"  {option.configuration.configuration_id:<12} {option.label:<34} "
                f"fuel {metrics.fuel_consumption_l:5.0f} L | endurance "
                f"{metrics.endurance_hours:4.0f} h | feasible: "
                f"{'YES' if option.feasible else 'NO'}"
            )
        section("DECISION LOG")
        for decision in session.decisions:
            print(
                f"  H+{decision.at_hour:>4.0f}  {decision.decision:<26} {decision.rationale[:48]}"
            )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
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
    configure.add_argument(
        "--coast",
        action="store_true",
        help="let the search use the RQ-015 stop rule (saves fuel, costs generator starts)",
    )
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

    premise = sub.add_parser(
        "premise", help="check the plan's premises against what the node saw (RQ-016)"
    )
    premise.add_argument("--at", type=float, default=36.0, help="how far the node has run")
    premise.add_argument(
        "--grid-returns", type=float,
        help="hours late supply actually returns; omit for never",
    )
    premise.add_argument(
        "--accept", action="store_true", help="have the operator accept the revision and replan"
    )
    premise.set_defaults(func=cmd_premise)

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

    alarms = sub.add_parser(
        "alarms", help="what a false premise alarm costs, and a true one is worth (RQ-017)"
    )
    alarms.add_argument(
        "--grid-hours", type=float, default=GRID_BREACH_HOURS,
        help="consecutive hours of missing supply before the premise is contradicted",
    )
    alarms.add_argument(
        "--sweep", type=float, nargs="+",
        help="move that threshold and price each setting, e.g. --sweep 1 2 3 4 6",
    )
    alarms.add_argument(
        "--world", action="append", help="restrict to named worlds (repeatable)"
    )
    alarms.add_argument(
        "--load", action="store_true",
        help="count how often the panel speaks over a whole mission (RQ-018)",
    )
    alarms.set_defaults(func=cmd_alarms)

    handover = sub.add_parser(
        "handover", help="a premise alarm across a shift boundary (RQ-018)"
    )
    handover.add_argument("--handover-at", type=float, default=42.0, help="hour of the handover")
    handover.add_argument("--outgoing", default="WATCH A", help="outgoing shift")
    handover.add_argument("--incoming", default="WATCH B", help="incoming shift")
    handover.add_argument(
        "--rationale",
        default="Seen. Holding the stated premise until the next resupply window.",
        help="what the outgoing shift recorded",
    )
    handover.add_argument(
        "--accept", action="store_true", help="the incoming shift replans on the revision"
    )
    handover.set_defaults(func=cmd_handover)

    depends = sub.add_parser(
        "depends", help="what depends on what, and what a failure does to it (RQ-005)"
    )
    depends.add_argument("--configuration", help="configuration to select (default: recommended)")
    depends.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        help="disruption to propagate (default: print the graph only)",
    )
    depends.set_defaults(func=cmd_depends)

    rules = sub.add_parser(
        "rules", help="can the dispatch rules close the solver's gap without a solver? (RQ-015)"
    )
    rules.add_argument(
        "--min-run", type=float, action="append",
        help="minimum hours a set runs once started (repeatable; default 0 2 3 4 6)",
    )
    rules.set_defaults(func=cmd_rules)

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
