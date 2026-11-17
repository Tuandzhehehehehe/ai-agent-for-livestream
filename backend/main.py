"""Local API for receiving and classifying YouTube Live comments."""

from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.agent_tools import execute_agent_tool, get_tool_definitions
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import AnswerStatus, Intent, SourceType
from ai.data import load_synthetic_data
from ai.multi_agent import MultiAgentOrchestrator, compute_lead_score
from ai.operations import CommentClassification, SimulatedComment, classify_comment
from ai.vector_rag import get_vector_rag_store
from backend.chat_safety import (
    ContentModerator,
    ViewerRateLimiter,
    configured_rate_limiter,
)
from backend.database import *
from backend.database.seed import load_demo_orders, seed_demo_store_data
from backend.local_model import LocalIntentModel, MODEL_NAME, get_local_intent_model
from backend.ollama_provider import get_ollama_provider

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
    lead_score: int = 0
    lead_label: Literal["hot", "warm", "cold"] = "cold"
    needs_human_review: bool
    reason: str | None = None
    moderation_status: Literal["allowed", "blocked"] = "allowed"
    moderation_reason: str | None = None
    answer_draft: "AnswerDraftResponse | None" = None
    thread_messages: list[ThreadMessage] = Field(default_factory=list)


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    sender: str = Field(default="Viewer", min_length=1, max_length=256)
    author_type: str = Field(default="viewer", min_length=1, max_length=64)
    session_id: str | None = None


class VectorRAGSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    source_type: SourceType | None = None


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
    cleaned = re.sub(r"[^A-Za-z0-9._@-]+", "_", raw_id.strip()).strip("_")
    if not cleaned:
        return "id"
    if len(cleaned) <= 128:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned[:115]}_{digest}"


