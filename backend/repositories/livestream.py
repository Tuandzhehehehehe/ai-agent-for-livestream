from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from backend.database.models import (
    AnswerDraftDocument,
    DraftActionStatus,
    LivestreamCommentDocument,
    LivestreamSessionDocument,
    SessionStatus,
    TenantContext,
    utc_now,
)
from backend.database.mongodb import (
    COLL_ANSWER_DRAFTS,
    COLL_CUSTOMERS,
    COLL_LIVESTREAM_COMMENTS,
    COLL_LIVESTREAM_SESSIONS,
    COLL_PRODUCTS,
    DatabaseOperationError,
    DuplicateDocumentError,
)
from backend.repositories.base import BaseTenantRepository


class LivestreamRepository(BaseTenantRepository[LivestreamSessionDocument]):
    collection_name = COLL_LIVESTREAM_SESSIONS
    model_cls = LivestreamSessionDocument
    id_field = "session_id"

    async def create_session(
        self,
        ctx: TenantContext,
        session: LivestreamSessionDocument,
    ) -> LivestreamSessionDocument:
        self._assert_document_tenant(ctx, session)
        await self._assert_scoped_refs_exist(
            ctx,
            COLL_PRODUCTS,
            "product_id",
            session.pinned_product_ids,
            label="Pinned product",
        )
        return await self._insert(session)

    async def list_sessions(
        self,
        ctx: TenantContext,
        *,
        status: SessionStatus | None = None,
        limit: int = 50,
    ) -> list[LivestreamSessionDocument]:
        extra: dict[str, Any] = {}
        if status is not None:
            extra["status"] = status
        return await self.list_in_tenant(ctx, extra, sort=[("created_at", -1)], limit=limit)

    async def transition_status(
        self,
        ctx: TenantContext,
        session_id: str,
        status: SessionStatus,
        *,
        timestamp: datetime | None = None,
    ) -> LivestreamSessionDocument | None:
        now = timestamp or utc_now()
        updates: dict[str, Any] = {"status": status}
        if status == "live":
            updates["started_at"] = now
        elif status == "ended":
            updates["ended_at"] = now
        return await self.update_by_id(ctx, session_id, updates)

    async def set_pinned_products(
        self,
        ctx: TenantContext,
        session_id: str,
        pinned_product_ids: list[str],
    ) -> LivestreamSessionDocument | None:
        ctx.require_write_permission()
        await self._assert_scoped_refs_exist(
            ctx,
            COLL_PRODUCTS,
            "product_id",
            pinned_product_ids,
            label="Pinned product",
        )
        return await self.update_by_id(
            ctx,
            session_id,
            {"pinned_product_ids": pinned_product_ids},
        )

    async def increment_metrics(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        total_comments: int = 0,
        high_lead_comments: int = 0,
        drafts_generated: int = 0,
        drafts_approved: int = 0,
    ) -> LivestreamSessionDocument | None:
        inc_fields: dict[str, int] = {}
        if total_comments:
            inc_fields["metrics_summary.total_comments"] = total_comments
        if high_lead_comments:
            inc_fields["metrics_summary.high_lead_comments"] = high_lead_comments
        if drafts_generated:
            inc_fields["metrics_summary.drafts_generated"] = drafts_generated
        if drafts_approved:
            inc_fields["metrics_summary.drafts_approved"] = drafts_approved

        if not inc_fields:
            return await self.get_by_id(ctx, session_id)

        try:
            raw = await self._collection.find_one_and_update(
                self._scoped_filter(ctx, {"session_id": session_id}, require_write=True),
                {
                    "$inc": inc_fields,
                    "$set": {"updated_at": utc_now()},
                },
                return_document=ReturnDocument.AFTER,
            )
            return self._deserialize(raw)
        except PyMongoError as exc:
            raise DatabaseOperationError(f"Failed to increment metrics for session {session_id!r}: {exc}") from exc


LivestreamSessionRepository = LivestreamRepository


