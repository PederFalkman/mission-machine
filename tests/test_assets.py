"""Asset behaviour: fuel curves, storage, PV and load profiles."""

import unittest

from mission_machine.assets.energy import Battery, Generator, GridConnection, SolarPV
from mission_machine.assets.loads import LoadProfile
from mission_machine.mission.library import load_mission


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gen = Generator(
            asset_id="G", name="G", capacity_kw=40.0,
            no_load_l_per_h=2.0, marginal_l_per_kwh=0.3, minimum_loading_fraction=0.3,
        )

    def test_fuel_curve_is_affine(self) -> None:
        self.assertEqual(self.gen.fuel_for(0.0, 1.0), 0.0)
        self.assertAlmostEqual(self.gen.fuel_for(10.0, 1.0), 2.0 + 3.0)
        self.assertAlmostEqual(self.gen.fuel_for(20.0, 2.0), 2 * (2.0 + 6.0))

    def test_specific_fuel_improves_with_loading(self) -> None:
        self.assertGreater(
            self.gen.specific_fuel_l_per_kwh(0.25), self.gen.specific_fuel_l_per_kwh(0.9)
        )

    def test_derating_applies_above_25c(self) -> None:
        self.assertEqual(self.gen.max_output_kw(15.0), 40.0)
        self.assertLess(self.gen.max_output_kw(40.0), 40.0)
        self.assertGreaterEqual(self.gen.max_output_kw(90.0), 40.0 * 0.8)

    def test_minimum_loading(self) -> None:
        self.assertAlmostEqual(self.gen.min_loading_kw(15.0), 12.0)


class BatteryTests(unittest.TestCase):
    def test_usable_energy_and_efficiency_split(self) -> None:
        battery = Battery(
            asset_id="B", name="B", capacity_kw=50.0, energy_capacity_kwh=100.0,
            round_trip_efficiency=0.9, min_state_of_charge=0.2,
        )
        self.assertAlmostEqual(battery.usable_kwh, 80.0)
        self.assertAlmostEqual(battery.one_way_efficiency ** 2, 0.9, places=9)
        self.assertEqual(battery.max_charge_kw, 50.0)


class SolarTests(unittest.TestCase):
    def test_output_scales_with_irradiance_and_is_zero_at_night(self) -> None:
        pv = SolarPV(asset_id="PV", name="PV", capacity_kw=10.0, derating=0.8)
        self.assertEqual(pv.output_kw(0.0, 15.0), 0.0)
        self.assertAlmostEqual(pv.output_kw(0.5, 25.0), 10.0 * 0.8 * 0.5, places=6)
        self.assertLess(pv.output_kw(0.5, 45.0), pv.output_kw(0.5, 25.0))


class GridTests(unittest.TestCase):
    def test_availability_windows(self) -> None:
        grid = GridConnection(asset_id="GRID", name="Grid", available_hours=[[0, 10]])
        self.assertTrue(grid.is_available_at(0))
        self.assertFalse(grid.is_available_at(10))


class LoadProfileTests(unittest.TestCase):
    def test_constant(self) -> None:
        self.assertEqual(LoadProfile(type="constant", kw=5.0).demand_kw(13.0, 10.0), 5.0)

    def test_hourly_wraps(self) -> None:
        profile = LoadProfile(type="hourly", values=[1.0, 2.0])
        self.assertEqual(profile.demand_kw(0.0, 0.0), 1.0)
        self.assertEqual(profile.demand_kw(3.0, 0.0), 2.0)

    def test_diurnal_peaks_at_peak_hour(self) -> None:
        profile = LoadProfile(type="diurnal", base_kw=5.0, swing_kw=4.0, peak_hour=14.0)
        self.assertAlmostEqual(profile.demand_kw(14.0, 0.0), 9.0)
        self.assertAlmostEqual(profile.demand_kw(2.0, 0.0), 5.0)

    def test_temperature_profile_has_no_effect_below_reference(self) -> None:
        profile = LoadProfile(type="temperature", base_kw=4.0, kw_per_degc=0.5, reference_c=10.0)
        self.assertEqual(profile.demand_kw(0.0, 5.0), 4.0)
        self.assertAlmostEqual(profile.demand_kw(0.0, 20.0), 9.0)

    def test_schedule_windows(self) -> None:
        profile = LoadProfile(type="schedule", windows=[[9.0, 17.0, 20.0]])
        self.assertEqual(profile.demand_kw(10.0, 0.0), 20.0)
        self.assertEqual(profile.demand_kw(18.0, 0.0), 0.0)
        self.assertEqual(profile.demand_kw(34.0, 0.0), 20.0)  # next day

    def test_unknown_profile_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LoadProfile(type="magic").demand_kw(0.0, 0.0)


class InventoryTests(unittest.TestCase):
    def test_inventory_lookup_and_grouping(self) -> None:
        inventory = load_mission().inventory
        self.assertEqual(len(inventory.generators), 2)
        self.assertEqual(len(inventory.batteries), 1)
        self.assertEqual(len(inventory.critical_loads), 4)
        self.assertIsNone(inventory.find("NOPE"))
        with self.assertRaises(KeyError):
            inventory.get("NOPE")

    def test_with_failure_does_not_mutate_the_original(self) -> None:
        from mission_machine.assets.base import FailureState

        inventory = load_mission().inventory
        degraded = inventory.with_failure("GEN-B", FailureState.UNAVAILABLE)
        self.assertFalse(degraded.get("GEN-B").is_available)
        self.assertTrue(inventory.get("GEN-B").is_available)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
