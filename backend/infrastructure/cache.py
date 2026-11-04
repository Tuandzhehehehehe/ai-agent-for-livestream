from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.database.models import (
    LivestreamCommentDocument,
    ProductDocument,
    TenantContext,
)
from backend.database.redis import translate_redis_error

T = TypeVar("T")

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._@-]+$")

TTL_ACTIVE_SESSION_STATE_SECONDS = 86_400
TTL_RECENT_COMMENTS_SECONDS = 86_400
TTL_EVENT_STREAM_SECONDS = 86_400
TTL_DEDUPE_KEY_SECONDS = 21_600
TTL_PRODUCT_CACHE_SECONDS = 3_600
TTL_SESSION_CACHE_SECONDS = 3_600

MAX_RECENT_COMMENTS_PER_SESSION = 100


async def execute_redis(op_name: str, coro: Awaitable[T]) -> T:
    try:
        return await coro
    except RedisError as exc:
        raise translate_redis_error(op_name, exc) from exc


async def execute_pipeline(
    client: Redis,
    op_name: str,
    build_fn: Callable[[Any], Any],
) -> list[Any]:
    try:
        pipe = client.pipeline(transaction=False)
        build_fn(pipe)
        return list(await pipe.execute())
    except RedisError as exc:
        raise translate_redis_error(op_name, exc) from exc


def _validate_segment(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"Redis key segment {name!r} cannot be empty.")
    if not _SAFE_SEGMENT_RE.match(cleaned):
        raise ValueError(
            f"Redis key segment {name!r}={value!r} contains unsafe characters; "
            "only alphanumeric, '.', '_', '-', and '@' are allowed."
        )
    return cleaned


