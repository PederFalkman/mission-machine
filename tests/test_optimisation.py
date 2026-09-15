"""The solver seam, and what it says about the dispatch rules (RQ-009).

Most of this runs without any solver installed: the seam has to work, and
report honestly, when nothing is wired. The tests that need a real
branch-and-cut solve are skipped unless one is present, and are kept short
enough to belong in a test suite.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.planning import milp
from mission_machine.planning.engine import PlanningEngine
from mission_machine.planning.optimal import compare_with_optimum
from mission_machine.planning.providers import (
    DISPATCH_MILP,
    OrToolsCpSatProvider,
    ProviderRegistry,
    PulpCbcProvider,
    SolverOutcome,
    SolverStatus,
)

SOLVER_AVAILABLE = PulpCbcProvider().describe().available
#: Short enough for a test suite; long enough for CBC to find something.
TEST_BUDGET_S = 20.0


def short_mission(hours: float = 12.0):
    """The demo mission over a shorter horizon, so a solve fits in a test."""

    mission = load_mission()
    mission.mission_duration_h = hours
    return mission


class RegistryTests(unittest.TestCase):
    def test_every_declared_backend_is_reported_wired_or_not(self) -> None:
        registry = ProviderRegistry()
        names = {descriptor.name for descriptor in registry.status_report()}
        self.assertIn("pulp-cbc", names)
        self.assertIn("ortools-cpsat", names, "a port nobody wired must still be reported")

    def test_an_unwired_port_reports_why_rather_than_vanishing(self) -> None:
        descriptor = OrToolsCpSatProvider().describe()
        self.assertFalse(descriptor.available)
        self.assertTrue(descriptor.detail)
        self.assertFalse(OrToolsCpSatProvider().supports(DISPATCH_MILP))

    def test_with_no_backend_the_registry_says_so_rather_than_guessing(self) -> None:
        registry = ProviderRegistry(providers=[OrToolsCpSatProvider()])
        model = milp.build_model(short_mission())
        outcome = registry.solve(model, time_budget_s=1)
        self.assertIs(outcome.status, SolverStatus.UNAVAILABLE)
        self.assertFalse(outcome.is_answer)
        self.assertIn("to_lp_string", outcome.detail)
        self.assertIn("ortools-cpsat", outcome.detail)

    def test_an_outcome_always_names_its_backend(self) -> None:
        outcome = SolverOutcome(status=SolverStatus.OPTIMAL, backend="test", objective=1.0)
        payload = outcome.to_dict()
        self.assertEqual(payload["backend"], "test")
        self.assertIn("SYNTHETIC", payload["data_labels"])

    def test_statuses_are_distinguishable(self) -> None:
        self.assertTrue(SolverStatus.OPTIMAL.is_answer)
        self.assertTrue(SolverStatus.FEASIBLE.is_answer)
        for status in (
            SolverStatus.INFEASIBLE,
            SolverStatus.UNAVAILABLE,
            SolverStatus.BUDGET_EXCEEDED,
            SolverStatus.ERROR,
        ):
            self.assertFalse(status.is_answer, status)


class ModelRestrictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()

    def test_restricting_to_a_configuration_drops_the_assets_it_does_not_use(self) -> None:
        whole = milp.build_model(self.mission, self.engine.environment, self.engine.inventory)
        option = next(
            o for o in self.plan.options if len(o.configuration.policy.generator_ids) == 1
        )
        restricted = milp.build_model(
            self.mission,
            self.engine.environment,
            self.engine.inventory,
            configuration=option.configuration,
        )
        self.assertLess(restricted.size()["variables"], whole.size()["variables"])
        self.assertLess(
            restricted.size()["binary_variables"], whole.size()["binary_variables"]
        )
        self.assertEqual(restricted.metadata["configuration_id"], option.configuration.configuration_id)

    def test_the_service_floor_is_a_requirement_not_physics(self) -> None:
        option = self.plan.options[0]
        model = milp.build_model(
            self.mission,
            self.engine.environment,
            self.engine.inventory,
            configuration=option.configuration,
            service_floor=option.simulation,
        )
        floors = [c for c in model.constraints if c.name.startswith("service_")]
        self.assertTrue(floors, "a configuration serving a discretionary load needs a floor")
        for constraint in floors:
            self.assertEqual(constraint.category, "requirement")
            self.assertEqual(constraint.sense, ">=")

    def test_the_simulated_schedule_still_satisfies_the_restricted_model(self) -> None:
        for option in self.plan.options:
            model = milp.build_model(
                self.mission,
                self.engine.environment,
                self.engine.inventory,
                configuration=option.configuration,
                service_floor=option.simulation,
            )
            report = milp.verify_simulation(
                self.mission,
                option.simulation,
                self.engine.environment,
                self.engine.inventory,
                model=model,
            )
            self.assertTrue(report["physically_consistent"], option.label)
            self.assertTrue(report["meets_requirements"], option.label)


class ComparisonWithoutASolverTests(unittest.TestCase):
    """The comparison has to behave when nobody installed a solver."""

    def test_no_solver_means_no_claim(self) -> None:
        mission = short_mission()
        engine = PlanningEngine(mission)
        plan = engine.generate_options()
        option = plan.options[0]
        registry = ProviderRegistry(providers=[OrToolsCpSatProvider()])
        comparison = compare_with_optimum(
            mission,
            option.configuration,
            option.simulation,
            environment=engine.environment,
            inventory=engine.inventory,
            registry=registry,
        )
        self.assertFalse(comparison.trustworthy)
        self.assertIsNone(comparison.optimal_fuel_l)
        self.assertIsNone(comparison.saving_l)
        self.assertTrue(
            any("nothing is claimed" in caveat for caveat in comparison.caveats),
            "with no optimum, the comparison must claim nothing about the rules",
        )


@unittest.skipUnless(SOLVER_AVAILABLE, "no MILP backend installed")
class SolverBackedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = short_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()
        cls.option = cls.plan.options[0]
        cls.comparison = compare_with_optimum(
            cls.mission,
            cls.option.configuration,
            cls.option.simulation,
            environment=cls.engine.environment,
            inventory=cls.engine.inventory,
            time_budget_s=TEST_BUDGET_S,
            baseline_fuel_l=cls.option.metrics.fuel_consumption_l,
        )

    def test_the_solver_answers_and_names_itself(self) -> None:
        self.assertTrue(self.comparison.status.is_answer, self.comparison.detail)
        self.assertEqual(self.comparison.backend, "pulp-cbc")

    def test_the_solver_answer_is_checked_before_it_is_believed(self) -> None:
        self.assertTrue(
            self.comparison.verified,
            f"solver returned an assignment that violates the model: "
            f"{self.comparison.violations[:3]}",
        )

    def test_the_optimum_is_never_worse_than_the_rules(self) -> None:
        self.assertLessEqual(
            self.comparison.optimal_fuel_l,
            self.comparison.baseline_fuel_l + 1e-6,
            "the rules cannot beat an optimum over the same constraints",
        )

    def test_the_perfect_foresight_caveat_is_always_attached(self) -> None:
        self.assertTrue(
            any("upper bound" in caveat for caveat in self.comparison.caveats),
            "a solver number next to a simulated one must carry the foresight caveat",
        )

    def test_a_budget_limited_solve_is_not_claimed_as_optimal(self) -> None:
        model = milp.build_model(
            load_mission(),  # full 72 h: too big to prove optimal in one second
            self.engine.environment,
            self.engine.inventory,
        )
        outcome = PulpCbcProvider().solve(model, time_budget_s=1)
        self.assertIsNot(
            outcome.status,
            SolverStatus.OPTIMAL,
            "a solve that spent its whole budget must not be reported as proven optimal",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class RollingHorizonTests(unittest.TestCase):
    """The lookahead harness, including what it does when nothing can solve."""

    def test_no_solver_means_no_fuel_figure(self) -> None:
        from mission_machine.planning.rolling import run_rolling_horizon

        mission = short_mission()
        engine = PlanningEngine(mission)
        option = engine.generate_options().options[0]
        result = run_rolling_horizon(
            mission,
            option.configuration,
            option.simulation,
            window_h=6,
            commit_h=6,
            environment=engine.environment,
            inventory=engine.inventory,
            registry=ProviderRegistry(providers=[OrToolsCpSatProvider()]),
        )
        self.assertFalse(result.completed)
        self.assertIsNone(result.fuel_l)
        self.assertTrue(any("could not produce a plan" in c for c in result.caveats))

    def test_a_window_that_does_not_reach_the_end_credits_its_closing_storage(self) -> None:
        """Without the terminal credit a finite window empties the battery for free."""

        mission = load_mission()
        engine = PlanningEngine(mission)
        option = engine.generate_options().options[0]
        mid = milp.build_model(
            mission,
            engine.environment,
            engine.inventory,
            configuration=option.configuration,
            start_hour=0.0,
            end_hour=12.0,
            terminal_storage_value=True,
        )
        final = milp.build_model(
            mission,
            engine.environment,
            engine.inventory,
            configuration=option.configuration,
            start_hour=60.0,
            end_hour=72.0,
            terminal_storage_value=False,
        )
        credits = [
            name for name, value in mid.objective.terms.items()
            if name.startswith("soc_") and value < 0
        ]
        self.assertEqual(len(credits), 1, "exactly the closing step should be credited")
        self.assertFalse(
            [n for n, v in final.objective.terms.items() if n.startswith("soc_") and v < 0],
            "a window that reaches the end of the mission must not credit leftover storage",
        )

    def test_the_service_floor_can_be_scoped_to_the_window(self) -> None:
        mission = load_mission()
        engine = PlanningEngine(mission)
        option = engine.generate_options().options[0]
        whole = milp.build_model(
            mission, engine.environment, engine.inventory,
            configuration=option.configuration, service_floor=option.simulation,
            start_hour=0.0, end_hour=12.0,
        )
        windowed = milp.build_model(
            mission, engine.environment, engine.inventory,
            configuration=option.configuration, service_floor=option.simulation,
            service_floor_window=True, start_hour=0.0, end_hour=12.0,
        )
        whole_floor = {c.name: c.rhs for c in whole.constraints if c.name.startswith("service_")}
        window_floor = {
            c.name: c.rhs for c in windowed.constraints if c.name.startswith("service_")
        }
        self.assertTrue(whole_floor)
        for name, rhs in window_floor.items():
            self.assertLessEqual(
                rhs,
                whole_floor[name] + 1e-6,
                "a window may not be asked to deliver the whole mission's discretionary energy",
            )


@unittest.skipUnless(SOLVER_AVAILABLE, "no MILP backend installed")
class RollingHorizonWithSolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = short_mission(12.0)
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()
        cls.option = cls.plan.options[0]

    def _run(self, window_h: float, commit_h: float):
        from mission_machine.planning.rolling import run_rolling_horizon

        return run_rolling_horizon(
            self.mission,
            self.option.configuration,
            self.option.simulation,
            window_h=window_h,
            commit_h=commit_h,
            environment=self.engine.environment,
            inventory=self.engine.inventory,
            time_budget_s=TEST_BUDGET_S,
            baseline_fuel_l=self.option.metrics.fuel_consumption_l,
        )

    def test_a_window_as_long_as_the_mission_reproduces_the_single_solve(self) -> None:
        """The strongest check on the harness: no rolling, same answer."""

        rolling = self._run(window_h=12.0, commit_h=12.0)
        single = compare_with_optimum(
            self.mission,
            self.option.configuration,
            self.option.simulation,
            environment=self.engine.environment,
            inventory=self.engine.inventory,
            time_budget_s=TEST_BUDGET_S,
            baseline_fuel_l=self.option.metrics.fuel_consumption_l,
        )
        self.assertTrue(rolling.completed, rolling.detail)
        self.assertEqual(rolling.solves, 1)
        self.assertAlmostEqual(rolling.fuel_l, single.optimal_fuel_l, places=1)

    def test_every_committed_window_is_verified(self) -> None:
        rolling = self._run(window_h=6.0, commit_h=3.0)
        self.assertTrue(rolling.completed, rolling.detail)
        self.assertGreater(rolling.solves, 1)
        for window in rolling.windows:
            self.assertTrue(window.verified, f"window at H+{window.start_hour} not verified")

    def test_a_shorter_lookahead_never_beats_the_whole_mission(self) -> None:
        short = self._run(window_h=3.0, commit_h=3.0)
        whole = self._run(window_h=12.0, commit_h=12.0)
        self.assertTrue(short.completed and whole.completed)
        self.assertGreaterEqual(
            short.fuel_l,
            whole.fuel_l - 1.0,
            "less foresight cannot use less fuel; if it does, the terminal credit is too generous",
        )

    def test_the_foresight_caveat_travels_with_the_number(self) -> None:
        rolling = self._run(window_h=6.0, commit_h=6.0)
        self.assertTrue(
            any("forecast accuracy" in caveat for caveat in rolling.caveats),
            "a lookahead result must say it still assumes a perfect forecast in-window",
        )
