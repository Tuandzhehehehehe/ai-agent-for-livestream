"""CPU-trained local intent classifier for livestream comments."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast, get_args

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from ai.customer_qa.schemas import Intent

MODEL_NAME = "tfidf-char-logreg-v1"
TRAINING_DATA_PATH = Path(__file__).with_name("intent_training_data.json")
HOLDOUT_DATA_PATH = Path(__file__).with_name("intent_holdout_data.json")
DEFAULT_JSON_REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "local_intent_model_report.json"
DEFAULT_MARKDOWN_REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "LOCAL_MODEL_EVALUATION.md"
DEFAULT_CONFIDENCE_THRESHOLD = 0.10
DEFAULT_CONFIDENCE_MARGIN = 0.025


@dataclass(frozen=True, slots=True)
class IntentPrediction:
    intent: Intent
    confidence: float
    margin: float


class LocalIntentModel:
    def __init__(
        self,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        training_data_path: str | Path = TRAINING_DATA_PATH,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")

        training_path = Path(training_data_path)
        training_data = json.loads(training_path.read_text(encoding="utf-8"))
        examples_by_intent = training_data.get("examples")
        if not isinstance(examples_by_intent, dict):
            raise ValueError("Intent training data must contain an examples object")

        supported_intents = set(get_args(Intent))
        if set(examples_by_intent) != supported_intents:
            raise ValueError("Intent training data labels do not match the Intent schema")

        texts: list[str] = []
        labels: list[str] = []
        for intent, examples in examples_by_intent.items():
            if not isinstance(examples, list) or len(examples) < 2:
                raise ValueError(f"Intent {intent!r} needs at least two training examples")
            for text in examples:
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"Intent {intent!r} has an invalid training example")
                texts.append(text)
                labels.append(intent)

        self.training_example_count = len(texts)
        self.training_dataset_id = training_data.get("metadata", {}).get(
            "dataset_id", training_path.name
        )
        self.confidence_threshold = confidence_threshold
        self.confidence_margin = DEFAULT_CONFIDENCE_MARGIN
        self._pipeline = Pipeline(
            steps=[
                (
                    "tfidf",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(2, 5),
                        sublinear_tf=True,
                        max_features=30_000,
                    ),
                ),
                (
                    "classifier",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=0,
                    ),
                ),
            ]
        )
        self._pipeline.fit(texts, labels)

    def predict(self, text: str) -> IntentPrediction:
        probabilities = self._pipeline.predict_proba([text])[0]
        classifier = self._pipeline.named_steps["classifier"]
        best_index = int(probabilities.argmax())
        confidence = float(probabilities[best_index])
        second_index = int(probabilities.argsort()[-2])
        margin = confidence - float(probabilities[second_index])
        predicted_intent = cast(Intent, classifier.classes_[best_index])
        if (
            confidence < self.confidence_threshold
            or margin < self.confidence_margin
        ):
            predicted_intent = "unknown"
        return IntentPrediction(predicted_intent, confidence, margin)


def evaluate_local_model(
    model: LocalIntentModel,
    *,
    holdout_data_path: str | Path = HOLDOUT_DATA_PATH,
) -> dict[str, Any]:
    """Measure the trained classifier on a separate synthetic holdout set."""
    holdout_path = Path(holdout_data_path)
    holdout_data = json.loads(holdout_path.read_text(encoding="utf-8"))
    examples_by_intent = holdout_data.get("examples")
    supported_intents = tuple(sorted(get_args(Intent)))
    if not isinstance(examples_by_intent, dict):
        raise ValueError("Holdout data must contain an examples object")
    if set(examples_by_intent) != set(supported_intents):
        raise ValueError("Holdout labels do not match the Intent schema")

    expected: list[Intent] = []
    predicted: list[Intent] = []
    for intent in supported_intents:
        examples = examples_by_intent[intent]
        if not isinstance(examples, list) or not examples:
            raise ValueError(f"Holdout intent {intent!r} needs examples")
        for text in examples:
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Holdout intent {intent!r} has invalid text")
            expected.append(intent)
            predicted.append(model.predict(text).intent)

    precision, recall, f1, support = precision_recall_fscore_support(
        expected,
        predicted,
        labels=supported_intents,
        zero_division=0,
    )
    per_intent = {
        intent: {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        for index, intent in enumerate(supported_intents)
    }
    unknown_indices = [
        index for index, label in enumerate(expected) if label == "unknown"
    ]
    answerable_indices = [
        index for index, label in enumerate(expected) if label != "unknown"
    ]
    accepted_indices = [
        index for index, label in enumerate(predicted) if label != "unknown"
    ]
    accepted_answerable_indices = [
        index for index in answerable_indices if predicted[index] != "unknown"
    ]
    metrics = {
        "accuracy": float(accuracy_score(expected, predicted)),
        "macro_precision": float(precision.mean()),
        "macro_recall": float(recall.mean()),
        "macro_f1": float(f1.mean()),
        "weighted_f1": float(
            precision_recall_fscore_support(
                expected,
                predicted,
                labels=supported_intents,
                average="weighted",
                zero_division=0,
            )[2]
        ),
        "decision_coverage": len(accepted_indices) / len(expected),
        "answerable_coverage": (
            len(accepted_answerable_indices) / len(answerable_indices)
            if answerable_indices
            else 0.0
        ),
        "selective_accuracy": (
            sum(predicted[index] == expected[index] for index in accepted_indices)
            / len(accepted_indices)
            if accepted_indices
            else 0.0
        ),
        "unknown_false_accept_rate": (
            sum(predicted[index] != "unknown" for index in unknown_indices)
            / len(unknown_indices)
            if unknown_indices
            else 0.0
        ),
    }
    return {
        "report_version": 1,
        "model": MODEL_NAME,
        "training_dataset_id": model.training_dataset_id,
        "training_examples": model.training_example_count,
        "holdout_dataset_id": holdout_data.get("metadata", {}).get(
            "dataset_id", holdout_path.name
        ),
        "holdout_examples": len(expected),
        "examples_per_intent": {
            intent: int(support[index])
            for index, intent in enumerate(supported_intents)
        },
        "confidence_threshold": model.confidence_threshold,
        "confidence_margin": model.confidence_margin,
        "metrics": metrics,
        "per_intent": per_intent,
        "confusion_matrix_labels": list(supported_intents),
        "confusion_matrix": confusion_matrix(
            expected,
            predicted,
            labels=supported_intents,
        ).tolist(),
        "limitations": [
            "Training and holdout examples are synthetic and hand-authored, not real livestream traffic.",
            "The holdout is small (five examples per intent); scores are not production estimates.",
            "No stream/channel grouping is available for this synthetic dataset.",
            "Confidence scores are not calibrated probabilities.",
        ],
    }


def write_local_model_report(
    report: dict[str, Any],
    *,
    json_path: str | Path = DEFAULT_JSON_REPORT_PATH,
    markdown_path: str | Path = DEFAULT_MARKDOWN_REPORT_PATH,
) -> None:
    json_destination = Path(json_path)
    markdown_destination = Path(markdown_path)
    json_destination.parent.mkdir(parents=True, exist_ok=True)
    markdown_destination.parent.mkdir(parents=True, exist_ok=True)
    json_destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_destination.write_text(_render_local_model_report(report), encoding="utf-8")


def _render_local_model_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Local Intent Model Training Report",
        "",
        f"Model: `{report['model']}`",
        f"Training dataset: `{report['training_dataset_id']}` ({report['training_examples']} examples)",
        f"Holdout dataset: `{report['holdout_dataset_id']}` ({report['holdout_examples']} examples)",
        f"Confidence threshold: `{report['confidence_threshold']:.3f}`",
        f"Top-class margin threshold: `{report['confidence_margin']:.3f}`",
        "",
        "All data in this report is synthetic and hand-authored; these metrics do not estimate production performance.",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
    ]
    for name in (
        "accuracy",
        "macro_precision",
        "macro_recall",
        "macro_f1",
        "weighted_f1",
        "decision_coverage",
        "answerable_coverage",
        "selective_accuracy",
        "unknown_false_accept_rate",
    ):
        lines.append(f"| `{name}` | {metrics[name]:.3f} ({metrics[name]:.1%}) |")

    lines.extend(
        [
            "",
            "## Per-Intent Results",
            "",
            "| Intent | Precision | Recall | F1 | Support |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for intent, result in report["per_intent"].items():
        lines.append(
            f"| `{intent}` | {result['precision']:.3f} | {result['recall']:.3f} "
            f"| {result['f1']:.3f} | {result['support']} |"
        )

    lines.extend(["", "## Confusion Matrix", "", "Rows are expected labels; columns are predicted labels.", ""])
    labels = report["confusion_matrix_labels"]
    lines.append("| Expected / predicted | " + " | ".join(f"`{label}`" for label in labels) + " |")
    lines.append("| " + "--- |" * (len(labels) + 1))
    for label, row in zip(labels, report["confusion_matrix"]):
        lines.append(f"| `{label}` | " + " | ".join(str(value) for value in row) + " |")

    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


@lru_cache(maxsize=1)
def get_local_intent_model() -> LocalIntentModel:
    configured_threshold = float(
        os.environ.get(
            "LOCAL_INTENT_CONFIDENCE_THRESHOLD",
            str(DEFAULT_CONFIDENCE_THRESHOLD),
        )
    )
    return LocalIntentModel(confidence_threshold=configured_threshold)


if __name__ == "__main__":
    model = LocalIntentModel()
    report = evaluate_local_model(model)
    write_local_model_report(report)
    print(
        f"Trained {MODEL_NAME} on {model.training_example_count} examples; "
        f"holdout accuracy={report['metrics']['accuracy']:.3f}, "
        f"macro_f1={report['metrics']['macro_f1']:.3f}."
    )
    print(f"Wrote {DEFAULT_MARKDOWN_REPORT_PATH} and {DEFAULT_JSON_REPORT_PATH}.")