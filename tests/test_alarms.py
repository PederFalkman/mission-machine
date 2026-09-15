"""What a false premise alarm costs, and what a true one is worth (RQ-017).

Two things are being tested here and they pull in opposite directions. A panel
that says nothing is useless; a panel that cries wolf is worse than useless,
because the operator stops reading it before the alarm that matters. Every test
below is about the line between them - and about the harness that measures it
being one that could report a failure, rather than one built to agree.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.operations.alarms import (
    CORRECT_SILENCE,
    HARMFUL,
    NUISANCE,
    WORTH_RAISING,
    Outcome,
    World,
    compare_outcomes,
    demonstrator_worlds,
    differences,
    evaluate_world,
)
from mission_machine.operations.premises import Thresholds, check_premises
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.engine import PlanningEngine
from tests.test_premises import session_in


def outcome(critical=0.0, discretionary=100.0, fuel=400.0, arm="X") -> Outcome:
    return Outcome(
        arm=arm,
        premise="test",
        critical_shortfall_kwh=critical,
        discretionary_served_kwh=discretionary,
        fuel_used_l=fuel,
    )


class ComparisonTests(unittest.TestCase):
    """The comparison decides every verdict, so its order has to be the mission's."""

    def test_critical_service_outranks_everything_else(self) -> None:
        serves_critical = outcome(critical=0.0, discretionary=0.0, fuel=520.0)
        saves_fuel = outcome(critical=50.0, discretionary=200.0, fuel=100.0)
        self.assertEqual(compare_outcomes(serves_critical, saves_fuel), "BETTER")

    def test_discretionary_service_outranks_fuel(self) -> None:
        serves = outcome(discretionary=150.0, fuel=450.0)
        frugal = outcome(discretionary=50.0, fuel=300.0)
        self.assertEqual(compare_outcomes(serves, frugal), "BETTER")

    def test_a_difference_inside_the_deadband_is_not_a_difference(self) -> None:
        """Without a deadband the harness would report rounding as a result."""

        self.assertEqual(compare_outcomes(outcome(fuel=400.0), outcome(fuel=402.0)), "SAME")
        self.assertEqual(compare_outcomes(outcome(fuel=400.0), outcome(fuel=410.0)), "BETTER")

    def test_comparison_is_antisymmetric(self) -> None:
        better, worse = outcome(fuel=300.0), outcome(fuel=400.0)
        self.assertEqual(compare_outcomes(better, worse), "BETTER")
        self.assertEqual(compare_outcomes(worse, better), "WORSE")

    def test_differences_name_the_dimension_and_the_number(self) -> None:
        lines = differences(outcome(discretionary=20.0, fuel=360.0), outcome())
        self.assertTrue(any("discretionary service -80 kWh" in line for line in lines))
        self.assertTrue(any("fuel -40 L" in line for line in lines))


class DetectorTests(unittest.TestCase):
    """The defects RQ-017 found in the RQ-016 detectors, kept fixed."""

    def test_supply_that_came_back_is_not_a_contradicted_premise(self) -> None:
        """Two isolated one-hour dropouts, supply present since: nothing to say.

        The first version of this detector counted every missing hour ever
        observed, so two unrelated blinks read as a contradicted premise and
        offered to write host-nation supply off for the rest of the mission.
        Measured cost of that mistake: 68 kWh of discretionary service given up
        for nothing.
        """

        session = session_in(
            [[0.0, 6.0], [7.0, 10.0], [11.0, 14.0], [30.0, 44.0]], run_to=14.0
        )
        report = session.check_premises()
        keys = [c.breach.premise.key for c in report.consequences]
        self.assertNotIn("GRID_AVAILABILITY", keys)

    def test_an_ongoing_absence_is_dated_from_the_run_that_breached(self) -> None:
        """Not from the first hour that ever went missing, which may be ancient."""

        session = session_in([[0.0, 6.0], [7.0, 14.0]], run_to=34.0)
        report = session.check_premises()
        breach = next(
            c.breach for c in report.consequences if c.breach.premise.key == "GRID_AVAILABILITY"
        )
        self.assertIn("H+30", breach.revision_statement)
        self.assertNotIn("H+6", breach.revision_statement)

    def test_a_run_may_not_span_the_gap_between_two_supply_windows(self) -> None:
        """One absent hour either side of an unpromised gap is two absences.

        The hours between the windows were never promised, so joining across
        them would recreate the defect above in a different shape: a long
        enough "run" assembled out of unrelated hours.
        """

        session = session_in([[0.0, 13.0], [30.0, 31.0], [32.0, 44.0]], run_to=32.0)
        breaches = check_premises(
            session.mission, session.planned_environment, session.observed, 32.0,
            Thresholds(grid_hours=2.0),
        )
        self.assertEqual(
            [b.premise.key for b in breaches if b.premise.key == "GRID_AVAILABILITY"], []
        )

    def test_the_threshold_is_a_parameter_and_moving_it_changes_detection(self) -> None:
        session = session_in([[0.0, 14.0], [33.0, 44.0]], run_to=32.0)
        self.assertTrue(
            check_premises(
                session.mission, session.planned_environment, session.observed, 32.0,
                Thresholds(grid_hours=2.0),
            )
        )
        self.assertFalse(
            check_premises(
                session.mission, session.planned_environment, session.observed, 32.0,
                Thresholds(grid_hours=4.0),
            )
        )

    def test_detection_never_looks_past_the_hour_it_is_asked_about(self) -> None:
        """The operator cannot see the future, so neither may the detector."""

        session = session_in([[0.0, 14.0]], run_to=40.0)
        early = check_premises(
            session.mission, session.planned_environment, session.observed[:30], 30.0
        )
        self.assertEqual(early, [])


