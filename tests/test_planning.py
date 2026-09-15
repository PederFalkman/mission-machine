"""Planning engine, metrics and the MILP formulation."""

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.planning import milp
from mission_machine.planning.configuration import SecondaryPolicy
from mission_machine.planning.engine import DEFAULT_STRATEGIES, PlanningEngine, Strategy


class OptionGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()

    def test_three_options_one_per_strategy(self) -> None:
        self.assertEqual(len(self.plan.options), 3)
        self.assertEqual(
            [option.strategy for option in self.plan.options], list(DEFAULT_STRATEGIES)
        )
        self.assertGreater(self.plan.candidates_evaluated, 50)
        self.assertGreater(self.plan.feasible_candidates, 0)

    def test_all_default_options_are_feasible_for_the_demo_mission(self) -> None:
        for option in self.plan.options:
            self.assertTrue(option.feasible, f"{option.label} not feasible")
            self.assertTrue(option.metrics.completes_mission)
            self.assertGreaterEqual(
                option.metrics.critical_load_coverage, self.mission.required_availability
            )

    def test_options_are_distinguishable(self) -> None:
        fuels = {round(o.metrics.fuel_consumption_l, 1) for o in self.plan.options}
        assets = {o.metrics.number_of_active_assets for o in self.plan.options}
        self.assertGreater(len(fuels | assets), 1, "options are indistinguishable")

    def test_min_fuel_option_uses_least_fuel(self) -> None:
        by_strategy = {o.strategy: o for o in self.plan.options}
        least = min(o.metrics.fuel_consumption_l for o in self.plan.options)
        self.assertAlmostEqual(
            by_strategy[Strategy.MIN_FUEL].metrics.fuel_consumption_l, least, places=6
        )

    def test_min_logistics_option_has_fewest_assets(self) -> None:
        by_strategy = {o.strategy: o for o in self.plan.options}
        fewest = min(o.metrics.number_of_active_assets for o in self.plan.options)
        self.assertEqual(
            by_strategy[Strategy.MIN_LOGISTICS].metrics.number_of_active_assets, fewest
        )

    def test_max_endurance_option_has_best_generator_redundancy(self) -> None:
        by_strategy = {o.strategy: o for o in self.plan.options}
        best = max(o.metrics.n_minus_1_ride_through_h for o in self.plan.options)
        self.assertAlmostEqual(
            by_strategy[Strategy.MAX_ENDURANCE].metrics.n_minus_1_ride_through_h, best, places=6
        )

    def test_metrics_report_every_required_output(self) -> None:
        required = [
            "ENDURANCE_HOURS", "CRITICAL_LOAD_COVERAGE", "SECONDARY_LOAD_COVERAGE",
            "FUEL_CONSUMPTION", "ENERGY_RESERVE", "GRID_DEPENDENCE",
            "NUMBER_OF_ACTIVE_ASSETS", "SINGLE_POINTS_OF_FAILURE", "RECOVERY_OPTIONS",
            "DEPLOYMENT_COMPLEXITY",
        ]
        payload = self.plan.options[0].metrics.to_dict()
        for key in required:
            self.assertIn(key, payload)

    def test_no_composite_score_is_reported(self) -> None:
        payload = self.plan.options[0].metrics.to_dict()
        for key in payload:
            self.assertNotIn("assurance_score", key.lower())
            self.assertNotIn("overall_score", key.lower())

    def test_discretionary_assessment_is_reported(self) -> None:
        assessment = self.plan.discretionary_assessment
        self.assertIn("statement", assessment)
        self.assertIn("supportable_functions", assessment)

    def test_max_supported_functions_strategy_serves_more_secondary_load(self) -> None:
        plan = self.engine.generate_options(strategies=[Strategy.MAX_SUPPORTED_FUNCTIONS])
        self.assertGreater(plan.options[0].metrics.secondary_load_coverage, 0.0)
        self.assertIsNot(
            plan.options[0].configuration.policy.secondary_policy, SecondaryPolicy.CRITICAL_ONLY
        )

    def test_deployment_complexity_components_are_exposed(self) -> None:
        deployment = self.plan.options[0].metrics.deployment
        payload = deployment.to_dict()
        self.assertEqual(
            payload["points"],
            deployment.assets_to_deploy
            + deployment.distinct_interface_types
            + -(-deployment.setup_critical_path_min // 30),
        )
        self.assertIn("formula", payload)


class MilpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()
        cls.model = milp.build_model(cls.mission)

    def test_model_has_variables_constraints_and_an_objective(self) -> None:
        size = self.model.size()
        self.assertGreater(size["variables"], 500)
        self.assertGreater(size["binary_variables"], 0)
        self.assertGreater(size["physics_constraints"], 0)
        self.assertGreater(size["requirement_constraints"], 0)
        self.assertEqual(self.model.objective.sense, "min")

    def test_every_generated_schedule_satisfies_the_declared_model(self) -> None:
        for option in self.plan.options:
            report = milp.verify_simulation(
                self.mission, option.simulation, model=self.model
            )
            self.assertTrue(
                report["physically_consistent"],
                f"{option.label}: {report['physics_violations'][:3]}",
            )
            self.assertTrue(
                report["meets_requirements"],
                f"{option.label}: {report['requirement_violations'][:3]}",
            )

    def test_a_tampered_schedule_is_rejected(self) -> None:
        option = self.plan.options[0]
        assignment = milp.assignment_from_simulation(self.mission, option.simulation)
        assignment["g_GEN_A_10"] = 10_000.0
        violations = self.model.verify(assignment)
        self.assertTrue(violations, "verifier accepted an impossible generator output")

    def test_lp_export_has_the_expected_sections(self) -> None:
        text = self.model.to_lp_string()
        for section in ("Minimize", "Subject To", "Bounds", "Binary", "End"):
            self.assertIn(section, text)

    def test_solve_without_a_backend_says_so(self) -> None:
        if milp.available_backends():
            self.skipTest("a MILP backend is installed")
        with self.assertRaises(NotImplementedError):
            milp.solve(self.model)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