class RedisKeys:
    @staticmethod
    def session_state(store_id: str, session_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        return f"store:{s_id}:session:{sess_id}:state"

    @staticmethod
    def session_comments(store_id: str, session_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        return f"store:{s_id}:session:{sess_id}:comments"

    @staticmethod
    def session_events_stream(store_id: str, session_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        return f"store:{s_id}:session:{sess_id}:events"

    @staticmethod
    def session_pubsub_channel(store_id: str, session_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        return f"store:{s_id}:session:{sess_id}:pubsub"

    @staticmethod
    def session_dedupe(store_id: str, session_id: str, event_or_comment_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        ev_id = _validate_segment("event_or_comment_id", event_or_comment_id)
        return f"store:{s_id}:session:{sess_id}:dedupe:{ev_id}"

    @staticmethod
    def product_cache(store_id: str, product_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        prod_id = _validate_segment("product_id", product_id)
        return f"store:{s_id}:cache:product:{prod_id}"

    @staticmethod
    def session_cache(store_id: str, session_id: str) -> str:
        s_id = _validate_segment("store_id", store_id)
        sess_id = _validate_segment("session_id", session_id)
        return f"store:{s_id}:cache:session:{sess_id}"


class TenantOperationalCache:
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def set_session_state(
        self,
        ctx: TenantContext,
        session_id: str,
        state: dict[str, Any],
        *,
        ttl_seconds: int = TTL_ACTIVE_SESSION_STATE_SECONDS,
    ) -> None:
        ctx.require_write_permission()
        key = RedisKeys.session_state(ctx.store_id, session_id)
        serialized: dict[str, str] = {
            k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v)
            for k, v in state.items()
        }

        def _build(pipe: Any) -> None:
            if serialized:
                pipe.hset(key, mapping=serialized)
            pipe.expire(key, ttl_seconds)

        await execute_pipeline(self._redis, "set_session_state", _build)

    async def get_session_state(
        self,
        ctx: TenantContext,
        session_id: str,
    ) -> dict[str, Any]:
        key = RedisKeys.session_state(ctx.store_id, session_id)
        raw = await execute_redis("get_session_state", self._redis.hgetall(key))
        parsed: dict[str, Any] = {}
        for k, v in raw.items():
            try:
                parsed[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                parsed[k] = v
        return parsed

    async def clear_session_state(self, ctx: TenantContext, session_id: str) -> None:
        ctx.require_write_permission()
        key = RedisKeys.session_state(ctx.store_id, session_id)
        await execute_redis("clear_session_state", self._redis.delete(key))

    async def push_recent_comment(
        self,
        ctx: TenantContext,
        session_id: str,
        comment: LivestreamCommentDocument | dict[str, Any],
        *,
        max_items: int = MAX_RECENT_COMMENTS_PER_SESSION,
        ttl_seconds: int = TTL_RECENT_COMMENTS_SECONDS,
    ) -> int:
        ctx.require_write_permission()
        key = RedisKeys.session_comments(ctx.store_id, session_id)
        payload = (
            comment.model_dump(mode="json")
            if isinstance(comment, LivestreamCommentDocument)
            else comment
        )
        encoded = json.dumps(payload, separators=(",", ":"))

        def _build(pipe: Any) -> None:
            pipe.lpush(key, encoded)
            pipe.ltrim(key, 0, max_items - 1)
            pipe.expire(key, ttl_seconds)

        results = await execute_pipeline(self._redis, "push_recent_comment", _build)
        return min(int(results[0]), max_items)

    async def get_recent_comments(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        key = RedisKeys.session_comments(ctx.store_id, session_id)
        count = max(1, min(limit, MAX_RECENT_COMMENTS_PER_SESSION))
        raw_items = await execute_redis(
            "get_recent_comments",
            self._redis.lrange(key, 0, count - 1),
        )
        return [json.loads(item) for item in raw_items]

    async def rebuild_recent_comments_cache(
        self,
        ctx: TenantContext,
        session_id: str,
        comments_newest_first: list[LivestreamCommentDocument],
        *,
        max_items: int = MAX_RECENT_COMMENTS_PER_SESSION,
        ttl_seconds: int = TTL_RECENT_COMMENTS_SECONDS,
    ) -> int:
        ctx.require_write_permission()
        key = RedisKeys.session_comments(ctx.store_id, session_id)
        bounded = comments_newest_first[:max_items]
        encoded_items = [
            json.dumps(item.model_dump(mode="json"), separators=(",", ":"))
            for item in bounded
        ]

        def _build(pipe: Any) -> None:
            pipe.delete(key)
            if encoded_items:
                pipe.rpush(key, *encoded_items)
                pipe.expire(key, ttl_seconds)

        await execute_pipeline(self._redis, "rebuild_recent_comments_cache", _build)
        return len(encoded_items)

    async def claim_idempotency_key(
        self,
        ctx: TenantContext,
        session_id: str,
        event_or_comment_id: str,
        *,
        ttl_seconds: int = TTL_DEDUPE_KEY_SECONDS,
    ) -> bool:
        ctx.require_write_permission()
        key = RedisKeys.session_dedupe(ctx.store_id, session_id, event_or_comment_id)
        claimed = await execute_redis(
            "claim_idempotency_key",
            self._redis.set(key, "1", ex=ttl_seconds, nx=True),
        )
        return bool(claimed)

    async def is_duplicate_key(
        self,
        ctx: TenantContext,
        session_id: str,
        event_or_comment_id: str,
    ) -> bool:
        key = RedisKeys.session_dedupe(ctx.store_id, session_id, event_or_comment_id)
        exists = await execute_redis("is_duplicate_key", self._redis.exists(key))
        return bool(exists > 0)

    async def cache_product(
        self,
        ctx: TenantContext,
        product: ProductDocument | dict[str, Any],
        *,
        ttl_seconds: int = TTL_PRODUCT_CACHE_SECONDS,
    ) -> None:
        ctx.require_write_permission()
        payload = (
            product.model_dump(mode="json")
            if isinstance(product, ProductDocument)
            else product
        )
        product_id = str(payload["product_id"])
        key = RedisKeys.product_cache(ctx.store_id, product_id)
        await execute_redis(
            "cache_product",
            self._redis.set(key, json.dumps(payload, separators=(",", ":")), ex=ttl_seconds),
        )

    async def get_cached_product(
        self,
        ctx: TenantContext,
        product_id: str,
    ) -> dict[str, Any] | None:
        key = RedisKeys.product_cache(ctx.store_id, product_id)
        raw = await execute_redis("get_cached_product", self._redis.get(key))
        if raw is None:
            return None
        return json.loads(raw)

    async def invalidate_product_cache(
        self,
        ctx: TenantContext,
        product_id: str,
    ) -> None:
        ctx.require_write_permission()
        key = RedisKeys.product_cache(ctx.store_id, product_id)
        await execute_redis("invalidate_product_cache", self._redis.delete(key))

    async def publish_dashboard_notification(
        self,
        ctx: TenantContext,
        session_id: str,
        notification: dict[str, Any],
    ) -> int:
        ctx.require_write_permission()
        channel = RedisKeys.session_pubsub_channel(ctx.store_id, session_id)
        message = json.dumps(
            {
                "store_id": ctx.store_id,
                "session_id": session_id,
                **notification,
            },
            separators=(",", ":"),
        )
        receivers = await execute_redis(
            "publish_dashboard_notification",
            self._redis.publish(channel, message),
        )
        return int(receivers)

