"""Operator priorities reaching the optimiser (RQ-001, RQ-008).

Pack 1 carried operator intent as ranked free text that reached the screen and
stopped there. These tests check that it now reaches the planner: that the
vocabulary is validated rather than guessed at, that a function the operator
asked for is served whenever a feasible configuration can serve it, that the
recommendation follows the operator's own ranking and quotes it, and that a
pre-authorised degradation is used only when nothing else is feasible - and
always said out loud.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.mission.spec import (
    MINIMISABLE_QUANTITIES,
    OperatorPriority,
    PriorityIntent,
)
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.configuration import (
    Configuration,
    DispatchPolicy,
    SecondaryPolicy,
)
from mission_machine.planning.engine import PlanningEngine
from mission_machine.explainability.explain import build_recommendation
from mission_machine.resilience.failures import GENERATOR_B_AND_GRID_LOSS


class VocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mission = load_mission()

    def test_the_demo_mission_states_intent_the_planner_can_read(self) -> None:
        self.assertEqual(self.mission.validate(), [])
        self.assertEqual(self.mission.never_interrupt_loads(), ["COMMS-01"])
        self.assertEqual(self.mission.serve_if_affordable_loads(), ["UAS-CHG-01"])
        self.assertEqual(self.mission.degradable_loads(), ["ECS-MIN-01"])
        self.assertEqual(self.mission.minimise_preferences(), [(5, "fuel")])

    def test_an_unactionable_priority_is_advisory_and_reported(self) -> None:
        advisory = self.mission.advisory_priorities()
        self.assertTrue(advisory, "the demo mission should keep one honest gap")
        for priority in advisory:
            self.assertFalse(priority.is_readable)
            self.assertIs(priority.intent, PriorityIntent.ADVISORY)

    def test_shed_order_follows_operator_intent_not_the_equipment(self) -> None:
        inventory = self.mission.inventory
        order = sorted(
            self.mission.critical_load_assets() + self.mission.secondary_load_assets(),
            key=self.mission.shed_order_key,
        )
        self.assertEqual(order[0].asset_id, "COMMS-01", "NEVER_INTERRUPT must be served first")
        self.assertLess(
            self.mission.shed_order_key(inventory.get("UAS-CHG-01")),
            self.mission.shed_order_key(inventory.get("AUX-01")),
            "a prioritised secondary load outranks one the mission never mentions",
        )
        self.assertLess(
            self.mission.shed_order_key(inventory.get("AUX-01")),
            self.mission.shed_order_key(inventory.get("VEH-CHG-01")),
            "an unmentioned load outranks one the operator called discretionary",
        )

    def test_the_quantity_vocabulary_is_closed(self) -> None:
        spec = load_mission()
        spec.operator_priorities.append(
            OperatorPriority(
                rank=99, statement="Minimise something", intent=PriorityIntent.MINIMISE,
                quantity="vibes",
            )
        )
        problems = spec.validate()
        self.assertTrue(any("vibes" in problem for problem in problems))

    def test_every_minimisable_quantity_resolves_to_a_real_metric(self) -> None:
        engine = PlanningEngine(self.mission)
        metrics = engine.generate_options().options[0].metrics
        for quantity, attribute in MINIMISABLE_QUANTITIES.items():
            self.assertTrue(
                hasattr(metrics, attribute), f"{quantity} maps to missing metric {attribute}"
            )
            self.assertIsInstance(float(getattr(metrics, attribute)), float)

    def test_contradicting_the_mission_is_a_validation_error(self) -> None:
        spec = load_mission()
        spec.operator_priorities.append(
            OperatorPriority(
                rank=98, statement="COMMS is optional", applies_to=["COMMS-01"],
                intent=PriorityIntent.SERVE_IF_AFFORDABLE,
            )
        )
        problems = spec.validate()
        self.assertTrue(any("SERVE_IF_AFFORDABLE" in problem for problem in problems))

    def test_pre_authorising_a_degradation_that_does_not_exist_is_rejected(self) -> None:
        spec = load_mission()
        spec.inventory.get("MED-01").min_service_fraction = 1.0
        spec.operator_priorities.append(
            OperatorPriority(
                rank=97, statement="Medical may be degraded", applies_to=["MED-01"],
                intent=PriorityIntent.DEGRADE_ACCEPTABLE,
            )
        )
        problems = spec.validate()
        self.assertTrue(any("no degraded mode to authorise" in problem for problem in problems))


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()

    def test_every_option_serves_the_function_the_operator_asked_for(self) -> None:
        for option in self.plan.options:
            self.assertEqual(
                option.metrics.unhonoured_priorities,
                [],
                f"{option.label} drops a function the operator prioritised",
            )
            self.assertGreaterEqual(option.metrics.load_coverage["UAS-CHG-01"], 0.95)

    def test_functions_the_operator_called_discretionary_are_still_shed(self) -> None:
        for option in self.plan.options:
            for load_id in ("VEH-CHG-01", "HVAC-AUX-01"):
                self.assertLess(option.metrics.load_coverage[load_id], 0.05)

    def test_a_policy_reads_the_operator_not_the_equipment_shed_number(self) -> None:
        policy = DispatchPolicy(secondary_policy=SecondaryPolicy.AS_PRIORITISED)
        inventory = self.mission.inventory
        self.assertTrue(policy.attempts(inventory.get("UAS-CHG-01"), self.mission))
        self.assertFalse(policy.attempts(inventory.get("AUX-01"), self.mission))

    def test_a_mission_without_readable_priorities_falls_back_and_says_so(self) -> None:
        spec = load_mission()
        for priority in spec.operator_priorities:
            priority.intent = PriorityIntent.ADVISORY
        policy = DispatchPolicy(secondary_policy=SecondaryPolicy.AS_PRIORITISED)
        # AUX-01 has shed priority 6, above the fallback threshold of 5.
        self.assertFalse(policy.attempts(spec.inventory.get("AUX-01"), spec))
        self.assertTrue(policy.attempts(spec.inventory.get("UAS-CHG-01"), spec))
        self.assertIn("planner can read", " ".join(policy.describe(spec)))

    def test_the_recommendation_quotes_the_priorities_that_decided_it(self) -> None:
        recommendation = build_recommendation(
            self.plan, self.engine, include_sensitivity=False
        )
        self.assertTrue(
            any("operator priority 4" in reason for reason in recommendation.why),
            "the recommendation must cite the priority that put UAS charging on",
        )
        self.assertTrue(
            any("priority 1" in reason for reason in recommendation.why),
            "the recommendation must cite the priority that put redundancy first",
        )

    def test_the_minimise_preference_decides_where_nothing_outranks_it(self) -> None:
        """With no NEVER_INTERRUPT function, the operator's fuel ranking leads."""

        mission = load_mission()
        for priority in mission.operator_priorities:
            if priority.intent is PriorityIntent.NEVER_INTERRUPT:
                priority.intent = PriorityIntent.MAINTAIN
        engine = PlanningEngine(mission)
        plan = engine.generate_options()
        recommendation = build_recommendation(plan, engine, include_sensitivity=False)
        best = plan.option(recommendation.recommended_configuration_id)
        self.assertAlmostEqual(
            best.metrics.fuel_consumption_l,
            min(o.metrics.fuel_consumption_l for o in plan.options),
            places=6,
        )
        self.assertTrue(any("operator priority 5" in reason for reason in recommendation.why))

    def test_never_interrupt_means_surviving_a_single_failure(self) -> None:
        """"Never" has to mean through a failure, not only in the plan as drawn."""

        recommendation = build_recommendation(
            self.plan, self.engine, include_sensitivity=False
        )
        best = self.plan.option(recommendation.recommended_configuration_id)
        self.assertGreaterEqual(
            best.metrics.n_minus_1_ride_through_h,
            self.mission.ride_through_target_h,
            "a mission with a NEVER_INTERRUPT function should not lead with an option that "
            "cannot ride out the loss of its largest generator",
        )
        self.assertTrue(
            any("never be interrupted" in reason.lower() for reason in recommendation.why),
            "the recommendation must say which priority put redundancy first",
        )

    def test_the_ride_through_target_comes_from_the_mission(self) -> None:
        self.assertAlmostEqual(
            self.mission.ride_through_target_h,
            self.mission.deployment_time_limit_min / 60.0,
        )

    def test_the_cost_of_following_the_operator_is_still_shown(self) -> None:
        """Following a stated priority must not hide what it costs."""

        recommendation = build_recommendation(
            self.plan, self.engine, include_sensitivity=False
        )
        self.assertTrue(
            any("more fuel" in trade.statement for trade in recommendation.trade_offs),
            "the fuel given up by putting priority 1 above priority 5 must be on the screen",
        )


