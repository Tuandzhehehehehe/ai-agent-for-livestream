"""Deterministic operations analysis for supplied simulated session records."""

from ai.operations.aggregation import (
    aggregate_frequent_questions,
    aggregate_recurring_event_issues,
    aggregate_recurring_issues,
)
from ai.operations.comment_classifier import classify_comment
from ai.operations.schemas import (
    CommentClassification,
    FrequentQuestion,
    OperationsRecommendation,
    OperationsSourceReference,
    OperationsSummary,
    RecurringIssue,
    SimulatedComment,
    SimulatedEvent,
)
from ai.operations.session_summarizer import (
    OperationsInputError,
    summarize_session,
)

__all__ = [
    "CommentClassification",
    "FrequentQuestion",
    "OperationsInputError",
    "OperationsRecommendation",
    "OperationsSourceReference",
    "OperationsSummary",
    "RecurringIssue",
    "SimulatedComment",
    "SimulatedEvent",
    "aggregate_frequent_questions",
    "aggregate_recurring_event_issues",
    "aggregate_recurring_issues",
    "classify_comment",
    "summarize_session",
]