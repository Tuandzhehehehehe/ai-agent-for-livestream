"""Evidence-backed deterministic summaries and draft recommendations."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TypeAlias

from ai.customer_qa.safety import detect_prompt_injection
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.data import load_synthetic_data
from ai.data.models import SyntheticDataset
from ai.operations.aggregation import (
    aggregate_frequent_questions,
    aggregate_recurring_event_issues,
    aggregate_recurring_issues,
)
from ai.operations.comment_classifier import classify_comment
from ai.operations.schemas import (
    CommentClassification,
    OperationsRecommendation,
    OperationsSourceReference,
    OperationsSummary,
    FrequentQuestion,
    RecurringIssue,
    SimulatedComment,
    SimulatedEvent,
)

CommentInput: TypeAlias = SimulatedComment | Mapping[str, object]
EventInput: TypeAlias = SimulatedEvent | Mapping[str, object]
_ACTION_EVENT_RECOMMENDATIONS = {
    "out_of_stock": "Operator should review the reported inventory alert.",
    "low_inventory": "Operator should review the reported inventory alert.",
    "stream_interruption": "Operator should review the reported stream interruption.",
    "checkout_error": "Operator should review the reported checkout error.",
    "payment_failure": "Operator should review the reported payment failure.",
}


class OperationsInputError(ValueError):
    """Raised for malformed simulated comments, events, or references."""


def summarize_session(
    comments: Iterable[CommentInput] | None = None,
    events: Iterable[EventInput] | None = None,
    *,
    dataset: SyntheticDataset | None = None,
    minimum_question_frequency: int = 2,
) -> OperationsSummary:
    """Analyze supplied records only and return a draft summary with citations.

    Repeated identical records with the same ID are ignored. Conflicting
    duplicate IDs and dangling event-to-comment references are rejected.
    ``None`` and empty iterables are treated as empty input.
    """
    parsed_comments, duplicate_comment_count = _parse_comments(comments)
    parsed_events, duplicate_event_count = _parse_events(events)
    comment_ids = {comment.comment_id for comment in parsed_comments}
    for event in parsed_events:
        dangling = set(event.related_comment_ids) - comment_ids
        if dangling:
            missing = ", ".join(sorted(dangling))
            raise OperationsInputError(
                f"event {event.event_id!r} references unknown comment IDs: {missing}"
            )

    records = dataset if dataset is not None else load_synthetic_data()
    product_names = tuple(product.name for product in records.products)
    classifications = tuple(
        classify_comment(comment, product_names=product_names)
        for comment in parsed_comments
    )
    frequent_questions = aggregate_frequent_questions(
        parsed_comments,
        classifications,
        minimum_frequency=minimum_question_frequency,
    )
    recurring_issues = aggregate_recurring_issues(parsed_comments, classifications)
    trusted_event_records = tuple(
        event
        for event in parsed_events
        if len(event.description) <= MAX_CUSTOMER_MESSAGE_LENGTH
        and not detect_prompt_injection(event.description)
    )
    recurring_issues += aggregate_recurring_event_issues(trusted_event_records)
    recurring_issues = tuple(
        sorted(
            recurring_issues,
            key=lambda item: (-item.count, item.source_type, item.issue_type),
        )
    )
    sources = tuple(_comment_source(comment) for comment in parsed_comments) + tuple(
        _event_source(event) for event in parsed_events
    )
    recommendations = _recommendations(
        parsed_events,
        recurring_issues,
    )
    overview = _build_overview(
        parsed_comments,
        parsed_events,
        classifications,
        frequent_questions,
        recurring_issues,
        duplicate_comment_count,
        duplicate_event_count,
    )
    return OperationsSummary(
        overview=overview,
        comment_count=len(parsed_comments),
        event_count=len(parsed_events),
        duplicate_comment_count=duplicate_comment_count,
        duplicate_event_count=duplicate_event_count,
        classifications=classifications,
        frequent_questions=frequent_questions,
        recurring_issues=recurring_issues,
        recommendations=recommendations,
        sources=sources,
    )


def _parse_comments(
    values: Iterable[CommentInput] | None,
) -> tuple[tuple[SimulatedComment, ...], int]:
    parsed: list[SimulatedComment] = []
    seen: dict[str, SimulatedComment] = {}
    duplicates = 0
    for index, value in enumerate(_iter_inputs(values, "comments")):
        if isinstance(value, SimulatedComment):
            comment = SimulatedComment(
                comment_id=(
                    value.comment_id.strip()
                    if isinstance(value.comment_id, str)
                    else value.comment_id
                ),
                text=value.text,
            )
        elif isinstance(value, Mapping):
            comment_id = value.get("comment_id")
            text = value.get("text")
            if not isinstance(comment_id, str) or not comment_id.strip():
                raise OperationsInputError(
                    f"comments[{index}].comment_id must be a non-empty string"
                )
            if not isinstance(text, str):
                raise OperationsInputError(f"comments[{index}].text must be a string")
            comment = SimulatedComment(comment_id.strip(), text)
        else:
            raise OperationsInputError(
                f"comments[{index}] must be SimulatedComment or a mapping"
            )
        _validate_comment(comment, index)
        previous = seen.get(comment.comment_id)
        if previous is not None:
            if previous != comment:
                raise OperationsInputError(
                    f"duplicate comment ID {comment.comment_id!r} has conflicting data"
                )
            duplicates += 1
            continue
        seen[comment.comment_id] = comment
        parsed.append(comment)
    return tuple(parsed), duplicates


def _parse_events(
    values: Iterable[EventInput] | None,
) -> tuple[tuple[SimulatedEvent, ...], int]:
    parsed: list[SimulatedEvent] = []
    seen: dict[str, SimulatedEvent] = {}
    duplicates = 0
    for index, value in enumerate(_iter_inputs(values, "events")):
        if isinstance(value, SimulatedEvent):
            event = SimulatedEvent(
                event_id=(
                    value.event_id.strip()
                    if isinstance(value.event_id, str)
                    else value.event_id
                ),
                event_type=(
                    value.event_type.strip()
                    if isinstance(value.event_type, str)
                    else value.event_type
                ),
                description=value.description,
                related_comment_ids=(
                    tuple(
                        comment_id.strip()
                        if isinstance(comment_id, str)
                        else comment_id
                        for comment_id in value.related_comment_ids
                    )
                    if isinstance(value.related_comment_ids, tuple)
                    else value.related_comment_ids
                ),
            )
        elif isinstance(value, Mapping):
            event_id = value.get("event_id")
            event_type = value.get("event_type")
            description = value.get("description")
            raw_comment_ids = value.get("related_comment_ids", ())
            if not isinstance(event_id, str) or not event_id.strip():
                raise OperationsInputError(
                    f"events[{index}].event_id must be a non-empty string"
                )
            if not isinstance(event_type, str) or not event_type.strip():
                raise OperationsInputError(
                    f"events[{index}].event_type must be a non-empty string"
                )
            if not isinstance(description, str):
                raise OperationsInputError(
                    f"events[{index}].description must be a string"
                )
            if not isinstance(raw_comment_ids, (tuple, list)):
                raise OperationsInputError(
                    f"events[{index}].related_comment_ids must be a list or tuple"
                )
            if any(
                not isinstance(comment_id, str) or not comment_id.strip()
                for comment_id in raw_comment_ids
            ):
                raise OperationsInputError(
                    f"events[{index}].related_comment_ids must contain non-empty strings"
                )
            if len(set(raw_comment_ids)) != len(raw_comment_ids):
                raise OperationsInputError(
                    f"events[{index}].related_comment_ids must not contain duplicates"
                )
            event = SimulatedEvent(
                event_id=event_id.strip(),
                event_type=event_type.strip(),
                description=description,
                related_comment_ids=tuple(
                    comment_id.strip() for comment_id in raw_comment_ids
                ),
            )
        else:
            raise OperationsInputError(
                f"events[{index}] must be SimulatedEvent or a mapping"
            )
        _validate_event(event, index)
        previous = seen.get(event.event_id)
        if previous is not None:
            if previous != event:
                raise OperationsInputError(
                    f"duplicate event ID {event.event_id!r} has conflicting data"
                )
            duplicates += 1
            continue
        seen[event.event_id] = event
        parsed.append(event)
    return tuple(parsed), duplicates


def _iter_inputs(values: Iterable[object] | None, name: str) -> Iterable[object]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes, Mapping)):
        raise OperationsInputError(f"{name} must be an iterable of records")
    try:
        return iter(values)
    except TypeError as error:
        raise OperationsInputError(f"{name} must be an iterable of records") from error


def _validate_comment(comment: SimulatedComment, index: int) -> None:
    if not isinstance(comment.comment_id, str) or not comment.comment_id.strip():
        raise OperationsInputError(
            f"comments[{index}].comment_id must be a non-empty string"
        )
    if not isinstance(comment.text, str):
        raise OperationsInputError(f"comments[{index}].text must be a string")


def _validate_event(event: SimulatedEvent, index: int) -> None:
    for field_name, value in (
        ("event_id", event.event_id),
        ("event_type", event.event_type),
    ):
        if not isinstance(value, str) or not value.strip():
            raise OperationsInputError(
                f"events[{index}].{field_name} must be a non-empty string"
            )
    if not isinstance(event.description, str):
        raise OperationsInputError(f"events[{index}].description must be a string")
    if not isinstance(event.related_comment_ids, tuple) or any(
        not isinstance(comment_id, str) or not comment_id.strip()
        for comment_id in event.related_comment_ids
    ):
        raise OperationsInputError(
            f"events[{index}].related_comment_ids must contain non-empty strings"
        )
    if len(set(event.related_comment_ids)) != len(event.related_comment_ids):
        raise OperationsInputError(
            f"events[{index}].related_comment_ids must not contain duplicates"
        )


def _recommendations(
    events: tuple[SimulatedEvent, ...],
    recurring_issues: tuple[RecurringIssue, ...],
) -> tuple[OperationsRecommendation, ...]:
    recommendations: list[OperationsRecommendation] = []

    for issue in recurring_issues:
        sources = issue.sources
        if issue.source_type == "event":
            event_guidance = _ACTION_EVENT_RECOMMENDATIONS[issue.issue_type]
            recommendations.append(
                OperationsRecommendation(
                    text=(
                        f"Draft recommendation: {event_guidance} "
                        f"This event type appears {issue.count} time(s)."
                    ),
                    reason=(
                        "This recommendation uses only the allowlisted simulated event "
                        "type and its count; no action has been performed."
                    ),
                    sources=sources,
                )
            )
            continue
        recommendations.append(
            OperationsRecommendation(
                text=(
                    f"Draft recommendation: ask an operator to review "
                    f"{issue.count} comment(s) grouped as {issue.issue_type}."
                ),
                reason=(
                    "The comment classification marked these records for human review; "
                    "no action has been performed."
                ),
                sources=sources,
            )
        )

    for event in events:
        if len(event.description) > MAX_CUSTOMER_MESSAGE_LENGTH:
            recommendations.append(
                OperationsRecommendation(
                    text="Draft recommendation: ask an operator to inspect this overlong event description.",
                    reason=(
                        f"The supplied description exceeds the "
                        f"{MAX_CUSTOMER_MESSAGE_LENGTH}-character safety limit; "
                        "no action has been performed."
                    ),
                    sources=(_event_source(event),),
                )
            )
            continue
        injection = detect_prompt_injection(event.description)
        if injection:
            recommendations.append(
                OperationsRecommendation(
                    text="Draft recommendation: ask an operator to inspect this untrusted event description.",
                    reason="Instruction-like text in an event is untrusted input and was not followed.",
                    sources=(_event_source(event),),
                )
            )
            continue

    return tuple(recommendations)


def _build_overview(
    comments: tuple[SimulatedComment, ...],
    events: tuple[SimulatedEvent, ...],
    classifications: tuple[CommentClassification, ...],
    frequent_questions: tuple[FrequentQuestion, ...],
    recurring_issues: tuple[RecurringIssue, ...],
    duplicate_comment_count: int,
    duplicate_event_count: int,
) -> str:
    review_count = sum(item.needs_human_review for item in classifications)
    categories = Counter(item.intent for item in classifications)
    category_text = ", ".join(
        f"{category}: {count}" for category, count in sorted(categories.items())
    ) or "none"
    frequent_text = ", ".join(
        f"{item.normalized_question} ({item.count})" for item in frequent_questions
    ) or "none reached the frequency threshold"
    issue_text = ", ".join(
        f"{item.issue_type}: {item.count}" for item in recurring_issues
    ) or "none"
    event_categories = Counter(
        _safe_event_category(event.event_type) for event in events
    )
    event_text = ", ".join(
        f"{category}: {count}" for category, count in sorted(event_categories.items())
    ) or "none"
    return (
        f"Draft session summary: {len(comments)} unique comment(s), "
        f"{len(events)} unique event(s), {review_count} comment(s) flagged for review; "
        f"ignored {duplicate_comment_count} duplicate comment and "
        f"{duplicate_event_count} duplicate event record(s). "
        f"Classifications: {category_text}. Frequent questions: {frequent_text}. "
        f"Issue groups: {issue_text}. Supplied event categories: {event_text}."
    )


def _safe_event_category(event_type: str) -> str:
    normalized = event_type.casefold().replace("-", "_").replace(" ", "_")
    return normalized if normalized in _ACTION_EVENT_RECOMMENDATIONS else "other/unclassified"


def _comment_source(comment: SimulatedComment) -> OperationsSourceReference:
    return OperationsSourceReference(
        source_type="comment",
        source_id=comment.comment_id,
        title="Simulated customer comment",
        excerpt=_safe_excerpt(comment.text),
    )


def _event_source(event: SimulatedEvent) -> OperationsSourceReference:
    return OperationsSourceReference(
        source_type="event",
        source_id=event.event_id,
        title=event.event_type,
        excerpt=_safe_excerpt(event.description),
    )


def _safe_excerpt(text: str, limit: int = 512) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]} [excerpt truncated]"