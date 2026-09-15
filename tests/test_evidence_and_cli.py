"""Evidence discipline, explainability and the command-line entry points."""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mission_machine.cli import main
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER, EvidenceLabel, Provenance
from mission_machine.explainability.assumptions import ASSUMPTIONS
from mission_machine.explainability.explain import build_recommendation, comparison_table
from mission_machine.mission.library import load_mission
from mission_machine.planning.engine import PlanningEngine

DOCS = Path(__file__).resolve().parents[1] / "docs"

FORBIDDEN_CLAIMS = (
    "operationally validated",
    "operational readiness",
    "field proven",
    "field-proven",
    "certified by",
    "approved by the swedish armed forces",
    "meets swedish armed forces requirements",
    "validated against swedish armed forces",
)


class EvidenceTests(unittest.TestCase):
    def test_disclaimer_states_the_three_things_that_matter(self) -> None:
        text = DEMONSTRATOR_DISCLAIMER.lower()
        self.assertIn("synthetic", text)
        self.assertIn("no military validation", text)
        self.assertIn("decision authority", text)

    def test_provenance_reports_its_weakest_label(self) -> None:
        provenance = Provenance.simulated("test")
        self.assertEqual(provenance.weakest(), EvidenceLabel.UNVALIDATED)

    def test_assumption_register_is_complete_and_unique(self) -> None:
        ids = [assumption.assumption_id for assumption in ASSUMPTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        for assumption in ASSUMPTIONS:
            self.assertTrue(assumption.statement)
            self.assertTrue(assumption.impact_if_wrong)
            self.assertTrue(assumption.where)

    def test_assumption_register_and_document_agree(self) -> None:
        document = (DOCS / "assumptions.md").read_text(encoding="utf-8")
        for assumption in ASSUMPTIONS:
            self.assertIn(
                assumption.assumption_id,
                document,
                f"{assumption.assumption_id} missing from docs/assumptions.md",
            )

    def test_no_document_claims_validation(self) -> None:
        """No document may claim validation, readiness or provenance it lacks.

        The check is negation-aware: a line that *denies* a claim ("no
        operational readiness status") is what the evidence rules require, so
        only an affirmative occurrence fails. ``docs/evidence-rules.md`` is
        exempt because it is the document that states the prohibition and
        therefore has to quote the phrases verbatim.
        """

        negations = ("no ", "not ", "never", "without", "unless", "cannot", "must not")
        for path in list(DOCS.rglob("*.md")) + [DOCS.parent / "README.md"]:
            if path.name == "evidence-rules.md":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                lowered = line.lower()
                for phrase in FORBIDDEN_CLAIMS:
                    if phrase in lowered and not any(word in lowered for word in negations):
                        self.fail(f"{path.name}:{number} claims '{phrase}': {line.strip()}")

    def test_research_questions_are_registered(self) -> None:
        text = (DOCS / "research" / "questions.md").read_text(encoding="utf-8")
        for number in range(1, 7):
            self.assertIn(f"RQ-00{number}", text)


class ExplainabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mission = load_mission()
        cls.engine = PlanningEngine(cls.mission)
        cls.plan = cls.engine.generate_options()
        cls.recommendation = build_recommendation(cls.plan, cls.engine)

    def test_recommendation_never_claims_authority(self) -> None:
        self.assertTrue(self.recommendation.operator_decision_required)
        self.assertIn("Operator decision", self.recommendation.decision_prompt)
        self.assertEqual(self.recommendation.disclaimer, DEMONSTRATOR_DISCLAIMER)

    def test_recommendation_explains_itself_and_the_alternatives(self) -> None:
        self.assertTrue(self.recommendation.why)
        self.assertEqual(len(self.recommendation.alternatives), 2)
        for alternative in self.recommendation.alternatives:
            self.assertTrue(alternative["why"])

    def test_confidence_comes_from_perturbations(self) -> None:
        confidence = self.recommendation.confidence
        self.assertIn(confidence.level, {"HIGH", "MEDIUM", "LOW"})
        self.assertEqual(len(confidence.variants), 4)
        for variant in confidence.variants:
            self.assertIn("critical_assurance_holds", variant)

    def test_comparison_marks_a_best_value_per_dimension(self) -> None:
        rows = comparison_table(self.plan.options)
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(any(value["is_best"] for value in row["values"]), row["metric"])

    def test_plan_serialises_to_json(self) -> None:
        payload = json.dumps(self.plan.to_dict(include_steps=True))
        self.assertIn("SYNTHETIC", payload)


class CliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_mission_command(self) -> None:
        code, output = self._run(["mission"])
        self.assertEqual(code, 0)
        self.assertIn("MM-DEMO-001", output)
        self.assertIn("SYNTHETIC", output)

    def test_configure_command_json(self) -> None:
        code, output = self._run(["--json", "configure", "--fast"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(len(payload["plan"]["options"]), 3)
        self.assertIn("recommendation", payload)

    def test_verify_command_passes(self) -> None:
        code, output = self._run(["verify"])
        self.assertEqual(code, 0)
        self.assertIn("physics PASS", output)

    def test_demo_command_runs_the_whole_flow(self) -> None:
        code, output = self._run(["demo"])
        self.assertEqual(code, 0)
        for heading in (
            "STEP 3 - GENERATE CONFIGURATIONS",
            "STEP 4 - COMPARE",
            "STEP 5 - OPERATOR SELECTS A CONFIGURATION",
            "WHAT CHANGED",
            "WHY IT MATTERS",
            "DECISION LOG",
        ):
            self.assertIn(heading, output)

    def test_assumptions_command(self) -> None:
        code, output = self._run(["assumptions"])
        self.assertEqual(code, 0)
        self.assertIn("AS-001", output)

    def test_export_lp_command(self) -> None:
        code, output = self._run(["export-lp"])
        self.assertEqual(code, 0)
        self.assertIn("Subject To", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
