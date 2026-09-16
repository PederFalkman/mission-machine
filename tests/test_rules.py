"""Can the dispatch rules close the solver's gap without a solver? (RQ-015)

RQ-009 measured the transparent rules giving up 10-22 % of the fuel against a
perfect-foresight solver, and RQ-012 found that twelve hours of lookahead
recovers nearly all of it - which said the deficiency was commitment, not
clairvoyance. This is the attempt to fix the commitment with rules an operator
can still read and predict.

Two things are held fixed here. The clauses do what they say, and the shipped
rule is left exactly as published so that every number in the register still
reproduces.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from mission_machine.mission.library import load_mission
from mission_machine.planning.configuration import GeneratorMode
from mission_machine.planning.engine import PlanningEngine
from mission_machine.planning.metrics import compute_metrics
from mission_machine.simulation.simulator import Simulator


def run(mission, engine, configuration, mode, min_run=0.0):
    candidate = replace(
        configuration,
        policy=replace(configuration.policy, generator_mode=mode, min_run_hours=min_run),
    )
    result = Simulator(mission, engine.environment, engine.inventory).run(
        candidate, end_hour=mission.mission_duration_h
    )
    return result, compute_metrics(mission, candidate, result, engine.inventory)


def starts(result) -> int:
    count, running = 0, False
    for step in result.steps:
        now = sum(step.generator_kw.values()) > 0.01
        count += 1 if (now and not running) else 0
        running = now
    return count


class PublishedBaselineTests(unittest.TestCase):
    """The shipped rule is not touched by any of this."""

    def test_the_search_does_not_use_the_new_rule_by_default(self) -> None:
        """Because its cost - generator starts - is outside the model (AS-026).

        A saving whose price the model cannot see is not a default.
        """

        engine = PlanningEngine(load_mission())
        self.assertNotIn(GeneratorMode.COAST, engine.generator_modes)

    def test_the_published_figures_still_reproduce(self) -> None:
        plan = PlanningEngine(load_mission()).generate_options()
        fuels = [round(option.metrics.fuel_consumption_l, 1) for option in plan.options]
        self.assertEqual(fuels, [431.0, 405.9, 420.3])
        for option in plan.options:
            self.assertIs(option.configuration.policy.generator_mode, GeneratorMode.CYCLED)


class StopClauseTests(unittest.TestCase):
    """Clause 1: stop the set, do not merely decline to start it."""

    def setUp(self) -> None:
        self.mission = load_mission()
        self.engine = PlanningEngine(self.mission)
        self.configuration = self.engine.generate_options().options[0].configuration

    def test_the_shipped_rule_leaves_a_full_battery_idle(self) -> None:
        """The defect the clause addresses, kept visible.

        CYCLED's own description says it recharges the battery "so it can be
        stopped again". It never stopped it: once a set was turning it tracked
        the load at part loading, and the battery sat at its charge target.
        """

        result, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.CYCLED)
        tail = [s for s in result.steps if s.hour >= 20.0 and sum(s.generator_kw.values()) > 0.01]
        self.assertTrue(tail)
        flat = [s for s in tail if s.battery_discharge_kw <= 1e-6]
        self.assertGreater(
            len(flat) / len(tail), 0.8,
            "the battery should be idle in most running hours under the shipped rule",
        )

    def test_the_clause_cuts_the_hours_a_set_is_turning(self) -> None:
        shipped, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.CYCLED)
        coast, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST)
        on = lambda r: sum(1 for s in r.steps if sum(s.generator_kw.values()) > 0.01)
        self.assertLess(on(coast), on(shipped))

    def test_the_saving_decomposes_into_two_readable_parts(self) -> None:
        """The mechanism, asserted rather than assumed.

        Fewer set-hours, so less no-load fuel; and less energy generated, because
        the shipped rule fills the battery to its target and then never draws it
        down. On MM-DEMO-001 that is 46 L of no-load and 32 L of marginal, and
        the two together are the whole of the difference.
        """

        shipped, shipped_metrics = run(
            self.mission, self.engine, self.configuration, GeneratorMode.CYCLED
        )
        coast, coast_metrics = run(
            self.mission, self.engine, self.configuration, GeneratorMode.COAST, 3.0
        )
        generators = {g.asset_id: g for g in self.mission.inventory.generators}

        def split(result):
            no_load = marginal = 0.0
            for step in result.steps:
                for asset_id, kw in step.generator_kw.items():
                    if kw > 0.01:
                        gen = generators[asset_id]
                        no_load += gen.no_load_l_per_h * step.duration_h
                        marginal += gen.marginal_l_per_kwh * kw * step.duration_h
            return no_load, marginal

        shipped_no_load, shipped_marginal = split(shipped)
        coast_no_load, coast_marginal = split(coast)
        fuel_saved = shipped_metrics.fuel_consumption_l - coast_metrics.fuel_consumption_l

        self.assertGreater(fuel_saved, 0.0)
        self.assertGreater(shipped_no_load - coast_no_load, 0.0, "fewer set-hours")
        self.assertGreater(shipped_marginal - coast_marginal, 0.0, "less energy generated")
        self.assertAlmostEqual(
            fuel_saved,
            (shipped_no_load - coast_no_load) + (shipped_marginal - coast_marginal),
            delta=1.0,
        )

    def test_the_same_service_is_delivered_for_the_smaller_bill(self) -> None:
        """Otherwise the saving would just be load the node stopped serving."""

        shipped, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.CYCLED)
        coast, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST, 3.0)
        served = lambda r: sum(s.critical_served_kw + s.secondary_served_kw for s in r.steps)
        self.assertAlmostEqual(served(coast), served(shipped), delta=1.0)

    def test_the_rule_spends_the_battery_by_the_end_of_the_mission(self) -> None:
        """The cost that does not show up in the fuel column.

        The shipped rule finishes MM-DEMO-001 with a full battery it paid for
        and never used; the new one finishes near its floor. Over a mission that
        ends on schedule that is the right way round, and for a node that might
        be extended, or that expects to hand over with charge in hand, it is
        not. Stated here so the saving is never quoted without it.
        """

        shipped, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.CYCLED)
        coast, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST, 3.0)
        self.assertLess(
            coast.steps[-1].battery_energy_kwh, shipped.steps[-1].battery_energy_kwh - 10.0
        )

    def test_critical_service_is_never_traded_for_fuel(self) -> None:
        for mission_id in ("MM-DEMO-001", "MM-DEMO-002", "MM-DEMO-003"):
            mission = load_mission(mission_id)
            engine = PlanningEngine(mission)
            for option in engine.generate_options().options:
                if not option.configuration.policy.generator_ids:
                    continue
                _, metrics = run(mission, engine, option.configuration, GeneratorMode.COAST, 3.0)
                with self.subTest(mission=mission_id, option=option.label):
                    self.assertGreaterEqual(
                        metrics.critical_load_coverage,
                        option.metrics.critical_load_coverage - 1e-6,
                    )


class CommitmentClauseTests(unittest.TestCase):
    """Clause 2: never start a second set just to refill the battery."""

    def test_a_recharge_does_not_pull_in_another_machine(self) -> None:
        """Without this clause the stop rule *cost* fuel on MM-DEMO-002.

        Node-hours fell from 47 to 24 and set-hours stayed at 48: the node ran
        both light sets at once to refill the battery faster, paying two no-load
        bills an hour instead of one, and finished 5.6 % worse than the rule it
        replaced.
        """

        mission = load_mission("MM-DEMO-002")
        engine = PlanningEngine(mission)
        configuration = engine.generate_options().options[0].configuration

        shipped, shipped_metrics = run(mission, engine, configuration, GeneratorMode.CYCLED)
        coast, coast_metrics = run(mission, engine, configuration, GeneratorMode.COAST)

        def set_hours(result):
            return sum(
                1
                for step in result.steps
                for kw in step.generator_kw.values()
                if kw > 0.01
            )

        self.assertLess(set_hours(coast), set_hours(shipped))
        self.assertLess(coast_metrics.fuel_consumption_l, shipped_metrics.fuel_consumption_l)


class MinimumRunTests(unittest.TestCase):
    """Clause 3: the one that keeps the start count answerable."""

    def setUp(self) -> None:
        self.mission = load_mission()
        self.engine = PlanningEngine(self.mission)
        self.configuration = self.engine.generate_options().options[0].configuration

    def test_a_minimum_run_reduces_the_starts(self) -> None:
        loose, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST, 0.0)
        tight, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST, 4.0)
        self.assertLess(starts(tight), starts(loose))

    def test_the_run_length_resets_when_the_set_stops(self) -> None:
        """Cumulative run hours are a different quantity and would never reset."""

        result, _ = run(self.mission, self.engine, self.configuration, GeneratorMode.COAST, 3.0)
        final = result.final_state
        for gen in self.mission.inventory.generators:
            if not final.generator_running.get(gen.asset_id, False):
                self.assertEqual(final.generator_current_run_h.get(gen.asset_id, 0.0), 0.0)
            self.assertLessEqual(
                final.generator_current_run_h.get(gen.asset_id, 0.0),
                final.generator_run_hours.get(gen.asset_id, 0.0) + 1e-9,
            )

    def test_the_shipped_rule_ignores_the_minimum_run(self) -> None:
        """It is a clause of the new rule, not a change to the old one."""

        plain, plain_metrics = run(
            self.mission, self.engine, self.configuration, GeneratorMode.CYCLED, 0.0
        )
        with_min, with_metrics = run(
            self.mission, self.engine, self.configuration, GeneratorMode.CYCLED, 6.0
        )
        self.assertAlmostEqual(
            plain_metrics.fuel_consumption_l, with_metrics.fuel_consumption_l, places=6
        )


if __name__ == "__main__":
    unittest.main()
