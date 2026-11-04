from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from backend.database.models import TenantContext
from backend.infrastructure.cache import (
    TTL_DEDUPE_KEY_SECONDS,
    TTL_EVENT_STREAM_SECONDS,
    RedisKeys,
    execute_pipeline,
    execute_redis,
)

MAX_STREAM_LENGTH = 5_000


class StreamEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_entry_id: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1)
    store_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    timestamp: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RedisEventStream:
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def publish_event(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        deduplicate: bool = True,
        dedupe_ttl_seconds: int = TTL_DEDUPE_KEY_SECONDS,
        stream_ttl_seconds: int = TTL_EVENT_STREAM_SECONDS,
        maxlen: int = MAX_STREAM_LENGTH,
    ) -> str | None:
        ctx.require_write_permission()
        if deduplicate:
            dedupe_key = RedisKeys.session_dedupe(ctx.store_id, session_id, event_id)
            claimed = await execute_redis(
                "publish_event_dedupe",
                self._redis.set(dedupe_key, "1", ex=dedupe_ttl_seconds, nx=True),
            )
            if not claimed:
                return None

        stream_key = RedisKeys.session_events_stream(ctx.store_id, session_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        fields: dict[str, str] = {
            "event_id": event_id,
            "store_id": ctx.store_id,
            "session_id": session_id,
            "event_type": event_type,
            "timestamp": now_iso,
            "payload_json": json.dumps(payload, separators=(",", ":")),
        }

        def _build(pipe: Any) -> None:
            pipe.xadd(
                stream_key,
                fields,
                maxlen=maxlen,
                approximate=True,
            )
            pipe.expire(stream_key, stream_ttl_seconds)

        results = await execute_pipeline(self._redis, "publish_event", _build)
        return str(results[0])

    async def ensure_consumer_group(
        self,
        ctx: TenantContext,
        session_id: str,
        group_name: str,
        *,
        start_id: str = "0",
    ) -> None:
        ctx.require_write_permission()
        stream_key = RedisKeys.session_events_stream(ctx.store_id, session_id)
        try:
            await self._redis.xgroup_create(
                name=stream_key,
                groupname=group_name,
                id=start_id,
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return
            raise

    async def read_group_events(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        group_name: str,
        consumer_name: str,
        count: int = 20,
        block_ms: int | None = None,
        stream_id: str = ">",
    ) -> list[StreamEventRecord]:
        stream_key = RedisKeys.session_events_stream(ctx.store_id, session_id)
        response = await execute_redis(
            "read_group_events",
            self._redis.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_key: stream_id},
                count=count,
                block=block_ms,
            ),
        )
        return self._parse_xread_response(response, expected_store_id=ctx.store_id)

    async def read_stream_range(
        self,
        ctx: TenantContext,
        session_id: str,
        *,
        min_id: str = "-",
        max_id: str = "+",
        count: int = 100,
    ) -> list[StreamEventRecord]:
        stream_key = RedisKeys.session_events_stream(ctx.store_id, session_id)
        entries = await execute_redis(
            "read_stream_range",
            self._redis.xrange(stream_key, min=min_id, max=max_id, count=count),
        )
        records: list[StreamEventRecord] = []
        for entry_id, fields in entries:
            record = self._decode_entry(str(entry_id), fields)
            if record.store_id == ctx.store_id:
                records.append(record)
        return records

    async def ack_events(
        self,
        ctx: TenantContext,
        session_id: str,
        group_name: str,
        *stream_entry_ids: str,
    ) -> int:
        ctx.require_write_permission()
        if not stream_entry_ids:
            return 0
        stream_key = RedisKeys.session_events_stream(ctx.store_id, session_id)
        acked = await execute_redis(
            "ack_events",
            self._redis.xack(stream_key, group_name, *stream_entry_ids),
        )
        return int(acked)

    @classmethod
    def _parse_xread_response(
        cls,
        response: Any,
        *,
        expected_store_id: str,
    ) -> list[StreamEventRecord]:
        if not response:
            return []
        records: list[StreamEventRecord] = []
        for _stream_name, entries in response:
            for entry_id, fields in entries:
                record = cls._decode_entry(str(entry_id), fields)
                if record.store_id == expected_store_id:
                    records.append(record)
        return records

    @staticmethod
    def _decode_entry(entry_id: str, fields: dict[str, Any]) -> StreamEventRecord:
        raw_payload = fields.get("payload_json", "{}")
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else dict(raw_payload)
        except json.JSONDecodeError:
            payload = {}

        return StreamEventRecord(
            stream_entry_id=entry_id,
            event_id=str(fields.get("event_id", "")),
            store_id=str(fields.get("store_id", "")),
            session_id=str(fields.get("session_id", "")),
            event_type=str(fields.get("event_type", "")),
            timestamp=str(fields.get("timestamp", "")),
            payload=payload,
        )