class CommentRepository(BaseTenantRepository[LivestreamCommentDocument]):
    collection_name = COLL_LIVESTREAM_COMMENTS
    model_cls = LivestreamCommentDocument
    id_field = "comment_id"

    async def append_comment(
        self,
        ctx: TenantContext,
        comment: LivestreamCommentDocument,
    ) -> tuple[LivestreamCommentDocument, bool]:
        self._assert_document_tenant(ctx, comment)
        checks = [
            self._assert_scoped_ref_exists(
                ctx,
                COLL_LIVESTREAM_SESSIONS,
                "session_id",
                comment.session_id,
                label="Livestream session",
            )
        ]
        if comment.customer_id is not None:
            checks.append(
                self._assert_scoped_ref_exists(
                    ctx,
                    COLL_CUSTOMERS,
                    "customer_id",
                    comment.customer_id,
                    label="Customer",
                )
            )
        await asyncio.gather(*checks)

        try:
            created = await self._insert(comment)
            return created, True
        except DuplicateDocumentError:
            existing = await self.get_by_id(ctx, comment.comment_id)
            if existing is not None:
                return existing, False
            raise

    async def list_recent_for_session(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[LivestreamCommentDocument]:
        return await self.list_in_tenant(
            ctx,
            {"session_id": session_id},
            sort=[("timestamp", -1)],
            limit=limit,
        )

    async def list_chronological_for_session(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        limit: int = 200,
        skip: int = 0,
    ) -> list[LivestreamCommentDocument]:
        return await self.list_in_tenant(
            ctx,
            {"session_id": session_id},
            sort=[("timestamp", 1)],
            limit=limit,
            skip=skip,
        )

    async def update_processing_metadata(
        self,
        ctx: TenantContext,
        comment_id: str,
        *,
        intent: str | None = None,
        intent_confidence: float | None = None,
        lead_label: str | None = None,
        lead_score: int | None = None,
        matched_product_ids: list[str] | None = None,
        safety_blocked: bool | None = None,
        safety_reason: str | None = None,
    ) -> LivestreamCommentDocument | None:
        updates: dict[str, Any] = {}
        if intent is not None:
            updates["intent"] = intent
        if intent_confidence is not None:
            updates["intent_confidence"] = intent_confidence
        if lead_label is not None:
            updates["lead_label"] = lead_label
        if lead_score is not None:
            updates["lead_score"] = lead_score
        if matched_product_ids is not None:
            updates["matched_product_ids"] = matched_product_ids
        if safety_blocked is not None:
            updates["safety_blocked"] = safety_blocked
        if safety_reason is not None:
            updates["safety_reason"] = safety_reason
        return await self.update_by_id(ctx, comment_id, updates)


LivestreamCommentRepository = CommentRepository


class AnswerRepository(BaseTenantRepository[AnswerDraftDocument]):
    collection_name = COLL_ANSWER_DRAFTS
    model_cls = AnswerDraftDocument
    id_field = "draft_id"

    async def save_draft(
        self,
        ctx: TenantContext,
        draft: AnswerDraftDocument,
    ) -> AnswerDraftDocument:
        self._assert_document_tenant(ctx, draft)
        await asyncio.gather(
            self._assert_scoped_ref_exists(
                ctx,
                COLL_LIVESTREAM_SESSIONS,
                "session_id",
                draft.session_id,
                label="Livestream session",
            ),
            self._assert_exists(
                COLL_LIVESTREAM_COMMENTS,
                {
                    "store_id": ctx.store_id,
                    "session_id": draft.session_id,
                    "comment_id": draft.comment_id,
                },
                f"Comment {draft.comment_id!r} in session {draft.session_id!r} "
                f"does not exist in store {ctx.store_id!r}.",
            ),
        )
        return await self._insert(draft)

    async def get_latest_for_comment(
        self,
        ctx: TenantContext,
        comment_id: str,
    ) -> AnswerDraftDocument | None:
        results = await self.list_in_tenant(
            ctx,
            {"comment_id": comment_id},
            sort=[("created_at", -1)],
            limit=1,
        )
        return results[0] if results else None

    async def list_for_session(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        seller_action: DraftActionStatus | None = None,
        limit: int = 100,
    ) -> list[AnswerDraftDocument]:
        extra: dict[str, Any] = {"session_id": session_id}
        if seller_action is not None:
            extra["seller_action"] = seller_action
        return await self.list_in_tenant(ctx, extra, sort=[("created_at", -1)], limit=limit)

    async def record_seller_action(
        self,
        ctx: TenantContext,
        draft_id: str,
        *,
        action: DraftActionStatus,
        final_reply_text: str | None = None,
    ) -> AnswerDraftDocument | None:
        updates: dict[str, Any] = {
            "seller_action": action,
            "acted_by_user_id": ctx.user_id,
            "acted_at": utc_now(),
        }
        if final_reply_text is not None:
            updates["final_reply_text"] = final_reply_text
        return await self.update_by_id(ctx, draft_id, updates)


AnswerDraftRepository = AnswerRepository
