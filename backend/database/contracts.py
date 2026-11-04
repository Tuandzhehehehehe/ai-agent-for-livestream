from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.database.models import (
    LivestreamCommentDocument,
    MembershipRole,
    TenantContext,
)
from backend.database.mongodb import MongoDatabaseManager
from backend.database.redis import RedisManager, RedisOperationError
from backend.infrastructure.cache import TenantOperationalCache
from backend.infrastructure.streams import RedisEventStream
from backend.repositories import (
    AnswerDraftRepository,
    CustomerRepository,
    FAQRepository,
    LivestreamCommentRepository,
    LivestreamSessionRepository,
    MembershipRepository,
    ProductRepository,
    SalesPolicyRepository,
    StoreRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommentPersistenceResult:
    comment: LivestreamCommentDocument
    inserted_into_mongo: bool
    cached_in_redis: bool
    stream_entry_id: str | None
    redis_error: str | None = None


class StoreDatabaseFacade:
    def __init__(
        self,
        mongo_manager: MongoDatabaseManager,
        redis_manager: RedisManager | None = None,
    ) -> None:
        db = mongo_manager.database
        self.mongo = mongo_manager
        self.redis = redis_manager

        self.users = UserRepository(db)
        self.stores = StoreRepository(db)
        self.memberships = MembershipRepository(db)
        self.products = ProductRepository(db)
        self.faqs = FAQRepository(db)
        self.sales_policies = SalesPolicyRepository(db)
        self.customers = CustomerRepository(db)
        self.sessions = LivestreamSessionRepository(db)
        self.comments = LivestreamCommentRepository(db)
        self.answer_drafts = AnswerDraftRepository(db)

        self.cache: TenantOperationalCache | None = (
            TenantOperationalCache(redis_manager.client)
            if redis_manager is not None and redis_manager.is_connected
            else None
        )
        self.streams: RedisEventStream | None = (
            RedisEventStream(redis_manager.client)
            if redis_manager is not None and redis_manager.is_connected
            else None
        )

    async def authorize_tenant(
        self,
        *,
        store_id: str,
        user_id: str,
        allowed_roles: set[MembershipRole] | None = None,
    ) -> TenantContext:
        return await self.memberships.verify_tenant_access(
            store_id=store_id,
            user_id=user_id,
            allowed_roles=allowed_roles,
        )

    async def record_comment_with_operational_sync(
        self,
        ctx: TenantContext,
        comment: LivestreamCommentDocument,
        *,
        publish_to_stream: bool = True,
    ) -> CommentPersistenceResult:
        persisted_comment, inserted = await self.comments.append_comment(ctx, comment)

        if inserted:
            await self.sessions.increment_metrics(
                ctx,
                comment.session_id,
                total_comments=1,
            )

        cached = False
        stream_entry_id: str | None = None
        redis_error_msg: str | None = None

        if self.cache is not None and inserted:
            try:
                await self.cache.push_recent_comment(ctx, comment.session_id, persisted_comment)
                cached = True
            except RedisOperationError as exc:
                redis_error_msg = str(exc)
                logger.warning(
                    "Non-fatal Redis cache sync failure for comment %s: %s",
                    comment.comment_id,
                    exc,
                )

        if publish_to_stream and self.streams is not None:
            try:
                stream_entry_id = await self.streams.publish_event(
                    ctx,
                    comment.session_id,
                    event_id=comment.comment_id,
                    event_type="comment.ingested",
                    payload=persisted_comment.model_dump(mode="json"),
                    deduplicate=True,
                )
            except RedisOperationError as exc:
                redis_error_msg = str(exc)
                logger.warning(
                    "Non-fatal Redis stream publish failure for comment %s: %s",
                    comment.comment_id,
                    exc,
                )

        return CommentPersistenceResult(
            comment=persisted_comment,
            inserted_into_mongo=inserted,
            cached_in_redis=cached,
            stream_entry_id=stream_entry_id,
            redis_error=redis_error_msg,
        )

    async def recover_session_comment_cache_from_mongo(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        limit: int = 50,
    ) -> int:
        if self.cache is None:
            return 0
        recent = await self.comments.list_recent_for_session(ctx, session_id, limit=limit)
        return await self.cache.rebuild_recent_comments_cache(ctx, session_id, recent)

