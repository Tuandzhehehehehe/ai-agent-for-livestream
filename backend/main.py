"""Local API for receiving and classifying YouTube Live comments."""

from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import time
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import AnswerStatus, Intent, SourceType
from ai.operations import SimulatedComment, classify_comment
from backend.chat_safety import (
    ContentModerator,
    ViewerRateLimiter,
    configured_rate_limiter,
)
from backend.database import *
from backend.local_model import LocalIntentModel, MODEL_NAME, get_local_intent_model
from backend.ollama_provider import get_ollama_provider

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
MAX_STORED_COMMENTS = 1000
MAX_COMMENT_LENGTH = 16_384


class LiveCommentPayload(BaseModel):
    """Comment payload posted by the browser extension."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    comment_id: str = Field(alias="id", min_length=1, max_length=256)
    sender: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    author_type: str = Field(alias="authorType", min_length=1, max_length=64)
    timestamp: int = Field(gt=0)


class ThreadMessage(BaseModel):
    """Message in a comment's private thread."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    sender: str = Field(default="Moderator", min_length=1, max_length=256)
    author_role: Literal["ai", "moderator"] = Field(
        default="moderator", alias="authorRole"
    )
    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000), gt=0)


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
    thread_messages: list[ThreadMessage] = Field(default_factory=list)


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


def _to_safe_id(raw_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._@-]+", "_", raw_id.strip()).strip("_")[:128] or "id"


