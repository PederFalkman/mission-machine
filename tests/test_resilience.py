"""Single points of failure, recovery options and degraded-mode reconfiguration."""

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.engine import PlanningEngine
from mission_machine.resilience.analysis import ResilienceAnalyst
from mission_machine.resilience.failures import (
    GENERATOR_B_AND_GRID_LOSS,
    GENERATOR_B_UNAVAILABLE,
    FailureEvent,
    Scenario,
)


class FailureModelTests(unittest.TestCase):
    def test_event_activity_window(self) -> None:
        event = FailureEvent(event_id="E", asset_id="X", hour=10.0, restored_hour=20.0)
        self.assertFalse(event.active_at(9.0))
        self.assertTrue(event.active_at(10.0))
        self.assertFalse(event.active_at(20.0))

    def test_mandated_scenario_is_generator_b_at_h30(self) -> None:
        self.assertEqual(GENERATOR_B_UNAVAILABLE.scenario_id, "SC-DEGRADED-001")
        self.assertEqual(GENERATOR_B_UNAVAILABLE.events[0].asset_id, "GEN-B")
        self.assertEqual(GENERATOR_B_UNAVAILABLE.events[0].hour, 30.0)

    def test_scenarios_model_consequence_not_cause(self) -> None:
        for scenario in (GENERATOR_B_UNAVAILABLE, GENERATOR_B_AND_GRID_LOSS):
            for event in scenario.events:
                self.assertTrue(event.cause.startswith("UNSPECIFIED"))


class ResilienceAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()
        cls.analyst = ResilienceAnalyst(cls.engine)
        cls.option = cls.plan.options[0]
        cls.impacts = cls.analyst.single_points_of_failure(
            cls.option.configuration, baseline=cls.option.metrics
        )

    def test_power_conversion_is_a_single_point_of_failure(self) -> None:
        by_asset = {impact.asset_id: impact for impact in self.impacts}
        self.assertIn("PCE-01", by_asset)
        self.assertTrue(by_asset["PCE-01"].is_single_point_of_failure)
        self.assertEqual(by_asset["PCE-01"].endurance_hours, 0.0)

    def test_every_active_supply_asset_is_probed(self) -> None:
        probed = {impact.asset_id for impact in self.impacts}
        for asset_id in self.option.configuration.policy.generator_ids:
            self.assertIn(asset_id, probed)

    def test_recovery_options_are_quantified(self) -> None:
        options = self.analyst.recovery_options(
            self.option.configuration, baseline=self.option.metrics
        )
        self.assertTrue(options)
        for option in options:
            payload = option.to_dict()
            for key in ("endurance_delta_h", "fuel_delta_l", "reserve_delta_h", "cost_note"):
                self.assertIn(key, payload)
            self.assertTrue(payload["cost_note"], f"{option.option_id} has no stated cost")

    def test_degrading_cooling_saves_fuel(self) -> None:
        options = self.analyst.recovery_options(
            self.option.configuration, baseline=self.option.metrics
        )
        degrade = [o for o in options if o.option_id.startswith("REC-DEGRADE")]
        self.assertTrue(degrade, "no degraded-setpoint option offered")
        self.assertLess(degrade[0].fuel_delta_l, 0.0)


class DegradedModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.session = OperationsSession(load_mission())
        plan = cls.session.generate_options()
        cls.session.select(plan.options[0].configuration.configuration_id)
        cls.report = cls.session.inject(GENERATOR_B_UNAVAILABLE)

    def test_report_answers_the_four_operator_questions(self) -> None:
        self.assertTrue(self.report.what_changed)
        self.assertTrue(self.report.why_it_matters)
        self.assertIsNotNone(self.report.options)
        self.assertTrue(self.report.comparison)
        self.assertTrue(self.report.operator_decision_required)

    def test_the_failed_asset_is_named_in_what_changed(self) -> None:
        self.assertTrue(any("GEN-B" in line for line in self.report.what_changed))

    def test_reconfiguration_options_exclude_the_failed_asset(self) -> None:
        for option in self.report.options.options:
            self.assertNotIn("GEN-B", option.configuration.policy.generator_ids)
        self.assertIn("GEN-B", self.report.options.excluded_assets)

    def test_redundancy_loss_is_reported(self) -> None:
        before = self.report.assessment_before.metrics.n_minus_1_ride_through_h
        after = self.report.assessment_after.metrics.n_minus_1_ride_through_h
        self.assertLess(after, before)

    def test_critical_functions_survive_this_single_failure(self) -> None:
        self.assertEqual(self.report.assessment_after.functions_degraded, [])
        self.assertEqual(self.report.assessment_after.status, "ASSURED")

    def test_compound_failure_forces_a_degradation(self) -> None:
        session = OperationsSession(load_mission())
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        report = session.inject(GENERATOR_B_AND_GRID_LOSS)
        self.assertEqual(report.assessment_after.status, "DEGRADED")
        self.assertTrue(report.assessment_after.functions_degraded)
        restoring = [
            option for option in report.recovery_options
            if option["restores_critical_assurance"]
        ]
        self.assertTrue(
            restoring, "no recovery action restores critical assurance in the compound case"
        )

    def test_operator_override_is_recorded_as_such(self) -> None:
        session = OperationsSession(load_mission())
        plan = session.generate_options(with_resilience=False)
        session.select(
            plan.options[2].configuration.configuration_id,
            rationale="Operator prefers fewer assets.",
            recommended=plan.options[0].configuration.configuration_id,
        )
        decision = session.decisions[-1]
        self.assertFalse(decision.followed_recommendation)
        self.assertEqual(decision.rationale, "Operator prefers fewer assets.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
