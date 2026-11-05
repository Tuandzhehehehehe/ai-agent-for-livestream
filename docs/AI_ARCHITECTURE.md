# AI, Backend, and Extension Architecture

## Scope

The `ai` Python package contains data processing, retrieval, customer Q&A,
operations assistance, and deterministic evaluation. The repository also
contains a local FastAPI backend, a read-only YouTube
Live collector, and MongoDB/Redis infrastructure modules. The database modules
are not yet wired into the live API routes, which still use in-memory storage.
Authentication, approval UI, and transaction execution are not implemented.

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
- `backend.database`, `backend.repositories`, `backend.infrastructure`:
  multi-tenant MongoDB/Redis managers, models, repositories, cache, and stream
  primitives. These are currently a separate integration layer, not the live
  route persistence implementation.
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
cloud LLM provider is called by the backend. A local Ollama model is optional;
when enabled, it receives only the question and retrieved evidence and produces
a draft. If Ollama is disabled, unavailable, or returns an unusable response,
the deterministic answer service remains the fallback.

## Retrieval choice

The bundled corpus currently has 18 records: 6 products, 8 FAQs, and 4 sales
policies. `ai.customer_qa.retrieval.retrieve_evidence` uses deterministic
keyword candidates, intent/category filtering, product-name matching, and
effective-policy filtering, then returns typed source references. A small
local probe confirmed a shipping paraphrase resolves to
`SYN-POLICY-SHIPPING-001`, a product-color question resolves to its product
and FAQ records, and an unrelated sports question returns no evidence.

A vector database is not justified for this corpus yet. FAISS/Chroma would add
an embedding model, index lifecycle, and dependency surface without a labeled
retrieval benchmark showing better recall on this small synthetic dataset.
Keep the current deterministic retriever as the baseline; reconsider a single
local vector index when the catalog/FAQ corpus grows materially and a held-out
retrieval evaluation demonstrates a measurable benefit.

`ai.evaluation` runs deterministic golden cases from a separate synthetic
fixture and reports intent/outcome accuracy, escalation recall, unsupported
answer rate, source coverage, duplicate handling, and operations traceability.
It writes machine-readable JSON and a matching Markdown report. Metrics include
their denominators and do not estimate real-world accuracy.

## Dependency direction

Data and schemas are independent of the HTTP backend. Retrieval uses the data
layer; Q&A and operations use schemas and retrieval. The backend imports these
Python APIs and exposes the comment/summary routes. The local model depends on
scikit-learn and the synthetic training corpus. Optional local Ollama uses its
loopback HTTP API and needs no cloud credentials or automatic model download.
The MongoDB/Redis packages are not wired into `backend.main` yet; current live
comments and rate limits remain in process memory.

## Phase boundaries

Phase 0 established package metadata and contracts. Phase 1 implements
the synthetic data layer; Phase 2 implements deterministic retrieval and
customer Q&A; Phase 3 adds a bounded deterministic safety/review gate; Phase 4
adds offline operations analysis; Phase 5 evaluates these modules against
curated synthetic cases. The local classifier was added separately and is not
measured by the golden-case report. Prompt-injection detection remains a basic
pattern baseline.