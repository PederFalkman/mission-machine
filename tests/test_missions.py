"""Does the MissionSpec generalise beyond the mission it was written for? (RQ-001)

Two more missions, of a deliberately different shape from MM-DEMO-001: one
where mobility is a hard requirement and there is no host-nation supply at all,
and one four days long where the binding constraint is heat rather than fuel.

The honest limit on all of this is stated where it belongs, in the register:
these were written by the same hands that wrote the schema, so they are a weaker
test than RQ-001 asks for. What the tests below can still do is hold fixed the
things the two missions found - each of which was code quietly assuming the
shape of the one mission it was written against.
"""

from __future__ import annotations

import unittest

from mission_machine.assets.inventory import asset_from_dict
from mission_machine.mission.library import list_missions, load_mission
from mission_machine.operations.alarms import demonstrator_worlds
from mission_machine.planning.engine import PlanningEngine, Strategy


class LoadingTests(unittest.TestCase):
    def test_all_three_missions_load_and_validate(self) -> None:
        for mission_id in ("MM-DEMO-001", "MM-DEMO-002", "MM-DEMO-003"):
            with self.subTest(mission=mission_id):
                mission = load_mission(mission_id)
                problems = mission.validate()
                # MM-DEMO-002 reports one deliberately: the trailer generator on
                # site cannot displace, which is the point of the mission.
                expected = 1 if mission_id == "MM-DEMO-002" else 0
                self.assertEqual(len(problems), expected, problems)

    def test_the_library_finds_every_bundled_mission(self) -> None:
        self.assertEqual(
            list_missions(), ["MM-DEMO-001", "MM-DEMO-002", "MM-DEMO-003"]
        )

    def test_a_bad_enum_says_which_asset_and_what_the_choices_are(self) -> None:
        """RQ-001's point is what somebody who did not write the schema hits.

        Answer, before this: ``'IMPORTANT' is not a valid LoadCriticality``,
        four frames inside the loader, naming neither the asset nor the
        permitted values.
        """

        with self.assertRaises(ValueError) as caught:
            asset_from_dict(
                {
                    "asset_id": "X-01",
                    "kind": "LOAD",
                    "criticality": "IMPORTANT",
                    "profile": {"type": "constant", "kw": 1.0},
                }
            )
        message = str(caught.exception)
        self.assertIn("X-01", message)
        self.assertIn("CRITICAL", message)
        self.assertIn("SECONDARY", message)


class MobilityTests(unittest.TestCase):
    """MM-DEMO-002: the mission may not rely on what it cannot take with it."""

    def setUp(self) -> None:
        self.mission = load_mission("MM-DEMO-002")

    def test_the_mission_names_the_asset_it_cannot_take(self) -> None:
        self.assertEqual(self.mission.mobility_excluded_assets(), ["GEN-HV-01"])
        self.assertEqual(
            self.mission.mobility_excluded_assets_if_required(), ["GEN-HV-01"]
        )

    def test_a_mission_that_need_not_move_excludes_nothing(self) -> None:
        """The limit binds only where the node is actually required to move."""

        self.assertEqual(
            load_mission("MM-DEMO-001").mobility_excluded_assets_if_required(), []
        )

    def test_an_option_relying_on_an_immobile_asset_is_labelled(self) -> None:
        class Configuration:
            active_asset_ids = ["GEN-HV-01", "PCE-LT-01"]

        self.assertEqual(
            self.mission.immobile_assets_relied_on(Configuration()), ["GEN-HV-01"]
        )

        class Mobile:
            active_asset_ids = ["GEN-LT-A", "PCE-LT-01"]

        self.assertEqual(self.mission.immobile_assets_relied_on(Mobile()), [])

    def test_committing_an_immobile_asset_states_the_requirement_it_breaks(self) -> None:
        """The action stays on offer. Lifting the requirement is a command decision.

        What the machine may not do is offer it as though it were free, which
        is what it did: "Commit GEN-HV-01 - bring the heavy generator on line",
        with nothing said about the hour's notice to move.
        """

        from mission_machine.resilience.analysis import ResilienceAnalyst

        analyst = ResilienceAnalyst(PlanningEngine(self.mission))
        heavy = self.mission.inventory.find("GEN-HV-01")
        light = self.mission.inventory.find("GEN-LT-A")

        requires = analyst._commit_requirements(heavy)
        self.assertTrue(any("relocation requirement lifted" in line for line in requires))
        self.assertIn("displace", analyst._commit_cost_note(heavy))

        self.assertFalse(
            any("relocation" in line for line in analyst._commit_requirements(light))
        )

    def test_a_mission_with_no_host_nation_supply_plans_anyway(self) -> None:
        plan = PlanningEngine(self.mission).generate_options()
        self.assertTrue(plan.options)
        self.assertTrue(any(option.feasible for option in plan.options))