def _process_comment(
    payload: LiveCommentPayload,
    *,
    session_id: str,
    intent_model: LocalIntentModel,
    moderator: ContentModerator,
) -> ClassifiedLiveComment:
    safe_comment_id = _to_safe_id(payload.comment_id)
    author_type = payload.author_type.casefold()
    moderation = moderator.inspect(payload.text)
    if not moderation.allowed:
        return ClassifiedLiveComment(
            comment_id=safe_comment_id,
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

    classification = classify_comment(
        SimulatedComment(comment_id=safe_comment_id, text=payload.text)
    )
    intent_source: Literal["rules", "local_model"] = "rules"
    intent_confidence = None
    override_intent: Intent | None = None
    if (
        classification.intent == "unknown"
        and len(payload.text) <= MAX_CUSTOMER_MESSAGE_LENGTH
    ):
        prediction = intent_model.predict(payload.text)
        intent_confidence = prediction.confidence
        if prediction.intent != "unknown":
            intent_source = "local_model"
            override_intent = prediction.intent

    draft = draft_customer_answer(
        CustomerQuestionRequest(
            session_id=session_id,
            comment_id=safe_comment_id,
            text=payload.text,
        ),
        intent_override=override_intent,
    )

    answer_draft = None
    if author_type in {"viewer", "member"} and (
        draft.status == "answered"
        or draft.sources
        or "?" in payload.text
        or "？" in payload.text
    ):
        answer_draft = AnswerDraftResponse(
            answer=draft.answer,
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

    return ClassifiedLiveComment(
        comment_id=safe_comment_id,
        sender=payload.sender,
        text=payload.text,
        author_type=payload.author_type,
        timestamp=payload.timestamp,
        intent=draft.intent,
        intent_source=intent_source,
        intent_confidence=intent_confidence,
        needs_human_review=draft.needs_human_review,
        reason=draft.reason,
        moderation_status="allowed",
        answer_draft=answer_draft,
    )


def create_app(
    *,
    local_model: LocalIntentModel | None = None,
    rate_limiter: ViewerRateLimiter | None = None,
    content_moderator: ContentModerator | None = None,
) -> FastAPI:
    """Build an app wired to MongoDB/Redis with an in-memory fallback buffer."""
    recent_comments: deque[ClassifiedLiveComment] = deque(maxlen=MAX_STORED_COMMENTS)
    threads_by_comment: dict[str, list[ThreadMessage]] = {}
    intent_model = local_model or get_local_intent_model()
    viewer_rate_limiter = rate_limiter or configured_rate_limiter()
    moderator = content_moderator or ContentModerator.from_environment()

    store_id = _to_safe_id(os.getenv("LIVESTREAM_STORE_ID", "default-store"))
    session_id = _to_safe_id(os.getenv("LIVESTREAM_SESSION_ID", "youtube-live"))
    tenant_ctx = TenantContext.system_context(store_id)
    db_settings = DatabaseSettings.from_env()
    mongo_manager = MongoDatabaseManager(db_settings.mongo)
    redis_manager = RedisManager(db_settings.redis)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = None
        try:
            async with database_lifespan(mongo_manager, redis_manager):
                facade = StoreDatabaseFacade(mongo_manager, redis_manager)
                if await facade.sessions.get_by_id(tenant_ctx, session_id) is None:
                    await facade.sessions.create_session(
                        tenant_ctx,
                        LivestreamSessionDocument(
                            store_id=store_id,
                            session_id=session_id,
                            title=session_id,
                            platform="youtube",
                            status="live",
                        ),
                    )
                app.state.db = facade
                yield
        except DatabaseError as exc:
            logger.warning("Database unavailable, using in-memory mode: %s", exc)
            yield

    app = FastAPI(title="Livestream AI Backend", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str | int | bool | None]:
        ollama = get_ollama_provider()
        mongo_ping = await mongo_manager.ping()
        redis_ping = await redis_manager.ping()
        return {
            "status": "ok",
            "ai_status": "ready",
            "ai_model": MODEL_NAME,
            "training_examples": intent_model.training_example_count,
            "ollama_enabled": ollama.enabled,
            "ollama_configured": ollama.is_configured,
            "ollama_model": ollama.model or None,
            "mongo_status": mongo_ping.get("status"),
            "redis_status": redis_ping.get("status"),
        }

    @app.post("/api/plugin/comments", response_model=ClassifiedLiveComment)
    async def receive_comment(payload: LiveCommentPayload) -> ClassifiedLiveComment:
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

        result = _process_comment(
            payload,
            session_id=session_id,
            intent_model=intent_model,
            moderator=moderator,
        )
        result.thread_messages = threads_by_comment.setdefault(result.comment_id, [])
        recent_comments.append(result)

        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                await facade.record_comment_with_operational_sync(
                    tenant_ctx,
                    LivestreamCommentDocument(
                        store_id=store_id,
                        session_id=session_id,
                        comment_id=result.comment_id,
                        platform="youtube",
                        user_handle=result.sender[:256],
                        message=result.text[:1000],
                        timestamp=datetime.fromtimestamp(
                            result.timestamp / 1000, tz=timezone.utc
                        ),
                        intent=result.intent,
                        intent_confidence=result.intent_confidence,
                        safety_blocked=result.moderation_status == "blocked",
                        safety_reason=result.moderation_reason,
                    ),
                )
                if result.answer_draft and result.answer_draft.answer:
                    await facade.answer_drafts.save_draft(
                        tenant_ctx,
                        AnswerDraftDocument(
                            store_id=store_id,
                            session_id=session_id,
                            draft_id=_to_safe_id(f"draft-{result.comment_id}"),
                            comment_id=result.comment_id,
                            suggested_reply=result.answer_draft.answer[:10000],
                            confidence=1.0 if result.answer_draft.status == "answered" else 0.0,
                            grounding_passed=not result.answer_draft.needs_human_review,
                            fallback_used=result.answer_draft.status != "answered",
                            sources=[
                                EvidenceSourceSnapshot(
                                    source_type=s.source_type,
                                    source_id=_to_safe_id(s.source_id),
                                    title=s.title[:256],
                                    snippet=s.excerpt[:1000],
                                )
                                for s in result.answer_draft.sources
                            ],
                        ),
                    )
            except DatabaseError as exc:
                logger.warning("Failed to persist comment %s: %s", result.comment_id, exc)

        return result

    @app.get("/api/plugin/comments", response_model=list[ClassifiedLiveComment])
    async def list_comments(
        limit: int = Query(default=100, ge=1, le=MAX_STORED_COMMENTS),
    ) -> list[ClassifiedLiveComment]:
        return list(reversed(recent_comments))[:limit]

    @app.post(
        "/api/plugin/comments/{comment_id:path}/thread",
        response_model=list[ThreadMessage],
    )
    async def post_comment_thread_message(
        comment_id: str,
        payload: ThreadMessage,
    ) -> list[ThreadMessage]:
        safe_comment_id = _to_safe_id(comment_id)
        thread = threads_by_comment.setdefault(safe_comment_id, [])
        thread.append(payload)

        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                draft = await facade.answer_drafts.get_latest_for_comment(
                    tenant_ctx, safe_comment_id
                )
                if draft is not None:
                    await facade.answer_drafts.record_seller_action(
                        tenant_ctx,
                        draft.draft_id,
                        action="approved" if payload.author_role == "ai" else "edited",
                        final_reply_text=payload.text[:5000],
                    )
            except DatabaseError as exc:
                logger.warning("Failed to persist thread reply for %s: %s", safe_comment_id, exc)

        return thread

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