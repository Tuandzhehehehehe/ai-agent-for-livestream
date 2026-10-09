# AI, Backend, and Extension Architecture

## Scope

The `ai` Python package contains data processing, retrieval, customer Q&A,
operations assistance, and deterministic evaluation. The repository also
contains a local FastAPI backend and a read-only YouTube Live comment
collector. There is no dashboard, database, authentication, approval workflow,
or transaction execution.

## Module boundaries

- `ai.schemas`: typed request, response, and source-reference models.
- `ai.data`: validated loading of explicitly synthetic catalog, FAQ, and
  policy records.
- `ai.retrieval`: deterministic search over approved records.
- `ai.customer_qa`: intent classification, safety checks, evidence selection,
  and draft response decisions.
- `ai.operations`: comment classification, question aggregation, session
  summaries, and evidence-backed recommendations.
- `ai.evaluation`: reproducible scenarios and metrics; measured results must
  be kept distinct from target thresholds.
- `backend.local_model`: local TF-IDF character n-gram and Logistic Regression
  intent classifier, trained from `backend/intent_training_data.json`.
- `backend.main`: HTTP ingestion, rate limiting, moderation, retrieval-backed
  answer drafts, and in-memory summaries.
- `extension`: YouTube Live comment collector; it does not post chat messages.

The `ai.data` package provides typed product, FAQ, and policy records, validates
the bundled JSON files, and exposes simple access and keyword-search
functions. Its bundled records are labeled synthetic demo data and are not
real products or business policies. `ai.customer_qa` adds a deterministic
keyword intent baseline, category/entity-filtered retrieval, and draft answers
grounded in those records. Unsupported, conflicting, consequential, and
injection-shaped requests are routed for human review. This is baseline safety,
not a complete prompt-injection defense. All customer-facing answers remain
drafts; the package never executes consequential actions.

The customer Q&A entry point rejects empty/invalid text, limits messages to
4096 characters, and routes recognized order, payment, return, and price-change
actions to human review. These deterministic keyword and pattern checks are
safeguards, not a complete security boundary.

`ai.operations` accepts supplied simulated comments and events, reuses the
customer-Q&A classifier and safety patterns, deduplicates identical records by
ID, aggregates repeated questions and review-worthy comments, and groups
allowlisted simulated event types. It emits summaries and draft recommendations
with comment/event references. Arbitrary event descriptions do not authorize
actions; injection-shaped and overlong content is flagged and source excerpts
are bounded.

The local classifier runs only when deterministic rules return `unknown`.
It does not generate answer text. Customer answers still come from retrieval
over approved synthetic data; missing evidence produces `needs_review`. No
external LLM provider is called by the backend.

`ai.evaluation` runs deterministic golden cases from a separate synthetic
fixture and reports intent/outcome accuracy, escalation recall, unsupported
answer rate, source coverage, duplicate handling, and operations traceability.
It writes machine-readable JSON and a matching Markdown report. Metrics include
their denominators and do not estimate real-world accuracy.

## Dependency direction

Data and schemas are independent of the HTTP backend. Retrieval uses the data
layer; Q&A and operations use schemas and retrieval. The backend imports these
Python APIs and exposes the comment/summary routes. The local model depends on
scikit-learn and the synthetic training corpus; no API credentials are needed.

## Phase boundaries

Phase 0 established package metadata and contracts. Phase 1 implements
the synthetic data layer; Phase 2 implements deterministic retrieval and
customer Q&A; Phase 3 adds a bounded deterministic safety/review gate; Phase 4
adds offline operations analysis; Phase 5 evaluates these modules against
curated synthetic cases. The local classifier was added separately and is not
measured by the golden-case report. Prompt-injection detection remains a basic
pattern baseline.