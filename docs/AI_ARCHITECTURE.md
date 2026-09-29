# AI and Data Architecture (Proposal)

## Scope

The `ai` Python package will contain AI-facing data processing, retrieval,
customer Q&A, operations assistance, and evaluation. It will not contain the
main backend API, user interface, livestream simulator, authentication,
approval workflow, or transaction execution.

## Proposed module boundaries

- `ai.schemas`: typed request, response, and source-reference models.
- `ai.data`: validated loading of explicitly synthetic catalog, FAQ, and
  policy records.
- `ai.retrieval`: deterministic search over approved records.
- `ai.customer_qa`: intent classification, safety checks, evidence selection,
  and draft response decisions.
- `ai.providers`: provider interface and deterministic offline mock.
- `ai.operations`: comment classification, question aggregation, session
  summaries, and evidence-backed recommendations.
- `ai.evaluation`: reproducible scenarios and metrics; measured results must
  be kept distinct from target thresholds.

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

`ai.evaluation` runs deterministic golden cases from a separate synthetic
fixture and reports intent/outcome accuracy, escalation recall, unsupported
answer rate, source coverage, duplicate handling, and operations traceability.
It writes machine-readable JSON and a matching Markdown report. Metrics include
their denominators and do not estimate real-world accuracy.

## Dependency direction

Data and schemas are independent of the backend and frontend. Retrieval uses
the data layer; Q&A and operations use schemas, retrieval, and a provider
interface. Evaluation exercises these components without external services.
The integration layer owned by Member 2 can call typed Python functions and
decide how to expose their results through the main API.

## Phase boundaries

Phase 0 established package metadata and proposed contracts. Phase 1 implements
the synthetic data layer; Phase 2 implements deterministic retrieval and
customer Q&A; Phase 3 adds a bounded deterministic safety/review gate; Phase 4
adds offline operations analysis; Phase 5 evaluates these modules against
curated synthetic cases. Prompt-injection detection remains a basic pattern
baseline.