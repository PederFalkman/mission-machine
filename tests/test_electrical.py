"""The electrical-feasibility seam, and what it says when nothing is wired.

Nothing in this file exercises a real power-flow solve: no backend is wired
in Pack 1, by design (see ``planning/electrical.py``). What is tested is the
house rule this repository holds every provider seam to - absence is
survivable, the answer names its source, and an unwired port is reported
rather than hidden - applied to a port with exactly one declared backend and
zero available ones.
"""

from __future__ import annotations

import unittest

from mission_machine.planning.electrical import (
    DEFAULT_ELECTRICAL_FEASIBILITY_REGISTRY,
    ElectricalFeasibilityRegistry,
    ElectricalFeasibilityRequest,
    ElectricalFeasibilityResult,
    ElectricalFeasibilityStatus,
    PowerSystemSolverProvider,
)


class RegistryTests(unittest.TestCase):
    def test_the_declared_backend_is_reported_wired_or_not(self) -> None:
        registry = ElectricalFeasibilityRegistry()
        names = {descriptor.name for descriptor in registry.status_report()}
        self.assertIn("power-system-solver", names)

    def test_an_unwired_port_reports_why_rather_than_vanishing(self) -> None:
        descriptor = PowerSystemSolverProvider().describe()
        self.assertFalse(descriptor.available)
        self.assertTrue(descriptor.detail)
        self.assertIn("boundary", descriptor.detail)
        self.assertIn("topology", descriptor.detail)

    def test_nothing_is_available_in_pack_1(self) -> None:
        registry = ElectricalFeasibilityRegistry()
        self.assertEqual(registry.available(), [])

    def test_with_no_backend_the_registry_says_so_rather_than_guessing(self) -> None:
        registry = ElectricalFeasibilityRegistry()
        request = ElectricalFeasibilityRequest(mission_id="MM-DEMO-001")
        result = registry.assess(request)
        self.assertIs(result.status, ElectricalFeasibilityStatus.NOT_ASSESSED)
        self.assertFalse(result.is_answer)
        self.assertEqual(result.backend, "none")
        self.assertIn("power-system-solver", result.detail)
        self.assertIn("MM-DEMO-001", result.detail)

    def test_a_wired_provider_that_is_called_directly_refuses_rather_than_guessing(self) -> None:
        provider = PowerSystemSolverProvider()
        request = ElectricalFeasibilityRequest(mission_id="MM-DEMO-001")
        with self.assertRaises(RuntimeError):
            provider.assess(request)

    def test_an_empty_registry_also_answers_honestly(self) -> None:
        """A registry is not required to carry the one declared backend."""

        registry = ElectricalFeasibilityRegistry(providers=[])
        result = registry.assess(ElectricalFeasibilityRequest(mission_id="MM-DEMO-002"))
        self.assertIs(result.status, ElectricalFeasibilityStatus.NOT_ASSESSED)
        self.assertEqual(result.backend, "none")

    def test_an_outcome_always_names_its_backend(self) -> None:
        result = ElectricalFeasibilityResult(
            status=ElectricalFeasibilityStatus.FEASIBLE, backend="test"
        )
        payload = result.to_dict()
        self.assertEqual(payload["backend"], "test")
        self.assertIn("SYNTHETIC", payload["data_labels"])

    def test_statuses_are_distinguishable(self) -> None:
        self.assertTrue(ElectricalFeasibilityStatus.FEASIBLE.is_answer)
        self.assertTrue(ElectricalFeasibilityStatus.INFEASIBLE.is_answer)
        for status in (
            ElectricalFeasibilityStatus.NOT_ASSESSED,
            ElectricalFeasibilityStatus.ERROR,
        ):
            self.assertFalse(status.is_answer, status)

    def test_the_default_registry_is_the_same_shape_as_a_fresh_one(self) -> None:
        names = {d.name for d in DEFAULT_ELECTRICAL_FEASIBILITY_REGISTRY.status_report()}
        self.assertIn("power-system-solver", names)

    def test_a_request_round_trips_through_to_dict(self) -> None:
        request = ElectricalFeasibilityRequest(
            mission_id="MM-DEMO-001",
            topology_ref="node-1",
            injections_kw={"LOAD-01": -5.0},
            injections_kvar={"LOAD-01": -1.0},
        )
        payload = request.to_dict()
        self.assertEqual(payload["mission_id"], "MM-DEMO-001")
        self.assertEqual(payload["injections_kw"]["LOAD-01"], -5.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
