import json
import tempfile
import unittest
from pathlib import Path

from ai.evaluation.metrics import ratio_metric
from ai.evaluation.runner import (
    DEFAULT_JSON_REPORT,
    DEFAULT_MARKDOWN_REPORT,
    SCENARIOS_PATH,
    _render_markdown,
    run_evaluation,
    write_evaluation_reports,
)


class SyntheticEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_evaluation()

    def test_scenarios_are_labeled_and_separate_from_demo_catalog(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(scenarios["metadata"]["data_origin"], "synthetic")
        self.assertIn("EVALUATION", scenarios["metadata"]["dataset_label"])
        self.assertEqual(len(scenarios["customer_qa_cases"]), 19)
        self.assertEqual(len(scenarios["comment_cases"]), 13)
        self.assertEqual(len(scenarios["operations_cases"]), 6)

    def test_every_golden_case_matches_its_expected_outcome(self) -> None:
        failed = [
            case
            for cases in self.report["cases"].values()
            for case in cases
            if not case["passed"]
        ]
        self.assertEqual(failed, [])
        self.assertTrue(
            all(
                case["source_references_valid"]
                for case in self.report["cases"]["customer_qa"]
            )
        )

    def test_metrics_report_explicit_denominators(self) -> None:
        metrics = self.report["metrics"]
        self.assertEqual(
            (metrics["intent_classification_accuracy"]["numerator"],
             metrics["intent_classification_accuracy"]["denominator"]),
            (32, 32),
        )
        self.assertEqual(
            (metrics["human_review_escalation_recall"]["numerator"],
             metrics["human_review_escalation_recall"]["denominator"]),
            (20, 20),
        )
        self.assertEqual(
            (metrics["unsupported_answer_rate"]["numerator"],
             metrics["unsupported_answer_rate"]["denominator"]),
            (0, 11),
        )
        self.assertEqual(
            (metrics["source_reference_coverage"]["numerator"],
             metrics["source_reference_coverage"]["denominator"]),
            (5, 5),
        )
        self.assertEqual(
            (metrics["duplicate_handling_correctness"]["numerator"],
             metrics["duplicate_handling_correctness"]["denominator"]),
            (2, 2),
        )
        self.assertEqual(
            metrics["operations_recommendation_source_traceability"]["value"],
            1.0,
        )

    def test_empty_metric_denominator_is_not_invented(self) -> None:
        result = ratio_metric(0, 0, definition="No measured cases.")
        self.assertIsNone(result["value"])
        self.assertEqual(result["target"], None)

    def test_evaluation_is_reproducible(self) -> None:
        self.assertEqual(self.report, run_evaluation())

    def test_checked_in_reports_match_current_evaluation(self) -> None:
        saved_report = json.loads(
            DEFAULT_JSON_REPORT.read_text(encoding="utf-8")
        )
        saved_markdown = DEFAULT_MARKDOWN_REPORT.read_text(encoding="utf-8")
        self.assertEqual(saved_report, self.report)
        self.assertEqual(saved_markdown, _render_markdown(saved_report))

    def test_report_writer_emits_matching_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            json_path = Path(temporary_directory) / "nested" / "report.json"
            markdown_path = Path(temporary_directory) / "docs" / "report.md"
            written = write_evaluation_reports(
                self.report,
                json_path=json_path,
                markdown_path=markdown_path,
            )
            reloaded = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(reloaded, written)
        self.assertIn("Synthetic Evaluation Report", markdown)
        self.assertIn("32 / 32", markdown)
        self.assertIn("not a claim of real-world accuracy", markdown)


if __name__ == "__main__":
    unittest.main()