class ThermalMissionTests(unittest.TestCase):
    """MM-DEMO-003: the binding constraint is heat, and fuel is ample."""

    def setUp(self) -> None:
        self.mission = load_mission("MM-DEMO-003")
        self.engine = PlanningEngine(self.mission)

    def test_demand_roughly_doubles_across_the_day(self) -> None:
        environment = self.mission.build_environment()
        cool = self.mission.critical_demand_kw(5.0, environment.temperature_c(5.0))
        hot = self.mission.critical_demand_kw(15.0, environment.temperature_c(15.0))
        self.assertGreater(hot, cool * 2.0)

    def test_fuel_is_not_the_binding_constraint(self) -> None:
        plan = self.engine.generate_options()
        leader = plan.options[0]
        self.assertLess(
            leader.metrics.fuel_consumption_l, self.mission.fuel_limit_l * 0.6
        )

    def test_the_default_options_are_reported_as_the_same_option(self) -> None:
        """The finding, kept visible: three names for one answer.

        On a mission whose constraint is not fuel, the default strategies -
        shaped for one that is - separate nothing, while a strategy that is
        implemented but not in the default set returns an option serving every
        function for fuel the mission has spare.
        """

        plan = self.engine.generate_options()
        self.assertTrue(plan.notes, "the planner should say the options do not differ")
        note = plan.notes[0]
        self.assertIn("materially the same", note)
        self.assertIn("MAX_SUPPORTED_FUNCTIONS", note)

    def test_the_option_the_default_set_does_not_generate_is_materially_different(self) -> None:
        default = self.engine.generate_options()
        widened = self.engine.generate_options(strategies=list(Strategy))
        best_default = max(o.metrics.secondary_load_coverage for o in default.options)
        best_widened = max(o.metrics.secondary_load_coverage for o in widened.options)
        self.assertGreater(best_widened, best_default + 0.5)

    def test_a_mission_whose_options_differ_gets_no_note(self) -> None:
        plan = PlanningEngine(load_mission("MM-DEMO-001")).generate_options()
        self.assertEqual(
            [n for n in plan.notes if "materially the same" in n], []
        )


class WorldConstructionTests(unittest.TestCase):
    """The alarm harness assumed the supply shape of the mission it was written for."""

    def test_extra_supply_windows_survive_world_construction(self) -> None:
        """MM-DEMO-003 has four windows; the harness was built against two.

        "Supply returns 2 h late" was silently also deleting the third and
        fourth windows, so the world was not the world its name described.
        """

        mission = load_mission("MM-DEMO-003")
        engine = PlanningEngine(mission)
        stated = engine.environment.grid.available_windows
        self.assertEqual(len(stated), 4)

        late = next(
            w for w in demonstrator_worlds(mission, engine) if w.key == "grid-2h-late"
        )
        windows = late.environment.grid.available_windows
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0], list(stated[0]))
        self.assertEqual(windows[1][0], stated[1][0] + 2.0)
        self.assertEqual(windows[2:], [list(w) for w in stated[2:]])

    def test_a_mission_with_no_supply_gets_no_supply_worlds(self) -> None:
        mission = load_mission("MM-DEMO-002")
        keys = {w.key for w in demonstrator_worlds(mission, PlanningEngine(mission))}
        self.assertFalse(any(key.startswith("grid-") for key in keys))
        self.assertIn("as-forecast", keys)

    def test_no_world_carries_an_empty_supply_window(self) -> None:
        for mission_id in ("MM-DEMO-001", "MM-DEMO-002", "MM-DEMO-003"):
            mission = load_mission(mission_id)
            for world in demonstrator_worlds(mission, PlanningEngine(mission)):
                for window in world.environment.grid.available_windows:
                    with self.subTest(mission=mission_id, world=world.key):
                        self.assertGreater(window[1], window[0])


if __name__ == "__main__":
    unittest.main()
