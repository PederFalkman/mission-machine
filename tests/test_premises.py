"""Checking the plan's premises against what the node observed (RQ-016).

The capability this tests is not "plan better" but "notice that you are planning
against the wrong world". Two properties matter most and are asserted
repeatedly: detection uses only what has already been observed, and the machine
never adopts a revised premise by itself.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.operations.premises import (
    GRID_BREACH_HOURS,
    PROFILE_BREACH_FRACTION,
    check_premises,
)
from mission_machine.operations.session import OperationsError, OperationsSession
from mission_machine.planning.engine import PlanningEngine


def session_in(realised_windows, *, run_to: float = 36.0, mission=None):
    """A session whose node lives in a world with the given grid windows."""

    mission = mission or load_mission()
    engine = PlanningEngine(mission)
    realised = engine.environment.with_grid_windows(
        realised_windows, name="observed", note="Realised supply."
    )
    session = OperationsSession(mission, engine, realised_environment=realised)
    plan = session.generate_options(with_resilience=False)
    session.select(plan.options[0].configuration.configuration_id)
    if run_to:
        session.run_to(run_to)
    return session


class DetectionTests(unittest.TestCase):
    def test_a_world_that_matches_the_premise_raises_nothing(self) -> None:
        session = session_in([[0.0, 14.0], [30.0, 44.0]])
        report = session.check_premises()
        self.assertTrue(report.clear, [c.breach.premise.key for c in report.consequences])

    def test_supply_that_does_not_return_contradicts_the_premise(self) -> None:
        session = session_in([[0.0, 14.0]])
        report = session.check_premises()
        keys = [c.breach.premise.key for c in report.consequences]
        self.assertIn("GRID_AVAILABILITY", keys)
        breach = next(c.breach for c in report.consequences if c.breach.premise.key == "GRID_AVAILABILITY")
        self.assertEqual(breach.severity, "CONTRADICTED")
        self.assertTrue(breach.evidence)
        self.assertEqual(breach.premise.assumption_id, "AS-008")

    def test_a_brief_absence_is_not_a_contradicted_premise(self) -> None:
        """One missing hour is a glitch; the threshold exists so it reads as one."""

        session = session_in([[0.0, 14.0], [31.0, 44.0]], run_to=32.0)
        report = session.check_premises()
        self.assertTrue(
            report.clear,
            f"{GRID_BREACH_HOURS:.0f} h of absence is the stated threshold for a breach",
        )

    def test_detection_uses_only_what_has_been_observed(self) -> None:
        """Before the plan's window is due, nothing is known to be wrong."""

        session = session_in([[0.0, 14.0]], run_to=20.0)
        self.assertTrue(session.check_premises().clear)
        session.run_to(36.0)
        self.assertFalse(session.check_premises().clear)

    def test_a_heavier_load_than_stated_contradicts_its_profile(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        session = OperationsSession(mission, engine)
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        session.run_to(12.0)
        # The node drew more than the mission said it would.
        for step in session.observed:
            step.critical_demand_kw *= 1.0 + PROFILE_BREACH_FRACTION * 2
        breaches = check_premises(
            mission, session.planned_environment, session.observed, session.current_hour
        )
        keys = [b.premise.key for b in breaches]
        self.assertIn("CRITICAL_LOAD_PROFILE", keys)
        breach = next(b for b in breaches if b.premise.key == "CRITICAL_LOAD_PROFILE")
        self.assertIsNotNone(breach.revised_inventory)
        self.assertGreater(
            breach.revised_inventory.get("COMMS-01").profile.kw,
            mission.inventory.get("COMMS-01").profile.kw,
            "the revision must carry the observed load, not the stated one",
        )

    def test_nothing_is_reported_before_the_node_has_observed_anything(self) -> None:
        session = session_in([[0.0, 14.0]], run_to=0.0)
        self.assertTrue(session.check_premises().clear)


class ConsequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.session = session_in([[0.0, 14.0]])
        cls.report = cls.session.check_premises()
        cls.consequence = cls.report.consequences[0]

    def test_the_consequence_is_projected_on_both_premises(self) -> None:
        self.assertIsNotNone(self.consequence.assessment_on_stated_premise)
        self.assertIsNotNone(self.consequence.assessment_on_revised_premise)
        self.assertTrue(self.consequence.matters_because)

    def test_the_stated_premise_was_hiding_something(self) -> None:
        stated = self.consequence.assessment_on_stated_premise
        revised = self.consequence.assessment_on_revised_premise
        self.assertNotEqual(
            (stated.status, round(stated.endurance_remaining_h, 1)),
            (revised.status, round(revised.endurance_remaining_h, 1)),
            "this scenario exists because the premise hides a worse picture",
        )

    def test_the_report_asks_rather_than_decides(self) -> None:
        payload = self.report.to_dict()
        self.assertTrue(payload["operator_decision_required"])
        self.assertIn("will not revise", payload["decision_prompt"])
        self.assertIn("SYNTHETIC", payload["data_labels"])


class OperatorAuthorityTests(unittest.TestCase):
    def test_checking_a_premise_never_changes_what_the_planner_plans_against(self) -> None:
        session = session_in([[0.0, 14.0]])
        before = session.planned_environment.grid.available_windows
        session.check_premises()
        session.check_premises()
        self.assertEqual(
            session.planned_environment.grid.available_windows,
            before,
            "noticing is not deciding",
        )
        self.assertEqual(session.premise_revisions, [])

    def test_accepting_a_revision_moves_the_planner_onto_it(self) -> None:
        session = session_in([[0.0, 14.0]])
        session.accept_premise_revision("GRID_AVAILABILITY", rationale="Operator accepts.")
        self.assertEqual(
            session.planned_environment.grid.available_windows,
            [[0.0, 14.0]],
            "after acceptance the planner works from what the node actually saw",
        )
        self.assertTrue(session.check_premises().clear, "the breach is answered, not repeated")

    def test_accepting_a_revision_is_recorded_as_an_operator_decision(self) -> None:
        session = session_in([[0.0, 14.0]])
        session.accept_premise_revision("GRID_AVAILABILITY", rationale="Grid confirmed lost.")
        decision = session.decisions[-1]
        self.assertEqual(decision.decision, "ACCEPT_PREMISE_REVISION")
        self.assertEqual(decision.rationale, "Grid confirmed lost.")
        self.assertEqual(session.premise_revisions[0]["key"], "GRID_AVAILABILITY")
        self.assertIn("premise_revisions", session.status_dict())

    def test_a_premise_nobody_raised_cannot_be_accepted(self) -> None:
        session = session_in([[0.0, 14.0], [30.0, 44.0]])
        with self.assertRaises(OperationsError):
            session.accept_premise_revision("GRID_AVAILABILITY")

    def test_replanning_after_acceptance_uses_the_revised_premise(self) -> None:
        session = session_in([[0.0, 14.0]])
        before = session.generate_options(with_resilience=False)
        session.accept_premise_revision("GRID_AVAILABILITY")
        after = session.generate_options(with_resilience=False)
        self.assertGreater(
            max(o.metrics.grid_dependence for o in before.options),
            max(o.metrics.grid_dependence for o in after.options),
            "options planned on the revised premise cannot lean on supply that is not there",
        )


class PlanRealitySplitTests(unittest.TestCase):
    def test_projection_uses_the_premise_and_running_uses_the_world(self) -> None:
        """Projecting on the realised world would hand the operator a future they lack."""

        session = session_in([[0.0, 14.0]], run_to=0.0)
        self.assertEqual(
            session.projector.environment.grid.available_windows,
            [[0.0, 14.0], [30.0, 44.0]],
        )
        self.assertEqual(
            session.simulator.environment.grid.available_windows, [[0.0, 14.0]]
        )

    def test_a_session_with_no_realised_world_given_behaves_as_before(self) -> None:
        session = OperationsSession(load_mission())
        self.assertIs(session.planned_environment, session.realised_environment)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
