"""Deterministic aggregation of repeated questions and operator-review issues."""

import re
from collections import defaultdict
from collections.abc import Iterable

from ai.customer_qa.schemas import Intent
from ai.operations.schemas import (
    CommentClassification,
    FrequentQuestion,
    OperationsSourceReference,
    RecurringIssue,
    SimulatedComment,
    SimulatedEvent,
)

QUESTION_INTENTS = frozenset(
    {
        "product_information",
        "price_question",
        "stock_question",
        "shipping_policy",
        "return_policy",
        "promotion_question",
    }
)
ISSUE_INTENTS = frozenset(
    {
        "prompt_injection",
        "refund_request",
        "price_change_request",
        "consequential_action",
        "complaint",
        "unknown",
    }
)
_ISSUE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("damaged_item", ("damaged", "broken", "defective")),
    ("wrong_item", ("wrong item", "incorrect item")),
    ("late_delivery", ("late", "delayed", "not arrived")),
)
_RECURRING_EVENT_TYPES = frozenset(
    {
        "out_of_stock",
        "low_inventory",
        "stream_interruption",
        "checkout_error",
        "payment_failure",
    }
)


def aggregate_frequent_questions(
    comments: Iterable[SimulatedComment],
    classifications: Iterable[CommentClassification],
    *,
    minimum_frequency: int = 2,
) -> tuple[FrequentQuestion, ...]:
    """Group normalized question text and include only groups meeting threshold.

    Repeated whitespace, capitalization, and punctuation are normalized.
    Separate comment IDs remain separate observations.
    """
    if isinstance(minimum_frequency, bool) or not isinstance(minimum_frequency, int):
        raise TypeError("minimum_frequency must be an integer")
    if minimum_frequency < 1:
        raise ValueError("minimum_frequency must be at least 1")

    comment_by_id = {comment.comment_id: comment for comment in comments}
    classification_by_id = {
        item.comment_id: item for item in classifications
    }
    groups: dict[tuple[Intent, str], list[SimulatedComment]] = defaultdict(list)
    for comment_id, classification in classification_by_id.items():
        comment = comment_by_id.get(comment_id)
        if comment is None or classification.intent not in QUESTION_INTENTS:
            continue
        normalized = _normalize_question(comment.text)
        if normalized:
            groups[(classification.intent, normalized)].append(comment)

    questions = [
        FrequentQuestion(
            normalized_question=normalized,
            category=intent,
            count=len(group),
            comment_ids=tuple(comment.comment_id for comment in group),
            sources=tuple(_comment_source(comment) for comment in group),
        )
        for (intent, normalized), group in groups.items()
        if len(group) >= minimum_frequency
    ]
    questions.sort(
        key=lambda item: (-item.count, item.category, item.normalized_question)
    )
    return tuple(questions)


def aggregate_recurring_issues(
    comments: Iterable[SimulatedComment],
    classifications: Iterable[CommentClassification],
) -> tuple[RecurringIssue, ...]:
    """Group review-worthy comments by intent and simple complaint markers."""
    comment_by_id = {comment.comment_id: comment for comment in comments}
    classification_by_id = {
        item.comment_id: item for item in classifications
    }
    groups: dict[str, list[SimulatedComment]] = defaultdict(list)
    for comment_id, classification in classification_by_id.items():
        comment = comment_by_id.get(comment_id)
        if comment is None or classification.intent not in ISSUE_INTENTS:
            continue
        issue_type = _issue_type(comment.text, classification.intent)
        groups[issue_type].append(comment)

    issues = [
        RecurringIssue(
            issue_type=issue_type,
            source_type="comment",
            count=len(group),
            comment_ids=tuple(comment.comment_id for comment in group),
            sources=tuple(_comment_source(comment) for comment in group),
        )
        for issue_type, group in groups.items()
    ]
    issues.sort(key=lambda item: (-item.count, item.issue_type))
    return tuple(issues)


def aggregate_recurring_event_issues(
    events: Iterable[SimulatedEvent],
) -> tuple[RecurringIssue, ...]:
    """Group repeated allowlisted event types without interpreting descriptions."""
    groups: dict[str, list[SimulatedEvent]] = defaultdict(list)
    for event in events:
        normalized_type = event.event_type.casefold().replace("-", "_").replace(" ", "_")
        if normalized_type in _RECURRING_EVENT_TYPES:
            groups[normalized_type].append(event)

    issues = [
        RecurringIssue(
            issue_type=event_type,
            source_type="event",
            count=len(group),
            comment_ids=(),
            sources=tuple(_event_source(event) for event in group),
            event_ids=tuple(event.event_id for event in group),
        )
        for event_type, group in groups.items()
    ]
    issues.sort(key=lambda item: (-item.count, item.issue_type))
    return tuple(issues)


def _normalize_question(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _issue_type(text: str, intent: Intent) -> str:
    normalized = text.casefold()
    if intent == "complaint":
        for issue_type, markers in _ISSUE_MARKERS:
            if any(marker in normalized for marker in markers):
                return issue_type
    return intent


def _comment_source(comment: SimulatedComment) -> OperationsSourceReference:
    return OperationsSourceReference(
        source_type="comment",
        source_id=comment.comment_id,
        title="Simulated customer comment",
        excerpt=_bounded_excerpt(comment.text),
    )


def _event_source(event: SimulatedEvent) -> OperationsSourceReference:
    return OperationsSourceReference(
        source_type="event",
        source_id=event.event_id,
        title=event.event_type,
        excerpt=_bounded_excerpt(event.description),
    )


def _bounded_excerpt(text: str, limit: int = 512) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]} [excerpt truncated]"