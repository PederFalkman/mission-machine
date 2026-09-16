"""MissionSpec loading and validation."""

import unittest

from mission_machine.assets.inventory import AssetInventory
from mission_machine.mission.library import load_mission
from mission_machine.mission.spec import MissionSpec, MissionSpecError


class MissionSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mission = load_mission()

    def test_demo_mission_is_valid(self) -> None:
        self.assertEqual(self.mission.mission_id, "MM-DEMO-001")
        self.assertEqual(self.mission.validate(), [])
        self.assertEqual(self.mission.step_count, 72)
        self.assertEqual(len(self.mission.critical_loads), 4)
        self.assertEqual(len(self.mission.secondary_loads), 4)

    def test_every_asset_is_labelled_synthetic(self) -> None:
        for asset in self.mission.inventory:
            self.assertIn("SYNTHETIC", asset.data_labels, asset.asset_id)
            self.assertIn("UNVALIDATED", asset.data_labels, asset.asset_id)

    def test_demand_is_positive_and_bounded(self) -> None:
        environment = self.mission.build_environment()
        for hour in self.mission.hours:
            demand = self.mission.critical_demand_kw(hour, environment.temperature_c(hour))
            self.assertGreater(demand, 0.0)
            self.assertLess(demand, 60.0)

    def test_unknown_load_is_reported_not_raised(self) -> None:
        spec = MissionSpec(
            mission_id="TEST-001",
            critical_loads=["NO-SUCH-LOAD"],
            inventory=AssetInventory(),
        )
        problems = spec.validate()
        self.assertTrue(any("not in asset inventory" in problem for problem in problems))
        with self.assertRaises(MissionSpecError):
            spec.require_valid()

    def test_criticality_mismatch_is_detected(self) -> None:
        spec = load_mission()
        spec.critical_loads = list(spec.critical_loads) + ["AUX-01"]
        spec.secondary_loads = [s for s in spec.secondary_loads if s != "AUX-01"]
        problems = spec.validate()
        self.assertTrue(any("marked SECONDARY" in problem for problem in problems))

    def test_mobility_is_reported_not_enforced_when_static(self) -> None:
        self.assertFalse(self.mission.mobility_requirement.relocation_required)
        self.assertIn("GRID-01", self.mission.mobility_excluded_assets())
        self.assertEqual(self.mission.validate(), [])

    def test_round_trips_through_dict(self) -> None:
        payload = self.mission.to_dict()
        restored = MissionSpec.from_dict(payload, inventory=self.mission.inventory)
        self.assertEqual(restored.mission_id, self.mission.mission_id)
        self.assertEqual(restored.fuel_limit_l, self.mission.fuel_limit_l)
        self.assertEqual(restored.validate(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
