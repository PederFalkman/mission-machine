"""What depends on what, and what a failure does to it (RQ-005).

Pack 1 could say that a function was not supported. It could not say what it was
*short of*, because the only evidence was a number that came out zero. These
tests hold the difference in place: that the mechanism is named, that a
shortfall stays distinguishable from an outage, and - the one that matters most -
that the graph explains the simulation rather than quietly becoming a second one.
"""

from __future__ import annotations

import unittest

from mission_machine.mission.library import load_mission
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.engine import PlanningEngine
from mission_machine.resilience import failures
from mission_machine.resilience.dependencies import (
    DEFAULT_PROPAGATION_REGISTRY,
    DependencyGraph,
    Dependency,
    DependencyKind,
    GraphPropagationProvider,
    OperatingState,
    PropagationRegistry,
    RodotPropagationProvider,
    Satisfaction,
    build_graph,
    propagate,
    states_from_events,
    states_from_step,
)


def _graph() -> DependencyGraph:
    return build_graph(load_mission())


class GraphDerivationTests(unittest.TestCase):
    """The graph comes from the asset set, not from a second file to keep in step."""

    def setUp(self) -> None:
        self.mission = load_mission()
        self.graph = _graph()

    def test_the_cooling_dependency_is_machine_readable_now(self) -> None:
        """It was always asserted; nothing could read it.

        ECS-MIN-01's ``function`` says *"Equipment shelter cooling required to
        keep comms and IT within limits"*, and that sentence was the whole
        dependency - the same shape as the operator priorities before RQ-008.
        """

        shelter = self.mission.inventory.find("ECS-MIN-01")
        self.assertIn("within limits", shelter.function.lower())
        self.assertEqual(sorted(shelter.supports), ["C2IT-01", "COMMS-01"])
        self.assertAlmostEqual(shelter.support_fraction, 0.8)

    def test_three_kinds_of_edge_and_each_one_is_derived(self) -> None:
        kinds = {edge.kind for edge in self.graph.edges}
        self.assertEqual(
            kinds, {DependencyKind.POWER, DependencyKind.CONVERSION, DependencyKind.COOLING}
        )
        cooling = [e for e in self.graph.edges if e.kind is DependencyKind.COOLING]
        self.assertEqual(
            sorted((e.dependant, e.provider) for e in cooling),
            [("C2IT-01", "ECS-MIN-01"), ("COMMS-01", "ECS-MIN-01")],
        )

    def test_every_mission_in_the_library_builds_a_graph(self) -> None:
        for mission_id in ("MM-DEMO-001", "MM-DEMO-002", "MM-DEMO-003"):
            graph = build_graph(load_mission(mission_id))
            with self.subTest(mission=mission_id):
                self.assertTrue(graph.edges)
                self.assertTrue(
                    any(e.kind is DependencyKind.COOLING for e in graph.edges),
                    "each asset set declares what its cooling supports",
                )

    def test_a_declared_support_that_is_not_in_the_inventory_is_reported(self) -> None:
        """Not silently dropped, which would turn a data error into a clean graph."""

        mission = load_mission()
        shelter = mission.inventory.find("ECS-MIN-01")
        shelter.supports = ["NOT-AN-ASSET"]
        graph = build_graph(mission)
        self.assertTrue(any("NOT-AN-ASSET" in note for note in graph.notes))
        self.assertFalse([e for e in graph.edges if e.kind is DependencyKind.COOLING])


