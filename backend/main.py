"""Local API for receiving and classifying YouTube Live comments."""

from collections import Counter, deque
import logging
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import AnswerStatus, Intent, SourceType
from ai.operations import CommentClassification, SimulatedComment, classify_comment
from backend.chat_safety import (
    ContentModerator,
    ViewerRateLimiter,
    configured_rate_limiter,
)
from backend.local_model import LocalIntentModel, get_local_intent_model

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
MAX_STORED_COMMENTS = 1000
MAX_COMMENT_LENGTH = 16_384
ANSWERABLE_INTENTS = frozenset(
    {
        "shipping_policy",
        "return_policy",
        "promotion_question",
        "stock_question",
        "price_question",
        "product_information",
    }
)
LOCAL_MODEL_REVIEW_REASONS = {
    "prompt_injection": "The local model detected a possible prompt injection.",
    "refund_request": "Refund requests require human handling.",
    "price_change_request": "Price changes require human handling.",
    "consequential_action": "This action requires human handling.",
    "complaint": "A customer complaint may require operator intervention.",
    "spam_or_irrelevant": "This comment may be unrelated or spam.",
}


class LiveCommentPayload(BaseModel):
    """Comment payload posted by the browser extension."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    comment_id: str = Field(alias="id", min_length=1, max_length=256)
    sender: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    author_type: str = Field(alias="authorType", min_length=1, max_length=64)
    timestamp: int = Field(gt=0)


class ClassifiedLiveComment(BaseModel):
    """Accepted comment with its deterministic AI classification."""

    comment_id: str
    sender: str
    text: str
    author_type: str
    timestamp: int
    intent: Intent
    intent_source: Literal["rules", "local_model"] = "rules"
    intent_confidence: float | None = None
    needs_human_review: bool
    reason: str | None = None
    moderation_status: Literal["allowed", "blocked"] = "allowed"
    moderation_reason: str | None = None
    answer_draft: "AnswerDraftResponse | None" = None


class AnswerSourceResponse(BaseModel):
    source_type: SourceType
    source_id: str
    title: str
    excerpt: str


class AnswerDraftResponse(BaseModel):
    answer: str
    intent: Intent
    sources: list[AnswerSourceResponse]
    status: AnswerStatus
    needs_human_review: bool
    reason: str | None
    is_draft: Literal[True]


def create_app(
    *,
    local_model: LocalIntentModel | None = None,
    rate_limiter: ViewerRateLimiter | None = None,
    content_moderator: ContentModerator | None = None,
) -> FastAPI:
    """Build an app with a bounded in-memory buffer for recent comments."""
    recent_comments: deque[ClassifiedLiveComment] = deque(maxlen=MAX_STORED_COMMENTS)
    intent_model = local_model or get_local_intent_model()
    viewer_rate_limiter = rate_limiter or configured_rate_limiter()
    moderator = content_moderator or ContentModerator.from_environment()
    app = FastAPI(title="Livestream AI Backend", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/plugin/comments", response_model=ClassifiedLiveComment)
    def receive_comment(payload: LiveCommentPayload) -> ClassifiedLiveComment:
        author_type = payload.author_type.casefold()
        if author_type in {"viewer", "member"}:
            retry_after = viewer_rate_limiter.consume(payload.sender)
            if retry_after is not None:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "viewer_rate_limited",
                        "message": "Too many comments from this viewer. Try again later.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        moderation = moderator.inspect(payload.text)
        if not moderation.allowed:
            result = ClassifiedLiveComment(
                comment_id=payload.comment_id,
                sender=payload.sender,
                text=payload.text,
                author_type=payload.author_type,
                timestamp=payload.timestamp,
                intent="spam_or_irrelevant",
                needs_human_review=True,
                reason="Comment blocked by chat moderation.",
                moderation_status="blocked",
                moderation_reason=moderation.reason,
            )
            recent_comments.append(result)
            return result

        classification = classify_comment(
            SimulatedComment(comment_id=payload.comment_id, text=payload.text)
        )
        intent_source: Literal["rules", "local_model"] = "rules"
        intent_confidence = None
        if (
            classification.intent == "unknown"
            and len(payload.text) <= MAX_CUSTOMER_MESSAGE_LENGTH
        ):
            prediction = intent_model.predict(payload.text)
            intent_confidence = prediction.confidence
            if prediction.intent != "unknown":
                intent_source = "local_model"
                review_reason = LOCAL_MODEL_REVIEW_REASONS.get(prediction.intent)
                classification = CommentClassification(
                    comment_id=payload.comment_id,
                    intent=prediction.intent,
                    needs_human_review=review_reason is not None,
                    reason=review_reason,
                )
        answer_draft = None
        if (
            author_type in {"viewer", "member"}
            and (
                classification.intent in ANSWERABLE_INTENTS
                or "?" in payload.text
                or "？" in payload.text
            )
        ):
            draft = draft_customer_answer(
                CustomerQuestionRequest(
                    session_id="youtube-live",
                    comment_id=payload.comment_id,
                    text=payload.text,
                ),
                intent_override=(
                    classification.intent if intent_source == "local_model" else None
                ),
            )
            answer = draft.answer
            answer_draft = AnswerDraftResponse(
                answer=answer,
                intent=draft.intent,
                sources=[
                    AnswerSourceResponse(
                        source_type=source.source_type,
                        source_id=source.source_id,
                        title=source.title,
                        excerpt=source.excerpt,
                    )
                    for source in draft.sources
                ],
                status=draft.status,
                needs_human_review=draft.needs_human_review,
                reason=draft.reason,
                is_draft=draft.is_draft,
            )
        result = ClassifiedLiveComment(
            comment_id=payload.comment_id,
            sender=payload.sender,
            text=payload.text,
            author_type=payload.author_type,
            timestamp=payload.timestamp,
            intent=classification.intent,
            intent_source=intent_source,
            intent_confidence=intent_confidence,
            needs_human_review=classification.needs_human_review,
            reason=classification.reason,
            answer_draft=answer_draft,
        )
        recent_comments.append(result)
        return result

    @app.get("/api/plugin/comments", response_model=list[ClassifiedLiveComment])
    def list_comments(
        limit: int = Query(default=100, ge=1, le=MAX_STORED_COMMENTS),
    ) -> list[ClassifiedLiveComment]:
        return list(reversed(recent_comments))[:limit]

    @app.get("/api/plugin/summary")
    def summarize_comments() -> dict[str, object]:
        intent_counts = Counter(comment.intent for comment in recent_comments)
        return {
            "comment_count": len(recent_comments),
            "moderated_comment_count": sum(
                comment.moderation_status == "blocked" for comment in recent_comments
            ),
            "needs_human_review_count": sum(
                comment.needs_human_review for comment in recent_comments
            ),
            "intent_counts": dict(sorted(intent_counts.items())),
        }

    return app


app = create_app()