"""The dispatch simulator: energy balance, limits and endurance."""

import unittest

from mission_machine.assets.base import FailureState
from mission_machine.mission.library import load_mission
from mission_machine.planning.configuration import (
    Configuration,
    DispatchPolicy,
    GeneratorMode,
    SecondaryPolicy,
)
from mission_machine.planning.engine import PlanningEngine
from mission_machine.resilience.failures import FailureEvent
from mission_machine.simulation.simulator import Simulator, simulate

TOLERANCE = 1e-6


def configuration(engine: PlanningEngine, **policy_kwargs) -> Configuration:
    policy = DispatchPolicy(
        generator_ids=("GEN-A", "GEN-B"),
        use_grid=True,
        deploy_pv=True,
        use_battery=True,
        battery_reserve_soc=0.2,
        secondary_policy=SecondaryPolicy.AS_PRIORITISED,
        generator_mode=GeneratorMode.CYCLED,
    )
    for key, value in policy_kwargs.items():
        setattr(policy, key, value)
    return Configuration(
        configuration_id="TEST",
        policy=policy,
        active_asset_ids=engine.active_assets_for(policy),
    )


class EnergyBalanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mission = load_mission()
        self.engine = PlanningEngine(self.mission)
        self.configuration = configuration(self.engine)
        self.result = self.engine.simulator.run(self.configuration)

    def test_every_step_balances(self) -> None:
        for step in self.result.steps:
            sources = step.pv_kw + step.grid_kw + step.generator_total_kw + step.battery_discharge_kw
            sinks = (
                step.critical_served_kw
                + step.secondary_served_kw
                + step.battery_charge_kw
                + step.curtailed_kw
            )
            self.assertAlmostEqual(sources, sinks, places=6, msg=f"imbalance at H+{step.hour}")

    def test_no_negative_flows(self) -> None:
        for step in self.result.steps:
            for value in (
                step.pv_kw, step.grid_kw, step.battery_charge_kw,
                step.battery_discharge_kw, step.curtailed_kw, step.secondary_served_kw,
            ):
                self.assertGreaterEqual(value, -TOLERANCE, f"negative flow at H+{step.hour}")

    def test_battery_stays_within_its_limits(self) -> None:
        battery = self.mission.inventory.get("BESS-01")
        for step in self.result.steps:
            self.assertGreaterEqual(step.battery_soc, battery.min_state_of_charge - 1e-6)
            self.assertLessEqual(step.battery_soc, battery.max_state_of_charge + 1e-6)
            self.assertLessEqual(step.battery_charge_kw, battery.max_charge_kw + 1e-6)
            self.assertLessEqual(step.battery_discharge_kw, battery.max_discharge_kw + 1e-6)

    def test_fuel_never_goes_negative_or_exceeds_the_limit(self) -> None:
        self.assertLessEqual(
            sum(step.fuel_used_l for step in self.result.steps),
            self.mission.fuel_limit_l + 1e-6,
        )
        for step in self.result.steps:
            self.assertGreaterEqual(step.fuel_remaining_l, -1e-6)

    def test_generators_respect_minimum_loading(self) -> None:
        for step in self.result.steps:
            for asset_id, output in step.generator_kw.items():
                if output <= 1e-9 or step.fuel_remaining_l < 1.0:
                    continue
                generator = self.mission.inventory.get(asset_id)
                self.assertGreaterEqual(
                    output + 1e-6,
                    generator.min_loading_kw(step.ambient_c),
                    f"{asset_id} below minimum loading at H+{step.hour}",
                )

    def test_grid_import_only_when_the_grid_is_available(self) -> None:
        for step in self.result.steps:
            if not step.grid_available:
                self.assertEqual(step.grid_kw, 0.0, f"grid import at H+{step.hour}")

    def test_pv_is_zero_at_night(self) -> None:
        for step in self.result.steps:
            if step.solar_fraction == 0.0:
                self.assertEqual(step.pv_kw, 0.0)


class DispatchRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mission = load_mission()
        self.engine = PlanningEngine(self.mission)

    def test_critical_only_policy_serves_no_secondary_load(self) -> None:
        config = configuration(self.engine, secondary_policy=SecondaryPolicy.CRITICAL_ONLY)
        result = self.engine.simulator.run(config)
        self.assertEqual(sum(step.secondary_served_kw for step in result.steps), 0.0)

    def test_full_policy_serves_more_than_priority_only(self) -> None:
        full = self.engine.simulator.run(
            configuration(self.engine, secondary_policy=SecondaryPolicy.FULL)
        )
        priority = self.engine.simulator.run(
            configuration(self.engine, secondary_policy=SecondaryPolicy.AS_PRIORITISED)
        )
        self.assertGreater(
            sum(s.secondary_served_kw for s in full.steps),
            sum(s.secondary_served_kw for s in priority.steps),
        )

    def test_losing_power_conversion_stops_the_node(self) -> None:
        config = configuration(self.engine)
        event = FailureEvent(
            event_id="E", asset_id="PCE-01", hour=0.0, state=FailureState.UNAVAILABLE
        )
        result = self.engine.simulator.run(config, events=[event])
        self.assertEqual(result.endurance_hours, 0.0)
        self.assertEqual(result.steps[0].critical_served_kw, 0.0)

    def test_running_out_of_fuel_ends_endurance(self) -> None:
        mission = load_mission()
        mission.fuel_limit_l = 40.0
        engine = PlanningEngine(mission)
        policy = DispatchPolicy(
            generator_ids=("GEN-A",), use_grid=False, deploy_pv=False,
            use_battery=True, secondary_policy=SecondaryPolicy.CRITICAL_ONLY,
        )
        config = Configuration(
            configuration_id="LOWFUEL",
            policy=policy,
            active_asset_ids=engine.active_assets_for(policy),
        )
        result = engine.simulator.run(config)
        self.assertLess(result.endurance_hours, mission.mission_duration_h)
        self.assertIn(result.endurance_limited_by, {"FUEL_EXHAUSTED", "NO_GENERATION_AVAILABLE"})

    def test_simulation_is_deterministic(self) -> None:
        config = configuration(self.engine)
        first = simulate(self.mission, config)
        second = simulate(self.mission, config)
        self.assertEqual(
            [s.to_dict() for s in first.steps], [s.to_dict() for s in second.steps]
        )

    def test_resuming_from_a_saved_state_matches_a_single_run(self) -> None:
        config = configuration(self.engine)
        whole = self.engine.simulator.run(config)
        first = self.engine.simulator.run(config, start_hour=0.0, end_hour=30.0)
        second = self.engine.simulator.run(
            config, start_hour=30.0, end_hour=72.0, initial_state=first.final_state
        )
        joined = first.steps + second.steps
        self.assertEqual(len(joined), len(whole.steps))
        for expected, actual in zip(whole.steps, joined):
            self.assertAlmostEqual(expected.fuel_remaining_l, actual.fuel_remaining_l, places=6)
            self.assertAlmostEqual(expected.battery_soc, actual.battery_soc, places=6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
