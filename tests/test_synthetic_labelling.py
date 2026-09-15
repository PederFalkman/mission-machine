"""Guardrail 3 - synthetic stays synthetic, all the way to the output.

Labelling the input data is easy. The claim worth checking is that the label
survives the planner, the simulator, the resilience analysis and the API, so
that nothing a reader sees can be mistaken for a measurement.

``is_operational_truth`` is the property that decides it, and in Pack 1 it is
False everywhere by construction. If a payload ever comes back True, either a
label has been dropped in transit or somebody has started claiming more than the
demonstrator can support.

Ported from capacity-machine's ``tests/test_synthetic_labelling.py``; see
``docs/reuse-assessment.md``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Iterator

from mission_machine.evidence.labels import (
    DEMONSTRATOR_DISCLAIMER,
    EvidenceLabel,
    is_operational_truth,
)
from mission_machine.explainability.explain import build_recommendation
from mission_machine.mission.library import load_mission
from mission_machine.operations.session import OperationsSession
from mission_machine.resilience.failures import GENERATOR_B_UNAVAILABLE
from mission_machine.ui.server import AppState

DATA = Path(__file__).resolve().parents[1] / "data"

REQUIRED_LABELS = {EvidenceLabel.SYNTHETIC, EvidenceLabel.UNVALIDATED}


def label_lists(payload: Any, path: str = "$") -> Iterator[tuple[str, list[str]]]:
    """Every ``data_labels`` list anywhere in a JSON payload, with its location."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "data_labels" and isinstance(value, list):
                yield f"{path}.{key}", value
            else:
                yield from label_lists(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from label_lists(item, f"{path}[{index}]")


class SourceDataTests(unittest.TestCase):
    def test_every_bundled_data_file_declares_itself_synthetic(self) -> None:
        files = sorted(DATA.rglob("*.json"))
        self.assertTrue(files)
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for location, labels in label_lists(payload, path.name):
                self.assertIn("SYNTHETIC", labels, f"{location} is not labelled SYNTHETIC")
                self.assertFalse(
                    is_operational_truth(labels),
                    f"{location} claims to be operational truth",
                )

    def test_every_asset_carries_a_source(self) -> None:
        inventory = load_mission().inventory
        for asset in inventory:
            self.assertIn("SYNTHETIC", asset.data_labels, asset.asset_id)
            self.assertTrue(asset.source, f"{asset.asset_id} has no stated source")


class PropagationTests(unittest.TestCase):
    """Run the whole flow and check the labels survive it."""

    @classmethod
    def setUpClass(cls) -> None:
        session = OperationsSession(load_mission())
        plan = session.generate_options()
        recommendation = build_recommendation(plan, session.engine, include_sensitivity=False)
        session.select(recommendation.recommended_configuration_id)
        report = session.inject(GENERATOR_B_UNAVAILABLE)
        cls.payloads = {
            "plan": plan.to_dict(include_steps=True),
            "recommendation": recommendation.to_dict(),
            "assessment": session.assess().to_dict(),
            "status": session.status_dict(),
            "report": report.to_dict(include_steps=True),
        }

    def test_nothing_anywhere_is_operational_truth(self) -> None:
        for name, payload in self.payloads.items():
            found = list(label_lists(payload, name))
            self.assertTrue(found, f"{name} carries no evidence labels at all")
            for location, labels in found:
                self.assertFalse(
                    is_operational_truth(labels),
                    f"{location} claims to be operational truth: {labels}",
                )

    def test_simulated_results_are_labelled_simulated_and_unvalidated(self) -> None:
        for location, labels in label_lists(self.payloads["plan"], "plan"):
            if "SIMULATED" in labels:
                self.assertIn(
                    "UNVALIDATED", labels, f"{location} is SIMULATED but not UNVALIDATED"
                )

    def test_the_disclaimer_reaches_every_operator_facing_payload(self) -> None:
        for name in ("recommendation", "report", "status"):
            self.assertEqual(
                self.payloads[name]["disclaimer"],
                DEMONSTRATOR_DISCLAIMER,
                f"{name} does not carry the demonstrator disclaimer",
            )

    def test_the_recommendation_never_claims_authority(self) -> None:
        self.assertTrue(self.payloads["recommendation"]["operator_decision_required"])
        self.assertTrue(self.payloads["report"]["operator_decision_required"])

    def test_labels_are_not_diluted_by_the_degraded_mode_replan(self) -> None:
        """A reconfiguration is as synthetic as the plan it replaces."""

        options = self.payloads["report"]["options"]["options"]
        self.assertTrue(options)
        for option in options:
            labels = option["configuration"]["data_labels"]
            self.assertTrue(REQUIRED_LABELS.issubset({EvidenceLabel(x) for x in labels}))


class ApiTests(unittest.TestCase):
    """The same check on what the UI actually serves."""

    @classmethod
    def setUpClass(cls) -> None:
        state = AppState()
        mission = state.mission_payload()
        plan = state.configure(sensitivity=False)
        recommended = plan["recommendation"]["recommended_configuration_id"]
        operate = state.select(recommended)
        degraded = state.degrade("SC-DEGRADED-001")
        cls.payloads = {
            "mission": mission,
            "plan": plan,
            "operate": operate,
            "degraded": degraded,
        }

    def test_every_api_payload_stays_synthetic(self) -> None:
        for name, payload in self.payloads.items():
            for location, labels in label_lists(payload, name):
                self.assertFalse(
                    is_operational_truth(labels),
                    f"{location} claims to be operational truth: {labels}",
                )

    def test_the_mission_screen_carries_the_disclaimer_and_the_assumptions(self) -> None:
        mission = self.payloads["mission"]
        self.assertEqual(mission["disclaimer"], DEMONSTRATOR_DISCLAIMER)
        self.assertTrue(mission["assumptions"])

    def test_withheld_energy_is_reported_rather_than_rendered_as_zero(self) -> None:
        """The defect this repository fixed, kept fixed."""

        options = self.payloads["plan"]["plan"]["options"]
        without_battery = [
            option
            for option in options
            if not option["configuration"]["policy"]["use_battery"]
        ]
        self.assertTrue(without_battery, "expected at least one option that omits the battery")
        for option in without_battery:
            metrics = option["metrics"]
            self.assertGreater(
                metrics["ENERGY_RESERVE_WITHHELD"],
                0.0,
                "a configuration that cannot reach the battery must say so",
            )
            self.assertTrue(
                metrics["OPEN_QUESTIONS"],
                "withheld energy must carry the question that explains it",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
