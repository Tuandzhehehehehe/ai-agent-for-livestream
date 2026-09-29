"""Typed request, classification, evidence, and draft response models."""

from dataclasses import dataclass
from typing import Literal

Intent = Literal[
    "prompt_injection",
    "refund_request",
    "price_change_request",
    "consequential_action",
    "complaint",
    "spam_or_irrelevant",
    "promotion_question",
    "shipping_policy",
    "return_policy",
    "stock_question",
    "price_question",
    "product_information",
    "unknown",
]
SourceType = Literal["product", "faq", "policy"]
AnswerStatus = Literal["answered", "needs_review"]


@dataclass(frozen=True, slots=True)
class CustomerQuestionRequest:
    session_id: str
    text: str
    comment_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntentClassification:
    intent: Intent
    matched_intents: tuple[Intent, ...]
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_type: SourceType
    source_id: str
    title: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class CustomerAnswerDraft:
    answer: str
    intent: Intent
    sources: tuple[SourceReference, ...]
    status: AnswerStatus
    needs_human_review: bool
    reason: str | None = None
    is_draft: bool = True