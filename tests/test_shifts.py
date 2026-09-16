"""A premise alarm across the hours and shifts of a rotation (RQ-018).

RQ-017 asked whether the first alarm of a world was worth raising. These tests
are about every hour after it. The thing being defended is an operator's
attention: a contradiction that is still true is not news the second time, an
alarm that stops appearing has to say why it stopped, and a shift that decided
to wait has to leave a record the next shift can read.

None of this answers RQ-018, which needs people. It removes the confound that
would have dominated any study of them, and builds the instrumentation such a
study would need.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.operations.alarms import demonstrator_worlds, measure_alarm_load
from mission_machine.operations.premises import Premise, PremiseBreach
from mission_machine.operations.session import (
    NOTED,
    RAISED,
    STANDING,
    OperationsError,
    OperationsSession,
    PremiseConsequence,
)
from mission_machine.planning.engine import PlanningEngine
from tests.test_premises import session_in


def consequence_crossing(*crossed: str) -> PremiseConsequence:
    """A consequence that crosses exactly the named lines, and nothing else."""

    breach = PremiseBreach(
        premise=Premise(key="TEST_PREMISE", statement="x", source="test"),
        detected_at_hour=0.0,
        severity="CONTRADICTED",
    )
    return PremiseConsequence(breach=breach, crossed=tuple(crossed))


class StandingTests(unittest.TestCase):
    """The same true thing, said once."""

    def test_a_contradiction_still_true_an_hour_later_is_not_raised_again(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=33.0)
        self.assertEqual([c.state for c in session.check_premises().consequences], [RAISED])

        session.run_to(34.0)
        report = session.check_premises()
        self.assertEqual([c.state for c in report.consequences], [STANDING])
        self.assertTrue(report.clear, "nothing new to interrupt anybody with")
        self.assertTrue(report.standing, "but it is still on the panel")

    def test_asking_twice_in_the_same_hour_gives_the_same_answer(self) -> None:
        """Refreshing a screen is not an event, and must not consume the alarm."""

        session = session_in([[0.0, 14.0]], run_to=33.0)
        first = session.check_premises()
        second = session.check_premises()
        self.assertEqual(
            [c.state for c in first.consequences], [c.state for c in second.consequences]
        )
        self.assertEqual([c.state for c in second.consequences], [RAISED])

    def test_a_standing_alarm_is_raised_again_when_it_crosses_a_new_line(self) -> None:
        """Suppression is only safe if getting worse still speaks up.

        Driven through the state machine directly: no bundled world reaches
        this branch, because the worlds that raise at all cross everything they
        are going to cross in the first hour. That is a property of these
        twelve worlds, not of the rule, so the branch is covered by
        construction rather than left untested.
        """

        session = session_in([[0.0, 14.0], [30.0, 44.0]], run_to=10.0)
        self.assertEqual(session._alarm_state(consequence_crossing("reserve-requirement")), RAISED)

        session.run_to(11.0)
        self.assertEqual(
            session._alarm_state(consequence_crossing("reserve-requirement")), STANDING
        )

        session.run_to(12.0)
        self.assertEqual(
            session._alarm_state(
                consequence_crossing("reserve-requirement", "critical:COMMS-01")
            ),
            RAISED,
        )
        self.assertEqual(session.premise_alarms["TEST_PREMISE"].times_raised, 2)

    def test_a_consequence_that_shrinks_does_not_raise_again(self) -> None:
        session = session_in([[0.0, 14.0], [30.0, 44.0]], run_to=10.0)
        session._alarm_state(consequence_crossing("reserve-requirement", "assured-support"))
        session.run_to(11.0)
        self.assertEqual(
            session._alarm_state(consequence_crossing("reserve-requirement")), STANDING
        )

    def test_an_immaterial_contradiction_is_never_an_alarm_at_all(self) -> None:
        session = session_in([[0.0, 14.0], [30.0, 44.0]], run_to=10.0)
        self.assertEqual(session._alarm_state(consequence_crossing()), NOTED)
        self.assertNotIn("TEST_PREMISE", session.premise_alarms)


class ResolutionTests(unittest.TestCase):
    """An alarm that stops appearing has to say why it stopped."""

    def test_a_premise_the_world_comes_back_to_is_reported_resolved(self) -> None:
        session = session_in([[0.0, 14.0], [36.0, 44.0]], run_to=33.0)
        self.assertTrue(session.check_premises().raised)

        for hour in (34.0, 35.0, 36.0, 37.0, 38.0):
            session.run_to(hour)
            report = session.check_premises()
            if report.resolved:
                self.assertEqual(report.resolved[0].key, "GRID_AVAILABILITY")
                self.assertIn("agrees with the mission", report.resolved[0].resolution_reason)
                break
        else:
            self.fail("the premise was never reported resolved")

    def test_resolution_is_reported_once_and_not_every_hour_after(self) -> None:
        session = session_in([[0.0, 14.0], [36.0, 44.0]], run_to=33.0)
        session.check_premises()
        seen = 0
        for hour in range(34, 50):
            session.run_to(float(hour))
            seen += len(session.check_premises().resolved)
        self.assertEqual(seen, 1)

    def test_an_alarm_that_stops_mattering_says_so_rather_than_vanishing(self) -> None:
        """The premise is still contradicted; what it was about is behind the node.

        Found by RQ-018: the grid alarm went quiet at H+44 - the hour the
        mission stops promising supply - with no word to anybody. An operator
        cannot tell that from "it is fixed".
        """

        session = session_in([[0.0, 14.0]], run_to=33.0)
        session.check_premises()
        reasons = []
        for hour in range(34, 60):
            session.run_to(float(hour))
            reasons += [alarm.resolution_reason for alarm in session.check_premises().resolved]
        self.assertEqual(len(reasons), 1)
        self.assertIn("no longer crosses any line", reasons[0])

    def test_a_premise_that_fails_twice_raises_twice(self) -> None:
        """The second and third alarm of a rotation, which is what RQ-018 asks about."""

        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "grid-twice")
        load = measure_alarm_load(mission, world, engine=engine)
        self.assertEqual(load.interruptions, 2)
        self.assertEqual(load.resolutions, 2)
        self.assertGreater(load.raised_at[1], load.raised_at[0])


class DismissalTests(unittest.TestCase):
    """'We saw it and decided to wait' is evidence. Its absence is guesswork."""

    def test_dismissing_records_a_decision_and_keeps_the_alarm_standing(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=33.0)
        session.check_premises()
        session.dismiss_premise_revision("GRID_AVAILABILITY", "Waiting for resupply.")

        decisions = [d.decision for d in session.decisions]
        self.assertIn("DISMISS_PREMISE_REVISION", decisions)

        session.run_to(34.0)
        report = session.check_premises()
        self.assertTrue(report.standing, "dismissal is not a mute button")
        self.assertTrue(session.premise_alarms["GRID_AVAILABILITY"].dismissed)

    def test_dismissing_does_not_change_what_the_planner_plans_against(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=33.0)
        before = session.planned_environment
        session.check_premises()
        session.dismiss_premise_revision("GRID_AVAILABILITY")
        self.assertIs(session.planned_environment, before)
        self.assertEqual(session.premise_revisions, [])

    def test_a_dismissed_alarm_is_raised_again_if_it_crosses_a_new_line(self) -> None:
        session = session_in([[0.0, 14.0], [30.0, 44.0]], run_to=10.0)
        session._alarm_state(consequence_crossing("reserve-requirement"))
        session.dismiss_premise_revision("TEST_PREMISE", "Noted, holding.")
        session.run_to(11.0)
        self.assertEqual(
            session._alarm_state(
                consequence_crossing("reserve-requirement", "status:DEGRADED")
            ),
            RAISED,
        )

    def test_dismissing_something_never_raised_is_refused(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=33.0)
        with self.assertRaises(OperationsError):
            session.dismiss_premise_revision("NO_SUCH_PREMISE")


class HandoverTests(unittest.TestCase):
    """What one watch hands the next."""

    def setUp(self) -> None:
        self.session = session_in([[0.0, 14.0]], run_to=33.0)
        self.session.check_premises()

    def test_the_brief_separates_what_was_decided_from_what_is_open(self) -> None:
        self.session.dismiss_premise_revision("GRID_AVAILABILITY", "Holding until resupply.")
        self.session.run_to(42.0)
        self.session.check_premises()
        brief = self.session.handover(outgoing="WATCH A", incoming="WATCH B")

        self.assertEqual([a.key for a in brief.carried_decisions], ["GRID_AVAILABILITY"])
        self.assertEqual(brief.open_decisions, [])
        self.assertEqual(brief.carried_decisions[0].dismissed_rationale, "Holding until resupply.")

    def test_an_undecided_alarm_is_handed_over_as_open(self) -> None:
        self.session.run_to(42.0)
        self.session.check_premises()
        brief = self.session.handover(outgoing="WATCH A", incoming="WATCH B")
        self.assertEqual([a.key for a in brief.open_decisions], ["GRID_AVAILABILITY"])
        self.assertEqual(brief.carried_decisions, [])

    def test_the_handover_is_itself_in_the_decision_log(self) -> None:
        self.session.handover(outgoing="WATCH A", incoming="WATCH B")
        last = self.session.decisions[-1]
        self.assertEqual(last.decision, "HANDOVER")
        self.assertIn("WATCH A", last.rationale)
        self.assertIn("WATCH B", last.rationale)

    def test_the_brief_carries_the_decisions_the_outgoing_shift_made(self) -> None:
        self.session.dismiss_premise_revision("GRID_AVAILABILITY", "Holding.")
        brief = self.session.handover()
        kinds = [d.decision for d in brief.decisions]
        self.assertIn("SELECT_CONFIGURATION", kinds)
        self.assertIn("DISMISS_PREMISE_REVISION", kinds)

    def test_the_brief_never_tells_the_incoming_shift_what_to_do(self) -> None:
        """It is assembled from the record. The decision stays with the people."""

        brief = self.session.handover()
        payload = brief.to_dict()
        self.assertNotIn("recommendation", payload)
        self.assertNotIn("recommended_action", payload)
        self.assertIn("SYNTHETIC", payload["data_labels"])


class AlarmLoadTests(unittest.TestCase):
    """The measurement that found the problem in the first place."""

    def test_a_contradiction_lasting_hours_interrupts_once(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "grid-never")
        load = measure_alarm_load(mission, world, engine=engine)

        self.assertEqual(load.interruptions, 1)
        self.assertGreater(load.contradiction_hours, 5)
        self.assertEqual(
            load.repeats_avoided, load.contradiction_hours - load.interruptions
        )

    def test_a_world_that_matches_the_premise_never_speaks(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        world = next(w for w in demonstrator_worlds(mission, engine) if w.key == "as-forecast")
        load = measure_alarm_load(mission, world, engine=engine)
        self.assertEqual(load.interruptions, 0)
        self.assertEqual(load.contradiction_hours, 0)


if __name__ == "__main__":
    unittest.main()
