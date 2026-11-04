from __future__ import annotations

from typing import Any, Generic, TypeVar

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from backend.database.models import (
    DatabaseDocumentModel,
    TenantContext,
    TenantDocumentModel,
    utc_now,
)
from backend.database.mongodb import (
    DatabaseOperationError,
    DuplicateDocumentError,
    ReferentialIntegrityError,
    TenantIsolationError,
)

ModelT = TypeVar("ModelT", bound=DatabaseDocumentModel)
TenantModelT = TypeVar("TenantModelT", bound=TenantDocumentModel)


def compact_updates(
    updates: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if updates:
        merged.update(updates)
    for key, value in kwargs.items():
        if value is not None:
            merged[key] = value
    return merged


class BaseRepository(Generic[ModelT]):
    collection_name: str
    model_cls: type[ModelT]
    id_field: str

    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self._db = database
        self._collection: AsyncCollection[dict[str, Any]] = database[self.collection_name]

    @property
    def collection(self) -> AsyncCollection[dict[str, Any]]:
        return self._collection

    def _deserialize(self, raw: dict[str, Any] | None) -> ModelT | None:
        if raw is None:
            return None
        payload = {k: v for k, v in raw.items() if k != "_id"}
        return self.model_cls.model_validate(payload)

    def _serialize(self, document: ModelT) -> dict[str, Any]:
        return document.to_mongo_dict()

    async def _assert_exists(
        self,
        collection_name: str,
        filter_query: dict[str, Any],
        error_message: str,
    ) -> None:
        try:
            doc = await self._db[collection_name].find_one(filter_query, {"_id": 1})
        except PyMongoError as exc:
            raise DatabaseOperationError(f"Failed to verify reference in '{collection_name}': {exc}") from exc
        if doc is None:
            raise ReferentialIntegrityError(error_message)

    async def _insert(self, document: ModelT) -> ModelT:
        payload = self._serialize(document)
        try:
            await self._collection.insert_one(payload)
            return document
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError(
                f"Duplicate document in '{self.collection_name}' for {self.id_field}={getattr(document, self.id_field, None)!r}."
            ) from exc
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to insert document into '{self.collection_name}': {exc}"
            ) from exc

    async def _find_one(self, filter_query: dict[str, Any]) -> ModelT | None:
        try:
            raw = await self._collection.find_one(filter_query)
            return self._deserialize(raw)
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to query '{self.collection_name}': {exc}"
            ) from exc

    async def _find_many(
        self,
        filter_query: dict[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[ModelT]:
        try:
            cursor = self._collection.find(filter_query)
            if sort:
                cursor = cursor.sort(sort)
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)
            docs = await cursor.to_list(length=limit if limit > 0 else None)
            return [self._deserialize(doc) for doc in docs if doc is not None]
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to list documents from '{self.collection_name}': {exc}"
            ) from exc

    async def _update_one(
        self,
        filter_query: dict[str, Any],
        update_fields: dict[str, Any],
        *,
        extra_operators: dict[str, Any] | None = None,
    ) -> ModelT | None:
        sanitized = {k: v for k, v in update_fields.items() if k not in {"_id", "created_at"}}
        sanitized["updated_at"] = utc_now()
        update_doc: dict[str, Any] = {"$set": sanitized}
        if extra_operators:
            for op, fields in extra_operators.items():
                if op == "$set":
                    update_doc["$set"].update(fields)
                else:
                    update_doc[op] = fields
        try:
            raw = await self._collection.find_one_and_update(
                filter_query,
                update_doc,
                return_document=ReturnDocument.AFTER,
            )
            return self._deserialize(raw)
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError(
                f"Update caused duplicate constraint violation in '{self.collection_name}'."
            ) from exc
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to update document in '{self.collection_name}': {exc}"
            ) from exc

    async def _delete_one(self, filter_query: dict[str, Any]) -> bool:
        try:
            result = await self._collection.delete_one(filter_query)
            return bool(result.deleted_count and result.deleted_count > 0)
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to delete document from '{self.collection_name}': {exc}"
            ) from exc

    async def _count(self, filter_query: dict[str, Any]) -> int:
        try:
            return int(await self._collection.count_documents(filter_query))
        except PyMongoError as exc:
            raise DatabaseOperationError(
                f"Failed to count documents in '{self.collection_name}': {exc}"
            ) from exc


