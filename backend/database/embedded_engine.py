"""Embedded local MongoDB (livestream_sales_db) and Redis engine for zero-Docker execution.

Implements the exact async PyMongo and redis.asyncio interfaces required by
StoreDatabaseFacade, all 13 MongoDB collections, and all 7 Redis key patterns
specified in Section 3.2.4 (Database Design).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import time
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from redis.exceptions import ResponseError

from backend.database.mongodb import (
    ALL_COLLECTIONS,
    COLL_ANSWER_DRAFTS,
    COLL_CUSTOMERS,
    COLL_FAQS,
    COLL_INVENTORY,
    COLL_LIVESTREAM_COMMENTS,
    COLL_LIVESTREAM_SESSIONS,
    COLL_ORDERS,
    COLL_PRODUCT_VARIANTS,
    COLL_PRODUCTS,
    COLL_SALES_POLICIES,
    COLL_STORE_MEMBERSHIPS,
    COLL_STORES,
    COLL_USERS,
)

UNIQUE_KEYS_BY_COLLECTION: dict[str, list[tuple[str, ...]]] = {
    COLL_USERS: [("user_id",), ("email",)],
    COLL_STORES: [("store_id",), ("store_id", "slug")],
    COLL_STORE_MEMBERSHIPS: [("store_id", "membership_id"), ("store_id", "user_id")],
    COLL_PRODUCTS: [("store_id", "product_id"), ("store_id", "sku_code")],
    COLL_PRODUCT_VARIANTS: [("store_id", "variant_id"), ("store_id", "sku_code")],
    COLL_INVENTORY: [("store_id", "inventory_id"), ("store_id", "inventory_scope_key")],
    COLL_FAQS: [("store_id", "faq_id")],
    COLL_SALES_POLICIES: [("store_id", "policy_id", "version")],
    COLL_CUSTOMERS: [("store_id", "customer_id"), ("store_id", "platform", "platform_user_handle")],
    COLL_LIVESTREAM_SESSIONS: [("store_id", "session_id")],
    COLL_LIVESTREAM_COMMENTS: [("store_id", "comment_id")],
    COLL_ANSWER_DRAFTS: [("store_id", "draft_id")],
    COLL_ORDERS: [("store_id", "order_id")],
}


def _matches_filter(doc: dict[str, Any], filter_query: dict[str, Any] | None) -> bool:
    if not filter_query:
        return True
    for key, expected in filter_query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if isinstance(actual, list):
                    if not any(item in expected["$in"] for item in actual):
                        return False
                elif actual not in expected["$in"]:
                    return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        else:
            if isinstance(actual, list) and not isinstance(expected, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
    return True


def _apply_nested_inc(doc: dict[str, Any], dotted_key: str, delta: int | float) -> None:
    parts = dotted_key.split(".")
    target = doc
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]
    leaf = parts[-1]
    target[leaf] = target.get(leaf, 0) + delta


def _sort_key_value(val: Any) -> Any:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.timestamp()
    return val


class EmbeddedAsyncCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, sort_spec: list[tuple[str, int]]) -> EmbeddedAsyncCursor:
        for field, direction in reversed(sort_spec):
            self._docs.sort(
                key=lambda d: _sort_key_value(d.get(field)),
                reverse=(direction < 0),
            )
        return self

    def skip(self, count: int) -> EmbeddedAsyncCursor:
        self._docs = self._docs[count:]
        return self

    def limit(self, count: int) -> EmbeddedAsyncCursor:
        if count > 0:
            self._docs = self._docs[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is not None and length > 0:
            return [deepcopy(d) for d in self._docs[:length]]
        return [deepcopy(d) for d in self._docs]


class _DeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class EmbeddedAsyncCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self._docs: list[dict[str, Any]] = []
        self._next_id = 1
        self._indexes: list[str] = ["_id_"]

    def _check_unique(self, candidate: dict[str, Any], ignore_index: int | None = None) -> None:
        for key_tuple in UNIQUE_KEYS_BY_COLLECTION.get(self.name, []):
            vals = tuple(candidate.get(k) for k in key_tuple)
            if any(v is None for v in vals):
                continue
            for idx, existing in enumerate(self._docs):
                if ignore_index is not None and idx == ignore_index:
                    continue
                if tuple(existing.get(k) for k in key_tuple) == vals:
                    raise DuplicateKeyError(f"Duplicate key in {self.name}: {key_tuple}={vals}")

    async def create_indexes(self, index_models: list[Any]) -> list[str]:
        names: list[str] = []
        for idx_model in index_models:
            name = str(idx_model.document.get("name", f"idx_{len(self._indexes)}"))
            if name not in self._indexes:
                self._indexes.append(name)
            names.append(name)
        return names

    async def find_one(
        self,
        filter_query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        for doc in self._docs:
            if _matches_filter(doc, filter_query):
                result = deepcopy(doc)
                if projection:
                    include_keys = {k for k, v in projection.items() if v}
                    if include_keys:
                        result = {k: v for k, v in result.items() if k in include_keys}
                return result
        return None

    def find(
        self,
        filter_query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> EmbeddedAsyncCursor:
        matched: list[dict[str, Any]] = []
        for doc in self._docs:
            if _matches_filter(doc, filter_query):
                item = deepcopy(doc)
                if projection:
                    include_keys = {k for k, v in projection.items() if v}
                    if include_keys:
                        item = {k: v for k, v in item.items() if k in include_keys}
                matched.append(item)
        return EmbeddedAsyncCursor(matched)

    async def insert_one(self, payload: dict[str, Any]) -> Any:
        doc = deepcopy(payload)
        self._check_unique(doc)
        doc.setdefault("_id", f"oid-{self._next_id}")
        self._next_id += 1
        self._docs.append(doc)
        return doc["_id"]

    async def find_one_and_update(
        self,
        filter_query: dict[str, Any],
        update_doc: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any = ReturnDocument.AFTER,
    ) -> dict[str, Any] | None:
        for idx, existing in enumerate(self._docs):
            if _matches_filter(existing, filter_query):
                updated = deepcopy(existing)
                if "$set" in update_doc:
                    updated.update(deepcopy(update_doc["$set"]))
                if "$inc" in update_doc:
                    for k, delta in update_doc["$inc"].items():
                        _apply_nested_inc(updated, k, delta)
                self._check_unique(updated, ignore_index=idx)
                self._docs[idx] = updated
                return deepcopy(updated if return_document == ReturnDocument.AFTER else existing)

        if not upsert:
            return None

        new_doc: dict[str, Any] = {}
        for k, v in filter_query.items():
            if not isinstance(v, dict):
                new_doc[k] = deepcopy(v)
        if "$setOnInsert" in update_doc:
            new_doc.update(deepcopy(update_doc["$setOnInsert"]))
        if "$set" in update_doc:
            new_doc.update(deepcopy(update_doc["$set"]))
        if "$inc" in update_doc:
            for k, delta in update_doc["$inc"].items():
                _apply_nested_inc(new_doc, k, delta)
        self._check_unique(new_doc)
        new_doc.setdefault("_id", f"oid-{self._next_id}")
        self._next_id += 1
        self._docs.append(new_doc)
        return deepcopy(new_doc)

    async def update_many(
        self,
        filter_query: dict[str, Any],
        update_doc: dict[str, Any],
    ) -> int:
        count = 0
        for idx, existing in enumerate(self._docs):
            if _matches_filter(existing, filter_query):
                updated = deepcopy(existing)
                if "$set" in update_doc:
                    updated.update(deepcopy(update_doc["$set"]))
                self._docs[idx] = updated
                count += 1
        return count

    async def delete_one(self, filter_query: dict[str, Any]) -> _DeleteResult:
        for idx, existing in enumerate(self._docs):
            if _matches_filter(existing, filter_query):
                self._docs.pop(idx)
                return _DeleteResult(1)
        return _DeleteResult(0)

    async def delete_many(self, filter_query: dict[str, Any]) -> _DeleteResult:
        kept = [d for d in self._docs if not _matches_filter(d, filter_query)]
        deleted = len(self._docs) - len(kept)
        self._docs = kept
        return _DeleteResult(deleted)

    async def count_documents(self, filter_query: dict[str, Any] | None = None) -> int:
        return sum(1 for d in self._docs if _matches_filter(d, filter_query))


class EmbeddedAsyncDatabase:
    def __init__(self, name: str = "livestream_sales_db") -> None:
        self.name = name
        self._collections: dict[str, EmbeddedAsyncCollection] = {
            coll_name: EmbeddedAsyncCollection(coll_name)
            for coll_name in ALL_COLLECTIONS
        }
        self._validators: dict[str, dict[str, Any]] = {}

    def __getitem__(self, name: str) -> EmbeddedAsyncCollection:
        if name not in self._collections:
            self._collections[name] = EmbeddedAsyncCollection(name)
        return self._collections[name]

    async def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())

    async def create_collection(
        self,
        name: str,
        *,
        validator: dict[str, Any] | None = None,
        validationLevel: str = "strict",
        validationAction: str = "error",
    ) -> EmbeddedAsyncCollection:
        if name not in self._collections:
            self._collections[name] = EmbeddedAsyncCollection(name)
        if validator is not None:
            self._validators[name] = validator
        return self._collections[name]

    async def command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if "collMod" in cmd and "validator" in cmd:
            self._validators[str(cmd["collMod"])] = cmd["validator"]
        return {"ok": 1.0}


class EmbeddedMongoDatabaseManager:
    """Drop-in local MongoDatabaseManager for livestream_sales_db with all 13 collections."""

    def __init__(self, database_name: str = "livestream_sales_db") -> None:
        self.database_name = database_name
        self._db = EmbeddedAsyncDatabase(database_name)
        self._connected = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def database(self) -> Any:
        return self._db

    def get_collection(self, name: str) -> Any:
        return self._db[name]

    async def connect(self) -> None:
        self._connected = True

    async def ping(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "database": self.database_name,
            "collections": len(ALL_COLLECTIONS),
        }

    async def close(self) -> None:
        self._connected = False


class _EmbeddedRedisPipeline:
    def __init__(self, client: EmbeddedAsyncRedisClient) -> None:
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def hset(self, key: str, mapping: dict[str, str] | None = None) -> _EmbeddedRedisPipeline:
        self._ops.append(("hset", (key,), {"mapping": mapping or {}}))
        return self

    def expire(self, key: str, ttl_seconds: int) -> _EmbeddedRedisPipeline:
        self._ops.append(("expire", (key, ttl_seconds), {}))
        return self

    def lpush(self, key: str, *values: str) -> _EmbeddedRedisPipeline:
        self._ops.append(("lpush", (key, *values), {}))
        return self

    def rpush(self, key: str, *values: str) -> _EmbeddedRedisPipeline:
        self._ops.append(("rpush", (key, *values), {}))
        return self

    def ltrim(self, key: str, start: int, end: int) -> _EmbeddedRedisPipeline:
        self._ops.append(("ltrim", (key, start, end), {}))
        return self

    def delete(self, *keys: str) -> _EmbeddedRedisPipeline:
        self._ops.append(("delete", keys, {}))
        return self

    def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> _EmbeddedRedisPipeline:
        self._ops.append(("xadd", (key, fields), {"maxlen": maxlen, "approximate": approximate}))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for method_name, args, kwargs in self._ops:
            fn = getattr(self._client, method_name)
            results.append(await fn(*args, **kwargs))
        self._ops.clear()
        return results


class EmbeddedAsyncRedisClient:
    """In-process Redis client supporting Hash, List, Stream, Pub/Sub, and String key patterns."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.strings: dict[str, str] = {}
        self.pubsub_history: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.consumer_groups: dict[tuple[str, str], int] = {}
        self._stream_seq = 0

    def pipeline(self, transaction: bool = False) -> _EmbeddedRedisPipeline:
        return _EmbeddedRedisPipeline(self)

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, mapping: dict[str, str] | None = None) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping or {})

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def lpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for val in values:
            lst.insert(0, str(val))
        return len(lst)

    async def rpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for val in values:
            lst.append(str(val))
        return len(lst)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self.lists:
            if end < 0:
                self.lists[key] = self.lists[key][start:]
            else:
                self.lists[key] = self.lists[key][start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        if end < 0:
            return list(lst[start:])
        return list(lst[start : end + 1])

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def exists(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if (
                key in self.strings
                or key in self.hashes
                or key in self.lists
                or key in self.streams
            ):
                count += 1
        return count

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            removed = False
            for store in (self.strings, self.hashes, self.lists, self.streams):
                if key in store:
                    store.pop(key, None)
                    removed = True
            self.ttls.pop(key, None)
            if removed:
                deleted += 1
        return deleted

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.ttls[key] = int(ttl_seconds)
        return True

    async def publish(self, channel: str, message: str) -> int:
        history = self.pubsub_history.setdefault(channel, [])
        history.append(str(message))
        if len(history) > 100:
            del history[:-100]
        return 1

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._stream_seq += 1
        entry_id = f"{int(time.time() * 1000)}-{self._stream_seq}"
        stream = self.streams.setdefault(key, [])
        stream.append((entry_id, {str(k): str(v) for k, v in fields.items()}))
        if maxlen is not None and maxlen > 0 and len(stream) > maxlen:
            del stream[:-maxlen]
        return entry_id

    async def xrange(
        self,
        key: str,
        *,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        entries = list(self.streams.get(key, []))
        if count is not None and count > 0:
            return entries[:count]
        return entries

    async def xgroup_create(
        self,
        *,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        if mkstream and name not in self.streams:
            self.streams[name] = []
        gkey = (name, groupname)
        if gkey in self.consumer_groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        self.consumer_groups[gkey] = 0
        return True

    async def xreadgroup(
        self,
        *,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int = 20,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        output: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream_key in streams:
            gkey = (stream_key, groupname)
            offset = self.consumer_groups.get(gkey, 0)
            all_entries = self.streams.get(stream_key, [])
            slice_entries = all_entries[offset : offset + count]
            self.consumer_groups[gkey] = offset + len(slice_entries)
            if slice_entries:
                output.append((stream_key, slice_entries))
        return output

    async def xack(self, key: str, groupname: str, *entry_ids: str) -> int:
        return len(entry_ids)


class EmbeddedRedisManager:
    """Drop-in local RedisManager supporting all 7 Redis data structures."""

    def __init__(self) -> None:
        self._client = EmbeddedAsyncRedisClient()
        self._connected = True

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def client(self) -> Any:
        return self._client

    async def connect(self) -> None:
        self._connected = True

    async def ping(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def close(self) -> None:
        self._connected = False
