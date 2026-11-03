# AI Integration Contract

This document describes the implemented in-process Python API and local HTTP
backend. Answers are drafts grounded in synthetic catalog/FAQ/policy records;
the backend does not call an external LLM or post replies to YouTube.

## Customer Q&A

The in-process customer Q&A interface is implemented by the AI package:

```python
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer

request = CustomerQuestionRequest(
    session_id="synthetic-session-1",
    comment_id="comment-42",
    text="What is the shipping policy?",
)
draft = draft_customer_answer(request)
```

`draft_customer_answer` also accepts an optional `intent_override`; only the
backend's local classifier should supply it. Never accept an intent override
directly from an untrusted HTTP request.

The returned frozen `CustomerAnswerDraft` contains `answer`, `intent`,
`sources`, `status`, `needs_human_review`, `reason`, and `is_draft`. `status`
is `answered` or `needs_review`. Each source contains `source_type`,
`source_id`, `title`, and `excerpt`. The intent baseline includes product,
price, stock, shipping, return, promotion, complaint, refund, price-change,
spam/irrelevant, unknown, and prompt-injection categories.

Order/payment requests are also classified as `consequential_action`. Messages
over 4096 characters are rejected into `needs_review`; callers should not
truncate customer text and resubmit it as if unchanged.

Only an `answered` result with at least one valid source reference is suitable
for a possible customer response. `needs_review` must be routed to a human; do
not auto-send it or perform a related action. Missing or conflicting evidence,
unsupported specific promotions, prompt-injection attempts, and consequential
requests are routed to `needs_review`. Answers are deterministic drafts based
only on synthetic records; the package never posts a comment, confirms an
order, changes a price, or performs a refund. Customer text is untrusted. The
injection checks are a baseline, not a complete security guarantee.

The integration layer should pass a `CustomerQuestionRequest` instance and
handle both statuses explicitly:

```python
draft = draft_customer_answer(request)
if draft.status == "needs_review" or draft.needs_human_review:
    route_to_operator(draft, source_ids=[source.source_id for source in draft.sources])
else:
    verify_sources_and_present_draft(draft.answer, draft.sources)
```

A review result may have no sources; preserve any returned references and its
`reason`.

## Backend HTTP API

The FastAPI app runs locally with `py -m uvicorn backend.main:app --host
127.0.0.1 --port 8000`. The extension sends this payload to
`POST /api/plugin/comments`:

```json
{
    "id": "comment-42",
    "sender": "@viewer",
    "text": "What is the shipping policy?",
    "authorType": "viewer",
    "timestamp": 1791568800000
}
```

The response includes rule/local-model intent source, moderation status, and an
optional answer draft with evidence sources. `GET /api/plugin/comments` returns
recent in-memory comments; `GET /api/plugin/summary` returns counts; `GET
/health` is the health check. The backend has no authentication or persistence
and should stay bound to loopback. Rate-limited requests receive HTTP 429.

## Operations assistance

Implemented offline Python entry point:

```python
from ai.operations import SimulatedComment, SimulatedEvent, summarize_session

summary = summarize_session(
    comments=(
        SimulatedComment("comment-1", "What is the shipping policy?"),
        SimulatedComment("comment-2", "What is the shipping policy?"),
    ),
    events=(
        SimulatedEvent("event-1", "stream_interruption", "Synthetic pause event"),
    ),
)
```

The returned `OperationsSummary` contains the overview, counts, classifications,
frequent questions, recurring issues, recommendations, and session source
references. Identical records sharing an ID are counted once; conflicting
duplicate IDs or dangling related-comment IDs raise `OperationsInputError`.
Mapping inputs may use `{"comment_id": str, "text": str}` for comments and
`{"event_id": str, "event_type": str, "description": str,
"related_comment_ids": list[str]}` for events; the related ID field is
optional. Empty collections are valid. Malformed records fail with
`OperationsInputError` rather than being silently skipped. Source excerpts are
limited to 512 characters and marked when truncated.
FAQ aggregation normalizes case/punctuation and defaults to a minimum frequency
of two distinct comment IDs. Every recommendation is marked as a draft and
includes the relevant comment/event references. Unknown event types do not
produce recommendations; repeated types from a small explicit event allowlist
are grouped into one cited review recommendation. No recommendation executes
an action.

`OperationsInputError` indicates malformed records, conflicting duplicate IDs,
duplicate related-comment references, or dangling event references. The caller
should surface the input issue for correction rather than silently dropping
records. Empty collections are valid. No approval decision or execution is
provided by `ai.operations`.

## Evaluation

The separate synthetic cases live in `ai/evaluation/scenarios.json`. Run the
reproducible evaluator and regenerate both reports with:

```powershell
py -m ai.evaluation.runner
```

The JSON report is written to `reports/evaluation_report.json`; the human-
readable summary is written to `docs/EVALUATION_REPORT.md`. Metrics include
their actual denominators and have no target threshold unless the project team
agrees one. Results are fixture checks only, not production accuracy claims.

## Runtime boundaries

The `ai` package exposes Python functions and typed values; `backend.main`
provides the HTTP routes. Storage is process-memory only. Authentication,
durable persistence, reviewer UI, and any approved reply workflow are not
implemented.