def _safe_timestamp_to_datetime(timestamp_ms: int) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return utc_now()


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
                comment_id=safe_comment_id,
                intent=prediction.intent,
                needs_human_review=review_reason is not None,
                reason=review_reason,
            )

    answer_draft = None
    final_intent = classification.intent
    needs_human_review = classification.needs_human_review
    reason = classification.reason

    if author_type in {"viewer", "member"} and (
        classification.intent in ANSWERABLE_INTENTS
        or "?" in payload.text
        or "？" in payload.text
    ):
        draft = draft_customer_answer(
            CustomerQuestionRequest(
                session_id=session_id,
                comment_id=safe_comment_id,
                text=payload.text,
            ),
            intent_override=(
                classification.intent if intent_source == "local_model" else None
            ),
        )
        if final_intent == "unknown" and draft.intent != "unknown":
            final_intent = draft.intent
        if final_intent in ANSWERABLE_INTENTS:
            needs_human_review = draft.needs_human_review
            reason = draft.reason

        if (
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

    lead_score, lead_label = compute_lead_score(payload.text, final_intent)

    return ClassifiedLiveComment(
        comment_id=safe_comment_id,
        sender=payload.sender,
        text=payload.text,
        author_type=payload.author_type,
        timestamp=payload.timestamp,
        intent=final_intent,
        intent_source=intent_source,
        intent_confidence=intent_confidence,
        lead_score=lead_score,
        lead_label=lead_label,
        needs_human_review=needs_human_review,
        reason=reason,
        moderation_status="allowed",
        answer_draft=answer_draft,
    )


def create_app(
    *,
    local_model: LocalIntentModel | None = None,
    rate_limiter: ViewerRateLimiter | None = None,
    content_moderator: ContentModerator | None = None,
    db_facade: StoreDatabaseFacade | None = None,
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

    def _upsert_recent_comment(item: ClassifiedLiveComment) -> None:
        for idx, existing in enumerate(recent_comments):
            if existing.comment_id == item.comment_id:
                recent_comments[idx] = item
                return
        if len(recent_comments) == MAX_STORED_COMMENTS:
            evicted = recent_comments[0]
            if evicted.comment_id != item.comment_id:
                threads_by_comment.pop(evicted.comment_id, None)
        recent_comments.append(item)

    async def _load_comments_from_repository(
        facade: StoreDatabaseFacade,
        *,
        limit: int = MAX_STORED_COMMENTS,
    ) -> list[ClassifiedLiveComment]:
        db_comments = await facade.comments.list_recent_for_session(
            tenant_ctx, session_id, limit=limit
        )
        db_drafts = await facade.answer_drafts.list_for_session(
            tenant_ctx, session_id, limit=min(limit * 2, MAX_STORED_COMMENTS * 2)
        )
        draft_by_comment: dict[str, AnswerDraftDocument] = {}
        for draft_doc in db_drafts:
            draft_by_comment.setdefault(draft_doc.comment_id, draft_doc)

        memory_by_id = {c.comment_id: c for c in recent_comments}
        items: list[ClassifiedLiveComment] = []

        for doc in db_comments:
            mem_item = memory_by_id.get(doc.comment_id)
            draft_doc = draft_by_comment.get(doc.comment_id)
            intent_val: Intent = (
                doc.intent  # type: ignore[assignment]
                if doc.intent in LOCAL_MODEL_REVIEW_REASONS
                or doc.intent in ANSWERABLE_INTENTS
                or doc.intent == "unknown"
                else (mem_item.intent if mem_item else "unknown")
            )
            answer_draft: AnswerDraftResponse | None = None
            if draft_doc is not None:
                draft_needs_review = not draft_doc.grounding_passed or draft_doc.fallback_used
                draft_reason = (
                    mem_item.answer_draft.reason
                    if mem_item and mem_item.answer_draft
                    else (
                        "Draft requires operator verification before sending."
                        if draft_needs_review
                        else None
                    )
                )
                answer_draft = AnswerDraftResponse(
                    answer=draft_doc.suggested_reply,
                    intent=intent_val,
                    sources=[
                        AnswerSourceResponse(
                            source_type=(
                                src.source_type  # type: ignore[arg-type]
                                if src.source_type in {"product", "faq", "policy"}
                                else "product"
                            ),
                            source_id=src.source_id,
                            title=src.title,
                            excerpt=src.snippet,
                        )
                        for src in draft_doc.sources
                    ],
                    status="needs_review" if draft_needs_review else "answered",
                    needs_human_review=draft_needs_review,
                    reason=draft_reason,
                    is_draft=True,
                )
            elif mem_item is not None:
                answer_draft = mem_item.answer_draft

            needs_review = (
                True
                if doc.safety_blocked
                else (
                    answer_draft.needs_human_review
                    if answer_draft is not None
                    else (
                        mem_item.needs_human_review
                        if mem_item is not None
                        else intent_val in LOCAL_MODEL_REVIEW_REASONS
                    )
                )
            )
            reason = (
                "Comment blocked by chat moderation."
                if doc.safety_blocked
                else (
                    mem_item.reason
                    if mem_item is not None
                    else (
                        answer_draft.reason
                        if answer_draft is not None
                        else LOCAL_MODEL_REVIEW_REASONS.get(intent_val)
                    )
                )
            )

            thread = threads_by_comment.get(doc.comment_id)
            if thread is None:
                reconstructed_thread: list[ThreadMessage] = []
                ts_ms = int(doc.timestamp.timestamp() * 1000)
                if (
                    answer_draft is not None
                    and answer_draft.status == "answered"
                    and not needs_review
                    and answer_draft.answer
                ):
                    reconstructed_thread.append(
                        ThreadMessage(
                            sender="AI Draft",
                            author_role="ai",
                            text=answer_draft.answer,
                            timestamp=ts_ms,
                        )
                    )
                if (
                    draft_doc is not None
                    and draft_doc.seller_action in {"approved", "edited"}
                    and draft_doc.final_reply_text
                    and (
                        not reconstructed_thread
                        or draft_doc.final_reply_text != reconstructed_thread[0].text
                    )
                ):
                    acted_ms = (
                        int(draft_doc.acted_at.timestamp() * 1000)
                        if draft_doc.acted_at
                        else ts_ms
                    )
                    reconstructed_thread.append(
                        ThreadMessage(
                            sender="Moderator",
                            author_role="moderator",
                            text=draft_doc.final_reply_text,
                            timestamp=acted_ms,
                        )
                    )
                threads_by_comment[doc.comment_id] = reconstructed_thread
                thread = reconstructed_thread

            lead_label_val: Literal["hot", "warm", "cold"] = (
                doc.lead_label  # type: ignore[assignment]
                if doc.lead_label in {"hot", "warm", "cold"}
                else (mem_item.lead_label if mem_item else "cold")
            )
            items.append(
                ClassifiedLiveComment(
                    comment_id=doc.comment_id,
                    sender=doc.user_handle,
                    text=doc.message,
                    author_type=mem_item.author_type if mem_item else "viewer",
                    timestamp=int(doc.timestamp.timestamp() * 1000),
                    intent=intent_val,
                    intent_source=mem_item.intent_source if mem_item else "rules",
                    intent_confidence=doc.intent_confidence,
                    lead_score=doc.lead_score if doc.lead_score is not None else 0,
                    lead_label=lead_label_val,
                    needs_human_review=needs_review,
                    reason=reason,
                    moderation_status="blocked" if doc.safety_blocked else "allowed",
                    moderation_reason=doc.safety_reason,
                    answer_draft=answer_draft,
                    thread_messages=list(thread),
                )
            )
        return items

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db_facade is not None:
            app.state.db = db_facade
            try:
                existing = await _load_comments_from_repository(
                    db_facade, limit=MAX_STORED_COMMENTS
                )
                for item in reversed(existing):
                    _upsert_recent_comment(item)
            except DatabaseError as exc:
                logger.warning("Failed to hydrate initial comments from custom facade: %s", exc)
            yield
            return

        app.state.db = None
        mongo_connected = False
        redis_connected = False
        try:
            await mongo_manager.connect()
            await initialize_database(mongo_manager.database)
            mongo_connected = True
        except DatabaseError as exc:
            logger.warning("MongoDB unavailable, using in-memory mode: %s", exc)

        if mongo_connected:
            try:
                await redis_manager.connect()
                redis_connected = True
            except DatabaseError as exc:
                logger.warning("Redis unavailable, continuing with MongoDB only: %s", exc)

            try:
                facade = StoreDatabaseFacade(
                    mongo_manager,
                    redis_manager if redis_connected else None,
                )
                await seed_demo_store_data(facade, tenant_ctx, session_id=session_id)
                app.state.db = facade
                existing = await _load_comments_from_repository(
                    facade, limit=MAX_STORED_COMMENTS
                )
                for item in reversed(existing):
                    _upsert_recent_comment(item)
            except DatabaseError as exc:
                logger.warning("Database session initialization failed, using in-memory mode: %s", exc)

        try:
            yield
        finally:
            if redis_connected:
                await redis_manager.close()
            if mongo_connected:
                await mongo_manager.close()

    app = FastAPI(title="Livestream AI Backend", version="0.1.0", lifespan=lifespan)
    app.state.db = db_facade
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    orchestrator = MultiAgentOrchestrator(
        intent_model=intent_model,
        moderator=moderator,
    )
    vector_store = get_vector_rag_store()
    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/dashboard", response_class=HTMLResponse)
    def serve_dashboard() -> HTMLResponse:
        if dashboard_path.is_file():
            return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Livestream AI Backend is running</h1>")

    @app.get("/health")
    async def health() -> dict[str, str | int | bool | None]:
        ollama = get_ollama_provider()
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        mongo_ping = (
            await facade.mongo.ping()
            if facade is not None and getattr(facade, "mongo", None) is not None
            else await mongo_manager.ping()
        )
        redis_ping = (
            await facade.redis.ping()
            if facade is not None and getattr(facade, "redis", None) is not None
            else await redis_manager.ping()
        )
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
            "orchestrator_backend": orchestrator.architecture_summary["orchestrator_backend"],
            "vector_rag_backend": vector_store.backend,
        }

    @app.post("/api/plugin/comments", response_model=ClassifiedLiveComment)
    async def receive_comment(payload: LiveCommentPayload) -> ClassifiedLiveComment:
        safe_incoming_id = _to_safe_id(payload.comment_id)
        for existing in recent_comments:
            if existing.comment_id == safe_incoming_id:
                return existing

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
        thread = threads_by_comment.setdefault(result.comment_id, [])
        draft_ready = bool(
            result.answer_draft
            and result.answer_draft.status == "answered"
            and not result.needs_human_review
            and not result.answer_draft.needs_human_review
            and result.answer_draft.answer
        )
        if draft_ready and not any(m.author_role == "ai" for m in thread):
            thread.append(
                ThreadMessage(
                    sender="AI Draft",
                    author_role="ai",
                    text=result.answer_draft.answer,
                )
            )
        result.thread_messages = thread
        _upsert_recent_comment(result)

        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                persistence = await facade.record_comment_with_operational_sync(
                    tenant_ctx,
                    LivestreamCommentDocument(
                        store_id=store_id,
                        session_id=session_id,
                        comment_id=result.comment_id,
                        platform="youtube",
                        user_handle=result.sender[:256],
                        message=result.text[:1000],
                        timestamp=_safe_timestamp_to_datetime(result.timestamp),
                        intent=result.intent,
                        intent_confidence=result.intent_confidence,
                        lead_label=result.lead_label,
                        lead_score=result.lead_score,
                        safety_blocked=result.moderation_status == "blocked",
                        safety_reason=result.moderation_reason,
                    ),
                )
                if persistence.inserted_into_mongo and (
                    result.lead_label == "hot" or result.answer_draft is not None
                ):
                    await facade.sessions.increment_metrics(
                        tenant_ctx,
                        session_id,
                        high_lead_comments=1 if result.lead_label == "hot" else 0,
                        drafts_generated=1 if result.answer_draft is not None else 0,
                        drafts_approved=0,
                    )
                if (
                    persistence.inserted_into_mongo
                    and result.answer_draft
                    and result.answer_draft.answer
                ):
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
                            seller_action="pending",
                            final_reply_text=None,
                            acted_by_user_id=None,
                            acted_at=None,
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
            except (DatabaseError, ValidationError) as exc:
                logger.warning("Failed to persist comment %s: %s", result.comment_id, exc)

        return result

    @app.get("/api/plugin/comments", response_model=list[ClassifiedLiveComment])
    async def list_comments(
        limit: int = Query(default=100, ge=1, le=MAX_STORED_COMMENTS),
    ) -> list[ClassifiedLiveComment]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                repo_items = await _load_comments_from_repository(facade, limit=limit)
                for item in reversed(repo_items):
                    _upsert_recent_comment(item)
                return repo_items[:limit]
            except DatabaseError as exc:
                logger.warning("Repository comment read failed, using in-memory fallback: %s", exc)
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
                    was_pending = draft.seller_action == "pending"
                    await facade.answer_drafts.record_seller_action(
                        tenant_ctx,
                        draft.draft_id,
                        action="approved" if payload.author_role == "ai" else "edited",
                        final_reply_text=payload.text[:5000],
                    )
                    if was_pending:
                        await facade.sessions.increment_metrics(
                            tenant_ctx,
                            session_id,
                            drafts_approved=1,
                        )
            except DatabaseError as exc:
                logger.warning("Failed to persist thread reply for %s: %s", safe_comment_id, exc)

        return thread

    @app.get("/api/plugin/summary")
    async def summarize_comments() -> dict[str, object]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        source_comments: list[ClassifiedLiveComment] = list(recent_comments)
        storage_mode = "in_memory"
        if facade is not None:
            try:
                source_comments = await _load_comments_from_repository(
                    facade, limit=MAX_STORED_COMMENTS
                )
                storage_mode = "mongodb"
            except DatabaseError as exc:
                logger.warning("Repository summary read failed, using in-memory fallback: %s", exc)

        intent_counts = Counter(comment.intent for comment in source_comments)
        lead_counts = Counter(comment.lead_label for comment in source_comments)
        return {
            "storage_mode": storage_mode,
            "comment_count": len(source_comments),
            "moderated_comment_count": sum(
                comment.moderation_status == "blocked" for comment in source_comments
            ),
            "needs_human_review_count": sum(
                comment.needs_human_review for comment in source_comments
            ),
            "hot_lead_count": lead_counts.get("hot", 0),
            "lead_counts": dict(sorted(lead_counts.items())),
            "intent_counts": dict(sorted(intent_counts.items())),
        }

    @app.get("/api/agent/architecture")
    def get_agent_architecture() -> dict[str, Any]:
        return orchestrator.architecture_summary

    @app.get("/api/agent/tools")
    def list_tools() -> dict[str, Any]:
        return {
            "count": len(get_tool_definitions()),
            "tools": get_tool_definitions(),
        }

    @app.post("/api/agent/chat")
    def run_multi_agent_chat(payload: AgentChatRequest) -> dict[str, Any]:
        exec_result = orchestrator.run(
            text=payload.text,
            session_id=payload.session_id or session_id,
            comment_id=_to_safe_id(f"agent-{int(time.time() * 1000)}"),
            sender=payload.sender,
            author_type=payload.author_type,
        )
        return {
            "orchestrator_backend": exec_result.orchestrator_backend,
            "vector_backend": exec_result.vector_backend,
            "state": exec_result.state,
        }

    @app.post("/api/agent/rag/search")
    def search_vector_rag(payload: VectorRAGSearchRequest) -> dict[str, Any]:
        hits = vector_store.search(
            payload.query,
            top_k=payload.top_k,
            source_type=payload.source_type,
        )
        return {
            "query": payload.query,
            "backend": vector_store.backend,
            "count": len(hits),
            "hits": [
                {
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "title": hit.title,
                    "excerpt": hit.excerpt,
                    "score": hit.score,
                    "backend": hit.backend,
                }
                for hit in hits
            ],
        }

    @app.get("/api/store/products")
    async def list_store_products() -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                products = await facade.products.list_active_products(tenant_ctx)
                inventories = await facade.products.list_inventory_for_store(tenant_ctx)
                inv_by_prod = {inv.product_id: inv for inv in inventories}
                items = [
                    {
                        "product_id": p.product_id,
                        "sku_code": p.sku_code,
                        "name": p.name,
                        "category": p.category,
                        "price": p.price,
                        "currency": p.currency,
                        "description": p.short_description,
                        "stock_quantity": inv_by_prod[p.product_id].quantity_available
                        if p.product_id in inv_by_prod
                        else 0,
                        "in_stock": inv_by_prod[p.product_id].in_stock
                        if p.product_id in inv_by_prod
                        else False,
                        "attributes": p.attributes,
                        "storage_mode": "mongodb",
                    }
                    for p in products
                ]
                return {"count": len(items), "storage_mode": "mongodb", "items": items}
            except DatabaseError:
                pass

        dataset = load_synthetic_data()
        items = [
            {
                "product_id": p.id,
                "sku_code": f"SKU-{p.id.replace('SYN-', '')}",
                "name": p.name,
                "category": p.category,
                "price": p.price,
                "currency": "USD",
                "description": p.description,
                "stock_quantity": p.stock_quantity,
                "in_stock": p.stock_quantity > 0,
                "attributes": dict(p.attributes),
                "storage_mode": "synthetic_memory",
            }
            for p in dataset.products
        ]
        return {"count": len(items), "storage_mode": "synthetic_memory", "items": items}

    @app.get("/api/store/orders")
    async def list_store_orders(
        status: OrderStatus | None = None,
        customer_handle: str | None = None,
    ) -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                orders = await facade.orders.list_orders(
                    tenant_ctx, status=status, customer_handle=customer_handle
                )
                return {
                    "count": len(orders),
                    "storage_mode": "mongodb",
                    "items": [o.model_dump(mode="json") for o in orders],
                }
            except DatabaseError:
                pass

        demo_orders = load_demo_orders(store_id=store_id, session_id=session_id)
        if status is not None:
            demo_orders = [o for o in demo_orders if o.status == status]
        if customer_handle:
            handle_norm = customer_handle.strip().casefold()
            demo_orders = [
                o for o in demo_orders if o.customer_handle.casefold() == handle_norm
            ]
        return {
            "count": len(demo_orders),
            "storage_mode": "synthetic_memory",
            "items": [o.model_dump(mode="json") for o in demo_orders],
        }

    @app.post("/api/store/seed")
    async def trigger_seed() -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            counts = await seed_demo_store_data(facade, tenant_ctx, session_id=session_id)
            return {"seeded": True, "storage_mode": "mongodb", "counts": counts}
        dataset = load_synthetic_data()
        orders = load_demo_orders(store_id=store_id, session_id=session_id)
        return {
            "seeded": True,
            "storage_mode": "synthetic_memory",
            "counts": {
                "products": len(dataset.products),
                "faqs": len(dataset.faqs),
                "policies": len(dataset.policies),
                "orders": len(orders),
            },
        }

    return app


app = create_app()