class MaterialityTests(unittest.TestCase):
    """A contradiction that crosses no stated line is noted, not raised."""

    def test_a_contradiction_that_changes_nothing_is_not_raised(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        overcast = engine.environment.variant(name="overcast", cloud_scale=0.35)
        session = OperationsSession(mission, engine, realised_environment=overcast)
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        session.run_to(12.0)

        report = session.check_premises()
        keys = [c.breach.premise.key for c in report.consequences]
        self.assertIn("WEATHER_YIELD", keys, "the yield premise really is contradicted")
        self.assertTrue(report.clear, "but it crosses no line the mission states")
        self.assertEqual([c.breach.premise.key for c in report.noted], ["WEATHER_YIELD"])

    def test_a_noted_premise_is_demoted_and_not_hidden(self) -> None:
        """The operator can still see it, and can still choose to plan on it."""

        mission = load_mission()
        engine = PlanningEngine(mission)
        overcast = engine.environment.variant(name="overcast", cloud_scale=0.35)
        session = OperationsSession(mission, engine, realised_environment=overcast)
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        session.run_to(12.0)

        noted = session.check_premises().noted
        self.assertTrue(noted)
        self.assertTrue(noted[0].matters_because)
        self.assertIn("noted rather than raised", noted[0].matters_because[-1])
        session.accept_premise_revision("WEATHER_YIELD")
        self.assertEqual(
            [decision.decision for decision in session.decisions][-1],
            "ACCEPT_PREMISE_REVISION",
        )

    def test_a_contradiction_that_puts_the_mission_at_risk_is_raised(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=36.0)
        report = session.check_premises()
        self.assertFalse(report.clear)
        self.assertEqual([c.breach.premise.key for c in report.raised], ["GRID_AVAILABILITY"])


class HarnessTests(unittest.TestCase):
    """The measurement itself: could it report a failure if there were one?"""

    def test_the_worlds_include_ones_where_the_premise_is_right(self) -> None:
        """A harness of catastrophes only would report the detectors as flawless."""

        mission = load_mission()
        keys = {world.key for world in demonstrator_worlds(mission)}
        self.assertIn("as-forecast", keys)
        self.assertIn("grid-flicker", keys)
        self.assertIn("load-noisy", keys)

    def test_the_noisy_world_draws_the_stated_energy_over_the_mission(self) -> None:
        """Only the hour-to-hour shape differs - there is nothing to report."""

        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "load-noisy")
        hours = range(int(mission.mission_duration_h))
        stated = sum(
            mission.inventory.find(load_id).profile.demand_kw(
                float(h), engine.environment.temperature_c(float(h))
            )
            for load_id in mission.critical_loads
            for h in hours
        )
        noisy = sum(
            world.inventory.find(load_id).profile.demand_kw(
                float(h), engine.environment.temperature_c(float(h))
            )
            for load_id in mission.critical_loads
            for h in hours
        )
        self.assertAlmostEqual(noisy / stated, 1.0, places=6)

    def test_the_world_the_mission_describes_raises_nothing(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "as-forecast")
        case = evaluate_world(mission, world, engine=engine)
        self.assertFalse(case.fired)
        self.assertEqual(case.verdict, CORRECT_SILENCE)

    def test_supply_that_never_returns_is_worth_raising(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "grid-never")
        case = evaluate_world(mission, world, engine=engine)
        self.assertTrue(case.fired)
        self.assertEqual(case.verdict, WORTH_RAISING)
        self.assertEqual(case.captured, "BETTER")
        self.assertEqual(set(case.outcomes), {"IGNORE", "ACCEPT", "TRUTH"})

    def test_the_verdict_comes_from_outcomes_not_from_a_label(self) -> None:
        """No world carries a hand-written "the premise really changed" flag.

        If it did, the harness would be the author marking their own homework.
        Whether there was anything worth saying is computed, by asking what
        planning on the truth would have been worth.
        """

        world = World("x", "y", PlanningEngine(load_mission()).environment)
        self.assertEqual(
            set(vars(world)), {"key", "description", "environment", "inventory"}
        )

    def test_raising_the_threshold_never_makes_an_alarm_come_sooner(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "grid-never")
        early = evaluate_world(mission, world, Thresholds(grid_hours=2.0), engine=engine)
        late = evaluate_world(mission, world, Thresholds(grid_hours=4.0), engine=engine)
        self.assertIsNotNone(early.detected_at_hour)
        self.assertIsNotNone(late.detected_at_hour)
        self.assertGreaterEqual(late.detected_at_hour, early.detected_at_hour)

    def test_a_transient_dropout_is_priced_rather_than_waved_away(self) -> None:
        """The case the harness exists to catch: an alarm that costs more than silence."""

        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "grid-blink")
        case = evaluate_world(mission, world, Thresholds(grid_hours=2.0), engine=engine)
        self.assertTrue(case.fired)
        self.assertIn(case.verdict, {HARMFUL, NUISANCE})
        if case.verdict == HARMFUL:
            self.assertTrue(case.cost_of_acting)


if __name__ == "__main__":
    unittest.main()
