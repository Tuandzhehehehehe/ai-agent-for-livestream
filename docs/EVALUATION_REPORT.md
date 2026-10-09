# Synthetic Evaluation Report

Evaluation set: `SYN-EVAL-PHASE5-001`

These are curated deterministic golden cases, not a representative sample of real customer traffic. They do not evaluate the separate backend local intent classifier, which is trained on synthetic examples.

## Case Counts

| Slice | Cases |
| --- | ---: |
| Customer Q&A | 19 |
| Comment classification | 13 |
| Operations | 6 |
| Expected human review | 20 |
| Tagged unsupported evidence | 11 |
| Duplicate handling | 2 |

## Metrics

| Metric | Result | Numerator / denominator | Interpretation |
| --- | ---: | ---: | --- |
| `customer_qa_outcome_accuracy` | 1.000 (100.0%) | 19 / 19 | Cases matching expected intent, status, review flag, source IDs, and required answer fragments. |
| `duplicate_handling_correctness` | 1.000 (100.0%) | 2 / 2 | Duplicate-specific cases correctly deduplicated identical records or rejected conflicting IDs. |
| `human_review_escalation_recall` | 1.000 (100.0%) | 20 / 20 | Expected-review synthetic cases that actually returned needs_review or needs_human_review. |
| `intent_classification_accuracy` | 1.000 (100.0%) | 32 / 32 | Correct expected intent labels across customer Q&A and operations comment cases. |
| `operations_case_accuracy` | 1.000 (100.0%) | 6 / 6 | Operations scenarios matching expected summaries, aggregation, recommendations, errors, and traceability checks. |
| `operations_recommendation_source_traceability` | 1.000 (100.0%) | 7 / 7 | Recommendation source references resolving to supplied comment/event IDs. |
| `operations_summary_source_traceability` | 1.000 (100.0%) | 12 / 12 | Top-level summary source references resolving to supplied unique comment/event IDs. |
| `source_reference_coverage` | 1.000 (100.0%) | 5 / 5 | Answered Q&A results with at least one source ID resolving to a product, FAQ, or policy in the selected synthetic dataset variant. |
| `unsupported_answer_rate` | 0.000 (0.0%) | 0 / 11 | Tagged unsupported-evidence Q&A cases that nevertheless returned status=answered. |

## Case Outcomes

All included expected outcomes matched.

## Limitations

- Scenario labels are hand-authored synthetic expected outcomes.
- The existing demonstration catalog is fictional; passing it does not validate real business facts.
- Intent accuracy measures only the included English keyword cases and is not general-world accuracy.
- The backend local TF-IDF classifier is evaluated separately in docs/LOCAL_MODEL_EVALUATION.md; its synthetic holdout is not measured by this report.
- Unsupported-answer rate is conditional on the explicitly tagged unsupported-evidence cases only.
- Prompt-injection cases cover known patterns and do not establish complete injection resistance.
- No latency, production traffic, or human reviewer agreement was measured.

Targets were not specified for this phase; metric entries therefore record measured values and denominators only.