class ShortfallTests(unittest.TestCase):
    """Capacity on the edge is the whole reason this is not a boolean graph."""

    def setUp(self) -> None:
        self.graph = _graph()

    def states(self, **fractions: float) -> dict[str, OperatingState]:
        return states_from_events(load_mission().inventory, served_fractions=fractions)

    def test_cooling_at_sixty_per_cent_is_short_and_not_stopped(self) -> None:
        result = propagate(self.graph, self.states(**{"ECS-MIN-01": 0.6}))
        comms = result.consequences["COMMS-01"]
        self.assertIs(comms.satisfaction, Satisfaction.SHORT)
        self.assertIn("short of cooling", " ".join(comms.because))
        self.assertIn("60%", " ".join(comms.because))

    def test_cooling_stopped_is_an_outage_and_says_so_differently(self) -> None:
        result = propagate(self.graph, self.states(**{"ECS-MIN-01": 0.0}))
        comms = result.consequences["COMMS-01"]
        self.assertIs(comms.satisfaction, Satisfaction.UNMET)
        self.assertIn("outside the cooling it needs", " ".join(comms.because))

    def test_degradation_is_charged_once(self) -> None:
        """The defect found by reading the explanation out loud.

        ``supported[x]`` is seeded from the provider's own served fraction, so
        multiplying by the state again charged the same degradation twice and
        cooling running at 60 % was reported as 36 %.
        """

        result = propagate(self.graph, self.states(**{"ECS-MIN-01": 0.6}))
        self.assertAlmostEqual(
            result.consequences["COMMS-01"].supported_fraction, 0.6 / 0.8, places=6
        )

    def test_cooling_above_what_is_needed_is_not_a_finding(self) -> None:
        """85 % of a service that needs 80 % is met, and saying otherwise is noise."""

        result = propagate(self.graph, self.states(**{"ECS-MIN-01": 0.85}))
        self.assertIs(result.consequences["COMMS-01"].satisfaction, Satisfaction.MET)
        self.assertFalse(result.consequences["COMMS-01"].because)


class AlternativesTests(unittest.TestCase):
    """POWER edges are alternatives; CONVERSION and COOLING edges are requirements."""

    def setUp(self) -> None:
        self.graph = _graph()
        self.inventory = load_mission().inventory

    def test_one_surviving_source_carries_the_node(self) -> None:
        states = states_from_events(
            self.inventory, unavailable=["GRID-01", "GEN-B", "BESS-01", "PV-01"]
        )
        result = propagate(self.graph, states)
        self.assertIs(result.consequences["PCE-01"].satisfaction, Satisfaction.MET)

    def test_losing_every_source_stops_the_conversion_unit(self) -> None:
        states = states_from_events(
            self.inventory,
            unavailable=["GRID-01", "GEN-A", "GEN-B", "BESS-01", "PV-01"],
        )
        result = propagate(self.graph, states)
        self.assertIs(result.consequences["PCE-01"].satisfaction, Satisfaction.UNMET)
        self.assertIn("no supply reaching it", " ".join(result.consequences["PCE-01"].because))

    def test_losing_the_single_conversion_unit_stops_everything_it_feeds(self) -> None:
        result = propagate(self.graph, states_from_events(self.inventory, ["PCE-01"]))
        for load in ("COMMS-01", "C2IT-01", "MED-01"):
            with self.subTest(load=load):
                self.assertIs(result.consequences[load].satisfaction, Satisfaction.UNMET)


class ChainTests(unittest.TestCase):
    """The chain is the deliverable. "COMMS-01 is at risk" is not an answer."""

    def setUp(self) -> None:
        self.graph = _graph()
        self.inventory = load_mission().inventory
        self.result = propagate(
            self.graph,
            states_from_events(
                self.inventory,
                unavailable=["GRID-01", "GEN-A", "GEN-B", "BESS-01", "PV-01"],
            ),
        )

    def test_the_chain_reaches_the_root_cause(self) -> None:
        because = self.result.consequences["COMMS-01"].because
        self.assertIn("COMMS-01 has no path to power", because[0])
        self.assertTrue(
            any("PCE-01 has no supply reaching it" in line for line in because),
            f"the chain should reach the supply, got {because}",
        )

    def test_the_chain_names_each_node_once(self) -> None:
        """The regression: ``_root_causes`` re-prefixed already-prefixed lines.

        The output came out as ``...PCE-01: ...BESS-01: BESS-01 has stopped``,
        nested three deep and repeated once per dependant. Substance right,
        unreadable.
        """

        because = self.result.consequences["COMMS-01"].because
        for line in because:
            self.assertNotIn("...", line)
            self.assertLessEqual(
                line.count(":"), 1, f"one hop per line, got {line!r}"
            )
        own = [line for line in because if not line.startswith(" ")]
        self.assertTrue(all(line.startswith("COMMS-01") for line in own))
        chained = [line.strip().split(" ")[0] for line in because if line.startswith(" ")]
        self.assertEqual(
            len(chained), len(set(chained)), f"a node twice in one chain: {chained}"
        )

    def test_a_node_down_on_its_own_account_still_gives_a_reason(self) -> None:
        """Without this it was reported stopped with nothing said about why."""

        states = states_from_events(self.inventory, unavailable=["GEN-A"])
        result = propagate(self.graph, states)
        self.assertTrue(result.consequences["GEN-A"].because)
        self.assertIn("GEN-A has stopped", result.consequences["GEN-A"].because[0])


