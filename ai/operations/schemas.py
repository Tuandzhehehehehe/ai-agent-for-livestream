"""Typed input and output records for operations analysis."""

from dataclasses import dataclass
from typing import Literal

from ai.customer_qa.schemas import Intent

OperationsSourceType = Literal["comment", "event"]
IssueSourceType = Literal["comment", "event"]


@dataclass(frozen=True, slots=True)
class SimulatedComment:
    comment_id: str
    text: str


@dataclass(frozen=True, slots=True)
class SimulatedEvent:
    event_id: str
    event_type: str
    description: str
    related_comment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationsSourceReference:
    source_type: OperationsSourceType
    source_id: str
    title: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class CommentClassification:
    comment_id: str
    intent: Intent
    needs_human_review: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FrequentQuestion:
    normalized_question: str
    category: Intent
    count: int
    comment_ids: tuple[str, ...]
    sources: tuple[OperationsSourceReference, ...]


@dataclass(frozen=True, slots=True)
class RecurringIssue:
    issue_type: str
    source_type: IssueSourceType
    count: int
    comment_ids: tuple[str, ...]
    sources: tuple[OperationsSourceReference, ...]
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationsRecommendation:
    text: str
    reason: str
    sources: tuple[OperationsSourceReference, ...]
    is_draft: bool = True


@dataclass(frozen=True, slots=True)
class OperationsSummary:
    overview: str
    comment_count: int
    event_count: int
    duplicate_comment_count: int
    duplicate_event_count: int
    classifications: tuple[CommentClassification, ...]
    frequent_questions: tuple[FrequentQuestion, ...]
    recurring_issues: tuple[RecurringIssue, ...]
    recommendations: tuple[OperationsRecommendation, ...]
    sources: tuple[OperationsSourceReference, ...]