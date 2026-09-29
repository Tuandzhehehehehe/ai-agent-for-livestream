# AI Integration Contract (Proposal)

This document proposes an in-process Python boundary for Member 2 to review.
It is not an existing backend API and does not authorize changes to another
member's implementation. Field names and status values require agreement
before integration.

## Customer Q&A

The following in-process Python interface is implemented by the AI package for
Member 2 to review. It is not an HTTP contract. The main backend fields and
transport remain owned by Member 2 and must not be changed without agreement.

```python
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer

request = CustomerQuestionRequest(
    session_id="synthetic-session-1",
    comment_id="comment-42",
    text="What is the shipping policy?",
)
draft = draft_customer_answer(request)
```

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

The functions in this example are Member 2 integration responsibilities, not
functions supplied by this package. A review result may have no sources;
preserve any returned references and its `reason`.

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

## Ownership and transport

The AI package exposes Python functions and typed values, not HTTP routes,
database models, or frontend-specific structures. Member 2 owns transport,
authentication, persistence, approval workflows, and the final integration
contract. No raw API response format is fixed until both members agree.