class UnknownTests(unittest.TestCase):
    def test_a_node_with_no_state_is_unknown_not_assumed_fine(self) -> None:
        graph = DependencyGraph(
            mission_id="X",
            edges=[Dependency(dependant="A", provider="B", kind=DependencyKind.CONVERSION)],
        )
        result = propagate(graph, {"A": OperatingState("A")})
        self.assertIn("B", result.unknown)
        self.assertIs(result.consequences["B"].satisfaction, Satisfaction.UNKNOWN)

    def test_a_cycle_is_reported_rather_than_resolved_by_assumption(self) -> None:
        graph = DependencyGraph(
            mission_id="X",
            edges=[
                Dependency(dependant="A", provider="B", kind=DependencyKind.CONVERSION),
                Dependency(dependant="B", provider="A", kind=DependencyKind.CONVERSION),
            ],
        )
        result = propagate(graph, {"A": OperatingState("A"), "B": OperatingState("B")})
        self.assertEqual(sorted(result.consequences), ["A", "B"])


class StatesFromRunTests(unittest.TestCase):
    """Operating states read off an actual hour, so the chain explains a run."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()

    def step(self, hour: float):
        from mission_machine.simulation.simulator import Simulator

        configuration = self.plan.options[0].configuration
        result = Simulator(self.mission, self.engine.environment, self.engine.inventory).run(
            configuration, end_hour=self.mission.mission_duration_h
        )
        return min(result.steps, key=lambda s: abs(s.hour - hour))

    def test_a_battery_on_its_floor_is_not_a_live_source(self) -> None:
        """Stored energy is not available energy.

        A battery sitting exactly on its hard floor reads 18 kWh and has nothing
        to give; calling that a source made the graph report the conversion unit
        stopped for no stated reason.
        """

        battery = self.mission.inventory.find("BESS-01")
        floor = battery.min_state_of_charge * battery.energy_capacity_kwh

        class Step:
            hour = 0.0
            battery_energy_kwh = floor

        states = states_from_step(self.mission.inventory, Step())
        self.assertEqual(states["BESS-01"].served_fraction, 0.0)
        self.assertIn("floor", states["BESS-01"].note)

    def test_an_idle_set_with_fuel_is_a_source_and_a_dry_one_is_not(self) -> None:
        """Committed or not is the wrong question to ask a dependency graph."""

        class Idle:
            hour = 0.0
            generator_kw: dict = {}
            fuel_remaining_l = 200.0

        class Dry:
            hour = 0.0
            generator_kw: dict = {}
            fuel_remaining_l = 0.0

        idle = states_from_step(self.mission.inventory, Idle())
        dry = states_from_step(self.mission.inventory, Dry())
        self.assertEqual(idle["GEN-A"].served_fraction, 1.0)
        self.assertIn("fuel available", idle["GEN-A"].note)
        self.assertEqual(dry["GEN-A"].served_fraction, 0.0)
        self.assertIn("no fuel", dry["GEN-A"].note)

    def test_a_conversion_unit_carries_the_node_only_if_power_flowed(self) -> None:
        """Defaulting it to serviceable reported a cooling shortfall when the
        real answer was that the node had run out of fuel."""

        class Nothing:
            hour = 0.0
            generator_kw: dict = {}
            pv_kw = 0.0
            grid_kw = 0.0
            battery_discharge_kw = 0.0

        states = states_from_step(self.mission.inventory, Nothing())
        self.assertEqual(states["PCE-01"].served_fraction, 0.0)
        self.assertIn("nothing flowing", states["PCE-01"].note)

    def test_a_load_served_in_full_reads_as_met(self) -> None:
        step = self.step(2.0)
        states = states_from_step(self.mission.inventory, step)
        self.assertAlmostEqual(states["COMMS-01"].served_fraction, 1.0, places=3)
        self.assertIn("kW", states["COMMS-01"].note)


class SessionTests(unittest.TestCase):
    """What the operator's panel actually gets."""

    def session(self):
        session = OperationsSession(load_mission())
        plan = session.generate_options()
        session.select(plan.options[0].configuration.configuration_id)
        return session

    def test_the_compound_loss_names_the_mechanism(self) -> None:
        session = self.session()
        report = session.inject(failures.GENERATOR_B_AND_GRID_LOSS, regenerate=False)
        joined = " ".join(report.mechanism)
        self.assertIn("COMMS-01 is outside the cooling it needs", joined)
        self.assertIn("no supply reaching it", joined)

    def test_the_hour_explained_is_the_worst_projected_one_and_says_so(self) -> None:
        """At the hour a set fails nothing is short yet, so the present is useless.

        The forecast is labelled a forecast; that is the difference between a
        projection and a claim about now.
        """

        session = self.session()
        report = session.inject(failures.GENERATOR_B_AND_GRID_LOSS, regenerate=False)
        self.assertTrue(report.propagation.projected)
        self.assertGreater(report.propagation.at_hour, report.event_hour)

    def test_the_single_generator_loss_reports_nothing_short(self) -> None:
        """An answer, not an empty result: the surviving set carries the node."""

        session = self.session()
        report = session.inject(failures.GENERATOR_B_UNAVAILABLE, regenerate=False)
        self.assertEqual(report.mechanism, [])

    def test_propagation_does_not_change_the_dispatch(self) -> None:
        """AS-027, asserted rather than promised.

        A graph that quietly started shedding loads would be a second,
        unverified model of the same node sitting beside the simulator.
        """

        session = self.session()
        before = session.project()[0]
        session.propagate_consequences()
        session.propagate_consequences()
        after = session.project()[0]
        self.assertEqual(
            [round(s.critical_served_kw, 6) for s in before.steps],
            [round(s.critical_served_kw, 6) for s in after.steps],
        )


