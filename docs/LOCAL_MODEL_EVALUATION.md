# Local Intent Model Training Report

Model: `tfidf-char-logreg-v1`
Training dataset: `SYN-LOCAL-INTENT-TRAIN-002` (260 examples)
Holdout dataset: `SYN-LOCAL-INTENT-HOLDOUT-001` (65 examples)
Confidence threshold: `0.100`
Top-class margin threshold: `0.025`

All data in this report is synthetic and hand-authored; these metrics do not estimate production performance.

## Metrics

| Metric | Result |
| --- | ---: |
| `accuracy` | 0.754 (75.4%) |
| `macro_precision` | 0.918 (91.8%) |
| `macro_recall` | 0.754 (75.4%) |
| `macro_f1` | 0.777 (77.7%) |
| `weighted_f1` | 0.777 (77.7%) |
| `decision_coverage` | 0.708 (70.8%) |
| `answerable_coverage` | 0.767 (76.7%) |
| `selective_accuracy` | 0.957 (95.7%) |
| `unknown_false_accept_rate` | 0.000 (0.0%) |

## Per-Intent Results

| Intent | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| `complaint` | 1.000 | 0.200 | 0.333 | 5 |
| `consequential_action` | 1.000 | 0.800 | 0.889 | 5 |
| `price_change_request` | 1.000 | 1.000 | 1.000 | 5 |
| `price_question` | 1.000 | 0.800 | 0.889 | 5 |
| `product_information` | 1.000 | 0.600 | 0.750 | 5 |
| `promotion_question` | 1.000 | 0.800 | 0.889 | 5 |
| `prompt_injection` | 1.000 | 1.000 | 1.000 | 5 |
| `refund_request` | 1.000 | 1.000 | 1.000 | 5 |
| `return_policy` | 1.000 | 0.600 | 0.750 | 5 |
| `shipping_policy` | 0.667 | 0.800 | 0.727 | 5 |
| `spam_or_irrelevant` | 1.000 | 0.800 | 0.889 | 5 |
| `stock_question` | 1.000 | 0.400 | 0.571 | 5 |
| `unknown` | 0.263 | 1.000 | 0.417 | 5 |

## Confusion Matrix

Rows are expected labels; columns are predicted labels.

| Expected / predicted | `complaint` | `consequential_action` | `price_change_request` | `price_question` | `product_information` | `promotion_question` | `prompt_injection` | `refund_request` | `return_policy` | `shipping_policy` | `spam_or_irrelevant` | `stock_question` | `unknown` |
| --- |--- |--- |--- |--- |--- |--- |--- |--- |--- |--- |--- |--- |--- |
| `complaint` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 3 |
| `consequential_action` | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `price_change_request` | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `price_question` | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `product_information` | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| `promotion_question` | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `prompt_injection` | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| `refund_request` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 |
| `return_policy` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1 | 0 | 0 | 1 |
| `shipping_policy` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 1 |
| `spam_or_irrelevant` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 1 |
| `stock_question` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 3 |
| `unknown` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |

## Limitations

- Training and holdout examples are synthetic and hand-authored, not real livestream traffic.
- The holdout is small (five examples per intent); scores are not production estimates.
- No stream/channel grouping is available for this synthetic dataset.
- Confidence scores are not calibrated probabilities.
