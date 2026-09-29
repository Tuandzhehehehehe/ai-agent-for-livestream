"""Run fixed synthetic golden cases and write reproducible evaluation reports."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ai.customer_qa import (
    CustomerQuestionRequest,
    classify_intent,
    draft_customer_answer,
)
from ai.data import FAQ, SalesPolicy, load_synthetic_data
from ai.data.models import SyntheticDataset
from ai.evaluation.metrics import ratio_metric
from ai.operations import (
    OperationsInputError,
    SimulatedComment,
    SimulatedEvent,
    classify_comment,
    summarize_session,
)

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON_REPORT = PROJECT_ROOT / "reports" / "evaluation_report.json"
DEFAULT_MARKDOWN_REPORT = PROJECT_ROOT / "docs" / "EVALUATION_REPORT.md"


class EvaluationDataError(ValueError):
    """Raised when the evaluation fixture is missing or malformed."""


def load_scenarios(path: str | Path = SCENARIOS_PATH) -> dict[str, Any]:
    """Load and minimally validate the separate synthetic evaluation fixture."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationDataError(f"Cannot read evaluation scenarios {source}: {error}") from error
    except json.JSONDecodeError as error:
        raise EvaluationDataError(
            f"Malformed evaluation JSON {source} at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(payload, dict):
        raise EvaluationDataError("Evaluation scenarios must be a JSON object")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("data_origin") != "synthetic":
        raise EvaluationDataError("Evaluation scenarios must be labeled synthetic")
    for key in ("customer_qa_cases", "comment_cases", "operations_cases"):
        if not isinstance(payload.get(key), list):
            raise EvaluationDataError(f"Evaluation scenarios field {key!r} must be an array")
    return payload


def run_evaluation(path: str | Path = SCENARIOS_PATH) -> dict[str, Any]:
    """Evaluate customer Q&A, comment classification, and operations behavior.

    Results are deterministic for a fixed scenario file and catalog. This
    synthetic case score is not a generalization estimate for real customers.
    """
    scenarios = load_scenarios(path)
    dataset = load_synthetic_data()
    product_names = tuple(product.name for product in dataset.products)

    qa_results: list[dict[str, Any]] = []
    qa_intent_correct = 0
    qa_outcome_correct = 0
    qa_review_true_positive = 0
    qa_review_denominator = 0
    unsupported_answer_count = 0
    unsupported_case_count = 0
    answered_count = 0
    answered_with_valid_sources = 0

    for case in scenarios["customer_qa_cases"]:
        case_id = _required_case_id(case)
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise EvaluationDataError(f"{case_id}: expected must be an object")
        text = case.get("text")
        if not isinstance(text, str):
            raise EvaluationDataError(f"{case_id}: text must be a string")
        case_dataset = _dataset_variant(dataset, case.get("dataset_variant", "base"))
        case_valid_source_ids = _valid_source_ids(case_dataset)
        classification = classify_intent(text, product_names=product_names)
        answer = draft_customer_answer(
            CustomerQuestionRequest(session_id=f"eval-{case_id}", text=text),
            dataset=case_dataset,
        )
        source_ids = sorted(source.source_id for source in answer.sources)
        source_refs_valid = all(
            source.source_id in case_valid_source_ids.get(source.source_type, set())
            for source in answer.sources
        )
        expected_ids = sorted(expected.get("source_ids", []))
        contains_match = all(
            phrase.casefold() in answer.answer.casefold()
            for phrase in expected.get("answer_contains", [])
        )
        intent_correct = classification.intent == expected.get("intent")
        if intent_correct:
            qa_intent_correct += 1
        review_expected = bool(expected.get("needs_human_review"))
        if review_expected:
            qa_review_denominator += 1
            qa_review_true_positive += answer.needs_human_review
        if case.get("unsupported_evidence", False):
            unsupported_case_count += 1
            unsupported_answer_count += answer.status == "answered"
        if answer.status == "answered":
            answered_count += 1
            answered_with_valid_sources += bool(answer.sources) and source_refs_valid

        outcome_correct = (
            intent_correct
            and answer.status == expected.get("status")
            and answer.needs_human_review == review_expected
            and source_ids == expected_ids
            and contains_match
            and (answer.status != "answered" or answer.is_draft)
        )
        qa_outcome_correct += outcome_correct
        qa_results.append(
            {
                "case_id": case_id,
                "expected_intent": expected.get("intent"),
                "actual_intent": classification.intent,
                "expected_status": expected.get("status"),
                "actual_status": answer.status,
                "expected_source_ids": expected_ids,
                "actual_source_ids": source_ids,
                "source_references_valid": source_refs_valid,
                "passed": outcome_correct,
            }
        )

    comment_results: list[dict[str, Any]] = []
    comment_intent_correct = 0
    comment_review_true_positive = 0
    comment_review_denominator = 0
    for case in scenarios["comment_cases"]:
        case_id = _required_case_id(case)
        text = case.get("text")
        if not isinstance(text, str):
            raise EvaluationDataError(f"{case_id}: text must be a string")
        comment = SimulatedComment(f"eval-{case_id}", text)
        classification = classify_comment(comment, product_names=product_names)
        expected_intent = case.get("expected_intent")
        expected_review = bool(case.get("expected_needs_human_review"))
        intent_correct = classification.intent == expected_intent
        review_correct = classification.needs_human_review == expected_review
        comment_intent_correct += intent_correct
        if expected_review:
            comment_review_denominator += 1
            comment_review_true_positive += classification.needs_human_review
        comment_results.append(
            {
                "case_id": case_id,
                "expected_intent": expected_intent,
                "actual_intent": classification.intent,
                "expected_needs_human_review": expected_review,
                "actual_needs_human_review": classification.needs_human_review,
                "passed": intent_correct and review_correct,
            }
        )

    operations_results: list[dict[str, Any]] = []
    operations_correct = 0
    duplicate_cases = 0
    duplicate_cases_correct = 0
    recommendation_reference_count = 0
    valid_recommendation_reference_count = 0
    summary_source_count = 0
    valid_summary_source_count = 0

    for case in scenarios["operations_cases"]:
        case_id = _required_case_id(case)
        duplicate_expectation = case.get("duplicate_expectation")
        if duplicate_expectation is not None:
            duplicate_cases += 1
        try:
            summary = summarize_session(
                case.get("comments", []),
                case.get("events", []),
                dataset=dataset,
            )
        except OperationsInputError as error:
            expected_error = case.get("expected_error")
            passed = expected_error == type(error).__name__
            operations_correct += passed
            if duplicate_expectation is not None:
                duplicate_cases_correct += passed and duplicate_expectation == "reject"
            operations_results.append(
                {
                    "case_id": case_id,
                    "expected_error": expected_error,
                    "actual_error": type(error).__name__,
                    "passed": passed,
                }
            )
            continue

        expected_error = case.get("expected_error")
        expected = case.get("expected", {})
        valid_comment_ids = _input_ids(case.get("comments", []), "comment_id")
        valid_event_ids = _input_ids(case.get("events", []), "event_id")
        valid_ops_ids = {"comment": valid_comment_ids, "event": valid_event_ids}
        rec_traceable = all(
            recommendation.is_draft
            and recommendation.text.startswith("Draft recommendation:")
            and bool(recommendation.sources)
            and all(
                source.source_id in valid_ops_ids[source.source_type]
                for source in recommendation.sources
            )
            for recommendation in summary.recommendations
        )
        summary_sources_traceable = all(
            source.source_id in valid_ops_ids[source.source_type]
            for source in summary.sources
        )
        recommendation_reference_count += sum(
            len(recommendation.sources) for recommendation in summary.recommendations
        )
        valid_recommendation_reference_count += sum(
            source.source_id in valid_ops_ids[source.source_type]
            for recommendation in summary.recommendations
            for source in recommendation.sources
        )
        summary_source_count += len(summary.sources)
        valid_summary_source_count += sum(
            source.source_id in valid_ops_ids[source.source_type]
            for source in summary.sources
        )

        frequent = {
            (item.normalized_question, item.count, item.category)
            for item in summary.frequent_questions
        }
        expected_frequent = {
            (item["normalized_question"], item["count"], item["category"])
            for item in expected.get("frequent_questions", [])
        }
        recurring = {
            (item.issue_type, item.source_type, item.count)
            for item in summary.recurring_issues
        }
        expected_recurring = {
            (item["issue_type"], item["source_type"], item["count"])
            for item in expected.get("recurring_issues", [])
        }
        comparisons = {
            "comment_count": summary.comment_count == expected.get("comment_count"),
            "event_count": summary.event_count == expected.get("event_count"),
            "duplicate_comment_count": summary.duplicate_comment_count
            == expected.get("duplicate_comment_count"),
            "duplicate_event_count": summary.duplicate_event_count
            == expected.get("duplicate_event_count"),
            "frequent_questions": frequent == expected_frequent,
            "recurring_issues": recurring == expected_recurring
            if "recurring_issues" in expected
            else True,
            "recommendation_count": len(summary.recommendations)
            == expected.get("recommendation_count"),
            "recommendations_traceable": rec_traceable,
            "summary_sources_traceable": summary_sources_traceable,
            "expected_error_absent": expected_error is None,
        }
        passed = all(comparisons.values())
        operations_correct += passed
        if duplicate_expectation is not None:
            duplicate_cases_correct += passed and duplicate_expectation == "deduplicate"
        operations_results.append(
            {
                "case_id": case_id,
                "expected_error": expected_error,
                "actual_error": None,
                "actual_comment_count": summary.comment_count,
                "actual_event_count": summary.event_count,
                "actual_duplicate_comment_count": summary.duplicate_comment_count,
                "actual_duplicate_event_count": summary.duplicate_event_count,
                "actual_recommendation_count": len(summary.recommendations),
                "checks": comparisons,
                "passed": passed,
            }
        )

    total_intent_cases = len(qa_results) + len(comment_results)
    total_review_cases = qa_review_denominator + comment_review_denominator
    metrics = {
        "intent_classification_accuracy": ratio_metric(
            qa_intent_correct + comment_intent_correct,
            total_intent_cases,
            definition="Correct expected intent labels across customer Q&A and operations comment cases.",
        ),
        "customer_qa_outcome_accuracy": ratio_metric(
            qa_outcome_correct,
            len(qa_results),
            definition="Cases matching expected intent, status, review flag, source IDs, and required answer fragments.",
        ),
        "human_review_escalation_recall": ratio_metric(
            qa_review_true_positive + comment_review_true_positive,
            total_review_cases,
            definition="Expected-review synthetic cases that actually returned needs_review or needs_human_review.",
        ),
        "unsupported_answer_rate": ratio_metric(
            unsupported_answer_count,
            unsupported_case_count,
            definition="Tagged unsupported-evidence Q&A cases that nevertheless returned status=answered.",
        ),
        "source_reference_coverage": ratio_metric(
            answered_with_valid_sources,
            answered_count,
            definition="Answered Q&A results with at least one source ID resolving to a product, FAQ, or policy in the selected synthetic dataset variant.",
        ),
        "operations_case_accuracy": ratio_metric(
            operations_correct,
            len(operations_results),
            definition="Operations scenarios matching expected summaries, aggregation, recommendations, errors, and traceability checks.",
        ),
        "duplicate_handling_correctness": ratio_metric(
            duplicate_cases_correct,
            duplicate_cases,
            definition="Duplicate-specific cases correctly deduplicated identical records or rejected conflicting IDs.",
        ),
        "operations_recommendation_source_traceability": ratio_metric(
            valid_recommendation_reference_count,
            recommendation_reference_count,
            definition="Recommendation source references resolving to supplied comment/event IDs.",
        ),
        "operations_summary_source_traceability": ratio_metric(
            valid_summary_source_count,
            summary_source_count,
            definition="Top-level summary source references resolving to supplied unique comment/event IDs.",
        ),
    }

    return {
        "report_version": 1,
        "evaluation_id": scenarios["metadata"]["dataset_id"],
        "data_origin": "synthetic",
        "catalog": "existing labeled synthetic demo catalog; evaluation scenarios remain separate",
        "scope_note": "These are curated deterministic golden cases, not a representative sample of real customer traffic.",
        "case_counts": {
            "customer_qa": len(qa_results),
            "comment_classification": len(comment_results),
            "operations": len(operations_results),
            "intent_classification_total": total_intent_cases,
            "human_review_expected_total": total_review_cases,
            "unsupported_evidence_total": unsupported_case_count,
            "duplicate_cases_total": duplicate_cases,
        },
        "metrics": metrics,
        "cases": {
            "customer_qa": qa_results,
            "comment_classification": comment_results,
            "operations": operations_results,
        },
        "limitations": [
            "Scenario labels are hand-authored synthetic expected outcomes.",
            "The existing demonstration catalog is fictional; passing it does not validate real business facts.",
            "Intent accuracy measures only the included English keyword cases and is not general-world accuracy.",
            "Unsupported-answer rate is conditional on the explicitly tagged unsupported-evidence cases only.",
            "Prompt-injection cases cover known patterns and do not establish complete injection resistance.",
            "No latency, production traffic, or human reviewer agreement was measured.",
        ],
    }


def write_evaluation_reports(
    report: Mapping[str, Any] | None = None,
    *,
    json_path: str | Path = DEFAULT_JSON_REPORT,
    markdown_path: str | Path = DEFAULT_MARKDOWN_REPORT,
) -> dict[str, Any]:
    """Write matching machine-readable JSON and human-readable Markdown reports."""
    evaluated = dict(report) if report is not None else run_evaluation()
    json_destination = Path(json_path)
    markdown_destination = Path(markdown_path)
    json_destination.parent.mkdir(parents=True, exist_ok=True)
    markdown_destination.parent.mkdir(parents=True, exist_ok=True)
    json_destination.write_text(
        json.dumps(evaluated, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_destination.write_text(_render_markdown(evaluated), encoding="utf-8")
    return evaluated


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Synthetic Evaluation Report",
        "",
        f"Evaluation set: `{report['evaluation_id']}`",
        "",
        "This report measures deterministic behavior on curated synthetic golden cases. It is not a claim of real-world accuracy.",
        "",
        "## Case Counts",
        "",
        "| Slice | Cases |",
        "| --- | ---: |",
    ]
    for label, key in (
        ("Customer Q&A", "customer_qa"),
        ("Comment classification", "comment_classification"),
        ("Operations", "operations"),
        ("Expected human review", "human_review_expected_total"),
        ("Tagged unsupported evidence", "unsupported_evidence_total"),
        ("Duplicate handling", "duplicate_cases_total"),
    ):
        lines.append(f"| {label} | {report['case_counts'][key]} |")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Metric | Result | Numerator / denominator | Interpretation |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for name in sorted(report["metrics"]):
        metric = report["metrics"][name]
        value = metric["value"]
        displayed = "N/A" if value is None else f"{value:.3f} ({value:.1%})"
        lines.append(
            f"| `{name}` | {displayed} | "
            f"{metric['numerator']} / {metric['denominator']} | {metric['definition']} |"
        )
    failures = [
        case
        for cases in report["cases"].values()
        for case in cases
        if not case["passed"]
    ]
    lines.extend(["", "## Case Outcomes", ""])
    if failures:
        lines.extend(["Unexpected outcomes:", ""])
        for case in failures:
            lines.append(f"- `{case['case_id']}`: {json.dumps(case, sort_keys=True)}")
    else:
        lines.append("All included expected outcomes matched.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    lines.extend(
        [
            "",
            "Targets were not specified for this phase; metric entries therefore record measured values and denominators only.",
            "",
        ]
    )
    return "\n".join(lines)


def _required_case_id(case: Any) -> str:
    if not isinstance(case, dict) or not isinstance(case.get("id"), str):
        raise EvaluationDataError("Each evaluation case must have a string id")
    return case["id"]


def _dataset_variant(dataset: SyntheticDataset, variant: str) -> SyntheticDataset:
    if variant == "base":
        return dataset
    if variant == "no_policies":
        return replace(dataset, policies=())
    if variant == "inactive_shipping_only":
        return replace(
            dataset,
            policies=(
                SalesPolicy(
                    "EVAL-POLICY-SHIP-INACTIVE",
                    "shipping",
                    "Synthetic evaluation policy; inactive and not answer evidence.",
                    "inactive",
                    "0.0.1",
                ),
            ),
        )
    if variant == "conflicting_shipping_policies":
        return replace(
            dataset,
            policies=(
                SalesPolicy(
                    "EVAL-POLICY-SHIP-A",
                    "shipping",
                    "Synthetic evaluation rule A: dispatch in two simulated days.",
                    "effective",
                    "1.0.0",
                ),
                SalesPolicy(
                    "EVAL-POLICY-SHIP-B",
                    "shipping",
                    "Synthetic evaluation rule B: dispatch in five simulated days.",
                    "effective",
                    "1.0.0",
                ),
            ),
        )
    if variant == "conflicting_lumanest_price_faq":
        conflict = FAQ(
            "EVAL-FAQ-PRICE-CONFLICT",
            "What is the LumaNest price?",
            "Synthetic evaluation conflict: the price is 99.00.",
            "pricing",
            ("SYN-PROD-001",),
        )
        return replace(dataset, faqs=dataset.faqs + (conflict,))
    raise EvaluationDataError(f"Unknown dataset variant {variant!r}")


def _valid_source_ids(dataset: SyntheticDataset) -> dict[str, set[str]]:
    return {
        "product": {item.id for item in dataset.products},
        "faq": {item.id for item in dataset.faqs},
        "policy": {item.id for item in dataset.policies},
    }


def _input_ids(records: Any, key: str) -> set[str]:
    return {
        record[key]
        for record in records
        if isinstance(record, dict) and isinstance(record.get(key), str)
    }


def main() -> None:
    report = write_evaluation_reports()
    print(json.dumps(report["metrics"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()