class ProviderSeamTests(unittest.TestCase):
    """A declared port reports itself whether or not anybody wired it."""

    def test_the_rodot_backend_is_declared_and_says_it_is_not_wired(self) -> None:
        descriptor = RodotPropagationProvider().describe()
        self.assertFalse(descriptor.available)
        self.assertIn("reuse-assessment", descriptor.detail)

    def test_the_unwired_backend_refuses_rather_than_returning_something(self) -> None:
        with self.assertRaises(RuntimeError):
            RodotPropagationProvider().propagate(_graph(), {})

    def test_the_registry_reports_every_backend_and_uses_the_wired_one(self) -> None:
        names = {d.name for d in DEFAULT_PROPAGATION_REGISTRY.status_report()}
        self.assertEqual(names, {"graph", "rodot"})
        self.assertIsInstance(DEFAULT_PROPAGATION_REGISTRY.active(), GraphPropagationProvider)

    def test_a_registry_with_nothing_wired_raises_rather_than_guessing(self) -> None:
        with self.assertRaises(RuntimeError):
            PropagationRegistry([RodotPropagationProvider()]).active()


class LabellingTests(unittest.TestCase):
    def test_the_result_carries_its_labels_and_disclaimer(self) -> None:
        result = propagate(_graph(), states_from_events(load_mission().inventory))
        payload = result.to_dict()
        self.assertIn("SYNTHETIC", payload["data_labels"])
        self.assertIn("SIMULATED", payload["data_labels"])
        self.assertIn("UNVALIDATED", payload["data_labels"])
        self.assertIn("no military validation", payload["disclaimer"])


if __name__ == "__main__":
    unittest.main()