class PreAuthorisedDegradationTests(unittest.TestCase):
    def test_degradation_is_not_used_while_anything_else_is_feasible(self) -> None:
        plan = PlanningEngine(load_mission()).generate_options()
        self.assertFalse(plan.relies_on_preauthorised_degradation)
        for option in plan.options:
            self.assertEqual(option.preauthorised_degradations, [])

    def test_degradation_rescues_the_compound_failure_and_is_declared(self) -> None:
        session = OperationsSession(load_mission())
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        report = session.inject(GENERATOR_B_AND_GRID_LOSS)

        replan = report.options
        self.assertTrue(
            replan.relies_on_preauthorised_degradation,
            "nothing else is feasible after the compound failure",
        )
        self.assertEqual(replan.degraded_loads, ["ECS-MIN-01"])
        self.assertTrue(
            any("pre-authorised" in note for note in replan.notes),
            "the reliance on a degradation must be stated, not implied",
        )
        for option in replan.options:
            self.assertTrue(option.feasible)
            self.assertEqual(option.preauthorised_degradations, ["ECS-MIN-01"])
            self.assertTrue(
                any("pre-authorised" in caveat for caveat in option.caveats),
                "every option carries the caveat, not just the plan",
            )

    def test_a_mission_without_a_pre_authorisation_gets_no_degraded_options(self) -> None:
        mission = load_mission()
        for priority in mission.operator_priorities:
            if priority.intent is PriorityIntent.DEGRADE_ACCEPTABLE:
                priority.intent = PriorityIntent.MAINTAIN
        session = OperationsSession(mission)
        plan = session.generate_options(with_resilience=False)
        session.select(plan.options[0].configuration.configuration_id)
        report = session.inject(GENERATOR_B_AND_GRID_LOSS)
        self.assertFalse(report.options.relies_on_preauthorised_degradation)
        self.assertTrue(
            any(not option.feasible for option in report.options.options),
            "without the operator's authorisation the planner must report failure, not invent one",
        )

    def test_the_degraded_inventory_only_touches_authorised_loads(self) -> None:
        mission = load_mission()
        degraded = mission.degraded_inventory()
        self.assertLess(
            degraded.get("ECS-MIN-01").profile.base_kw,
            mission.inventory.get("ECS-MIN-01").profile.base_kw,
        )
        for load in mission.inventory.loads:
            if load.asset_id == "ECS-MIN-01":
                continue
            self.assertEqual(
                degraded.get(load.asset_id).profile.to_dict(), load.profile.to_dict()
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