class BaseTenantRepository(BaseRepository[TenantModelT], Generic[TenantModelT]):
    @staticmethod
    def _validate_ctx(ctx: TenantContext, *, require_write: bool = False) -> str:
        if not isinstance(ctx, TenantContext):
            raise TenantIsolationError("A verified TenantContext instance is required for tenant operations.")
        store_id = ctx.store_id.strip()
        if not store_id:
            raise TenantIsolationError("TenantContext.store_id cannot be empty.")
        if require_write:
            ctx.require_write_permission()
        return store_id

    def _scoped_filter(
        self,
        ctx: TenantContext,
        extra_filter: dict[str, Any] | None = None,
        *,
        require_write: bool = False,
    ) -> dict[str, Any]:
        store_id = self._validate_ctx(ctx, require_write=require_write)
        if not extra_filter:
            return {"store_id": store_id}
        if "store_id" in extra_filter and extra_filter["store_id"] != store_id:
            raise TenantIsolationError(
                f"Cross-tenant filter violation: filter store_id={extra_filter['store_id']!r} "
                f"does not match TenantContext store_id={store_id!r}."
            )
        scoped = dict(extra_filter)
        scoped["store_id"] = store_id
        return scoped

    def _assert_document_tenant(self, ctx: TenantContext, document: TenantModelT) -> None:
        store_id = self._validate_ctx(ctx, require_write=True)
        if document.store_id != store_id:
            raise TenantIsolationError(
                f"Cross-tenant write blocked: document.store_id={document.store_id!r} "
                f"does not match TenantContext.store_id={store_id!r}."
            )

    async def _assert_scoped_ref_exists(
        self,
        ctx: TenantContext,
        collection_name: str,
        id_field: str,
        id_value: str,
        label: str | None = None,
    ) -> None:
        store_id = self._validate_ctx(ctx)
        entity_label = label or collection_name
        await self._assert_exists(
            collection_name,
            {"store_id": store_id, id_field: id_value},
            f"{entity_label} {id_value!r} does not exist in store {store_id!r}.",
        )

    async def _assert_scoped_refs_exist(
        self,
        ctx: TenantContext,
        collection_name: str,
        id_field: str,
        id_values: list[str],
        label: str | None = None,
    ) -> None:
        unique_ids = list(dict.fromkeys(id_values))
        if not unique_ids:
            return
        store_id = self._validate_ctx(ctx)
        entity_label = label or collection_name
        try:
            cursor = self._db[collection_name].find(
                {"store_id": store_id, id_field: {"$in": unique_ids}},
                {id_field: 1, "_id": 0},
            )
            docs = await cursor.to_list(length=len(unique_ids))
        except PyMongoError as exc:
            raise DatabaseOperationError(f"Failed to verify references in '{collection_name}': {exc}") from exc
        found_ids = {doc[id_field] for doc in docs if id_field in doc}
        missing = [item_id for item_id in unique_ids if item_id not in found_ids]
        if missing:
            raise ReferentialIntegrityError(
                f"{entity_label} {missing[0]!r} does not exist in store {store_id!r}."
            )

    async def create_in_tenant(self, ctx: TenantContext, document: TenantModelT) -> TenantModelT:
        self._assert_document_tenant(ctx, document)
        return await self._insert(document)

    async def get_by_id(self, ctx: TenantContext, entity_id: str) -> TenantModelT | None:
        return await self._find_one(self._scoped_filter(ctx, {self.id_field: entity_id}))

    async def list_in_tenant(
        self,
        ctx: TenantContext,
        filter_query: dict[str, Any] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[TenantModelT]:
        return await self._find_many(
            self._scoped_filter(ctx, filter_query),
            sort=sort,
            limit=limit,
            skip=skip,
        )

    async def update_by_id(
        self,
        ctx: TenantContext,
        entity_id: str,
        updates: dict[str, Any],
        *,
        extra_operators: dict[str, Any] | None = None,
    ) -> TenantModelT | None:
        protected_fields = {"_id", "store_id", self.id_field, "created_at"}
        sanitized = {k: v for k, v in updates.items() if k not in protected_fields}
        return await self._update_one(
            self._scoped_filter(ctx, {self.id_field: entity_id}, require_write=True),
            sanitized,
            extra_operators=extra_operators,
        )

    async def delete_by_id(self, ctx: TenantContext, entity_id: str) -> bool:
        return await self._delete_one(
            self._scoped_filter(ctx, {self.id_field: entity_id}, require_write=True)
        )

    async def count_in_tenant(
        self,
        ctx: TenantContext,
        filter_query: dict[str, Any] | None = None,
    ) -> int:
        return await self._count(self._scoped_filter(ctx, filter_query))

