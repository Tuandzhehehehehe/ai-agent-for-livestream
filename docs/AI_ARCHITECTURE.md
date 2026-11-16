# AI, Backend, and Extension Architecture

## Scope

The `ai` Python package contains multi-agent orchestration, vector/hybrid RAG
retrieval, tool/function calling, customer Q&A, operations assistance, and
deterministic evaluation. The repository also contains a FastAPI backend wired
to multi-tenant MongoDB/Redis infrastructure (with automatic in-memory fallback),
a React / Next.js operator dashboard (`frontend/` and `/dashboard`), and a
YouTube Live browser extension with private moderator/AI comment threads.

## Module boundaries

- `ai.multi_agent`: Multi-Agent StateGraph orchestrator (`LangGraph` compatible
  with built-in graph execution fallback) coordinating 5 specialized agents:
  `ModerationGuardAgent`, `IntentTriageAgent`, `ToolCallingRAGAgent`,
  `AnswerSynthesisReviewAgent`, and `OperationsAnalyticsAgent`.
- `ai.vector_rag`: Hybrid Vector RAG store supporting `FAISS`, `ChromaDB`, and
  built-in cosine similarity vector embeddings over products, FAQs, and active
  sales policies.
- `ai.agent_tools`: OpenAI / Ollama / LangChain compatible function-calling tool
  registry and schemas (`get_tool_definitions`, `execute_agent_tool`).
- `ai.schemas` / `ai.customer_qa`: typed request, response, intent classification,
  safety checks, evidence selection, and draft response decisions.
- `ai.data`: validated loading of synthetic catalog, FAQ, and policy records.
- `ai.operations`: comment classification, question aggregation, session
  summaries, and evidence-backed recommendations.
- `ai.evaluation`: reproducible scenarios and metrics; measured results must
  be kept distinct from target thresholds.
- `backend.local_model`: local TF-IDF character n-gram and Logistic Regression
  intent classifier, trained from `backend/intent_training_data.json`.
- `backend.ollama_provider`: local Ollama LLM client supporting both evidence-grounded
  answer synthesis and native LLM tool/function calling (`Qwen` / `Llama`).
- `backend.main`: HTTP ingestion, multi-agent & RAG endpoints, rate limiting,
  moderation, private comment threads, and MongoDB/Redis persistence via
  `StoreDatabaseFacade`.
- `backend.database`, `backend.repositories`, `backend.infrastructure`:
  multi-tenant MongoDB/Redis managers, models, repositories, cache, and stream
  primitives.
- `frontend`: Next.js / React operator dashboard for monitoring the multi-agent
  pipeline, testing Vector RAG & tool calling, and reviewing live comments.
- `extension`: YouTube Live assistant extension with chip strip and private
  moderator/AI comment threads.

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