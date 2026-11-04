from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import (
    CollectionInvalid,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)

logger = logging.getLogger(__name__)

MongoUriStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=10,
        pattern=r"^mongodb(?:\+srv)?://.+",
    ),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

COLL_USERS = "users"
COLL_STORES = "stores"
COLL_STORE_MEMBERSHIPS = "store_memberships"
COLL_PRODUCTS = "products"
COLL_PRODUCT_VARIANTS = "product_variants"
COLL_INVENTORY = "inventory"
COLL_FAQS = "faqs"
COLL_SALES_POLICIES = "sales_policies"
COLL_CUSTOMERS = "customers"
COLL_LIVESTREAM_SESSIONS = "livestream_sessions"
COLL_LIVESTREAM_COMMENTS = "livestream_comments"
COLL_ANSWER_DRAFTS = "answer_drafts"

ALL_COLLECTIONS: tuple[str, ...] = (
    COLL_USERS,
    COLL_STORES,
    COLL_STORE_MEMBERSHIPS,
    COLL_PRODUCTS,
    COLL_PRODUCT_VARIANTS,
    COLL_INVENTORY,
    COLL_FAQS,
    COLL_SALES_POLICIES,
    COLL_CUSTOMERS,
    COLL_LIVESTREAM_SESSIONS,
    COLL_LIVESTREAM_COMMENTS,
    COLL_ANSWER_DRAFTS,
)


class DatabaseError(Exception):
    pass


class DatabaseConnectionError(DatabaseError):
    pass


class DatabaseOperationError(DatabaseError):
    pass


class DuplicateDocumentError(DatabaseError):
    pass


class DocumentNotFoundError(DatabaseError):
    pass


class ReferentialIntegrityError(DatabaseError):
    pass


class TenantIsolationError(DatabaseError):
    pass


class TenantAuthorizationError(DatabaseError):
    pass


class MongoSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: MongoUriStr = "mongodb://localhost:27017"
    database: NonEmptyStr = "livestream_sales_db"
    max_pool_size: int = Field(default=50, ge=1, le=500)
    min_pool_size: int = Field(default=0, ge=0, le=100)
    server_selection_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    connect_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    socket_timeout_ms: int = Field(default=10000, ge=1000, le=120000)
    app_name: str = "ai-agent-livestream-sales"

    @classmethod
    def from_env(cls) -> MongoSettings:
        explicit_uri = os.getenv("MONGODB_URI")
        if not explicit_uri:
            host = os.getenv("MONGO_HOST", "localhost")
            port = os.getenv("MONGO_PORT", "27017")
            username = os.getenv("MONGO_ROOT_USERNAME", "")
            password = os.getenv("MONGO_ROOT_PASSWORD", "")
            auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")
            if username and password:
                user_enc = quote_plus(username)
                pass_enc = quote_plus(password)
                explicit_uri = f"mongodb://{user_enc}:{pass_enc}@{host}:{port}/?authSource={auth_source}"
            else:
                explicit_uri = f"mongodb://{host}:{port}"

        return cls(
            uri=explicit_uri,
            database=os.getenv("MONGODB_DATABASE", "livestream_sales_db"),
            max_pool_size=int(os.getenv("MONGODB_MAX_POOL_SIZE", "50")),
            min_pool_size=int(os.getenv("MONGODB_MIN_POOL_SIZE", "0")),
            server_selection_timeout_ms=int(os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "5000")),
            connect_timeout_ms=int(os.getenv("MONGODB_CONNECT_TIMEOUT_MS", "5000")),
            socket_timeout_ms=int(os.getenv("MONGODB_SOCKET_TIMEOUT_MS", "10000")),
            app_name=os.getenv("MONGODB_APP_NAME", "ai-agent-livestream-sales"),
        )


class MongoDatabaseManager:
    def __init__(self, settings: MongoSettings) -> None:
        self._settings = settings
        self._client: AsyncMongoClient[dict[str, Any]] | None = None
        self._connected = False

    @property
    def settings(self) -> MongoSettings:
        return self._settings

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def client(self) -> AsyncMongoClient[dict[str, Any]]:
        if self._client is None or not self._connected:
            raise DatabaseConnectionError("MongoDB client is not connected. Call connect() first.")
        return self._client

    @property
    def database(self) -> AsyncDatabase[dict[str, Any]]:
        return self.client[self._settings.database]

    def get_collection(self, name: str) -> AsyncCollection[dict[str, Any]]:
        return self.database[name]

    async def connect(self) -> None:
        if self._connected and self._client is not None:
            return

        if self._client is None:
            self._client = AsyncMongoClient(
                self._settings.uri,
                maxPoolSize=self._settings.max_pool_size,
                minPoolSize=self._settings.min_pool_size,
                serverSelectionTimeoutMS=self._settings.server_selection_timeout_ms,
                connectTimeoutMS=self._settings.connect_timeout_ms,
                socketTimeoutMS=self._settings.socket_timeout_ms,
                appname=self._settings.app_name,
                tz_aware=True,
            )

        try:
            await self._client.admin.command("ping")
            self._connected = True
            logger.info("Connected to MongoDB database '%s'.", self._settings.database)
        except (ConnectionFailure, ServerSelectionTimeoutError, OperationFailure, PyMongoError) as exc:
            await self.close()
            raise DatabaseConnectionError(
                f"Unable to connect to MongoDB database '{self._settings.database}': {exc}"
            ) from exc

    async def ping(self) -> dict[str, Any]:
        if self._client is None or not self._connected:
            return {"status": "disconnected", "database": self._settings.database}
        try:
            result = await self._client.admin.command("ping")
            ok = bool(result.get("ok") == 1 or result.get("ok") == 1.0)
            return {
                "status": "healthy" if ok else "degraded",
                "database": self._settings.database,
            }
        except PyMongoError as exc:
            return {
                "status": "unhealthy",
                "database": self._settings.database,
                "error": str(exc),
            }

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                logger.warning("Error while closing MongoDB client: %s", exc)
            finally:
                self._client = None
                self._connected = False


def get_index_specifications() -> dict[str, list[IndexModel]]:
    return {
        COLL_USERS: [
            IndexModel([("user_id", ASCENDING)], unique=True, name="uq_users_user_id"),
            IndexModel([("email", ASCENDING)], unique=True, name="uq_users_email"),
        ],
        COLL_STORES: [
            IndexModel([("store_id", ASCENDING)], unique=True, name="uq_stores_store_id"),
            IndexModel([("store_id", ASCENDING), ("slug", ASCENDING)], unique=True, name="uq_stores_store_slug"),
            IndexModel([("store_id", ASCENDING), ("owner_user_id", ASCENDING)], name="ix_stores_owner_user"),
        ],
        COLL_STORE_MEMBERSHIPS: [
            IndexModel(
                [("store_id", ASCENDING), ("membership_id", ASCENDING)],
                unique=True,
                name="uq_memberships_store_membership_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="uq_memberships_store_user",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("status", ASCENDING), ("role", ASCENDING)],
                name="ix_memberships_store_status_role",
            ),
        ],
        COLL_PRODUCTS: [
            IndexModel(
                [("store_id", ASCENDING), ("product_id", ASCENDING)],
                unique=True,
                name="uq_products_store_product_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("sku_code", ASCENDING)],
                unique=True,
                name="uq_products_store_sku_code",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("is_active", ASCENDING), ("category", ASCENDING)],
                name="ix_products_store_active_category",
            ),
        ],
        COLL_PRODUCT_VARIANTS: [
            IndexModel(
                [("store_id", ASCENDING), ("variant_id", ASCENDING)],
                unique=True,
                name="uq_variants_store_variant_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("sku_code", ASCENDING)],
                unique=True,
                name="uq_variants_store_sku_code",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("product_id", ASCENDING), ("is_active", ASCENDING)],
                name="ix_variants_store_product_active",
            ),
        ],
        COLL_INVENTORY: [
            IndexModel(
                [("store_id", ASCENDING), ("inventory_id", ASCENDING)],
                unique=True,
                name="uq_inventory_store_inventory_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("inventory_scope_key", ASCENDING)],
                unique=True,
                name="uq_inventory_store_scope_key",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("product_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"variant_id": None},
                name="uq_inventory_store_base_product",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("product_id", ASCENDING), ("variant_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"variant_id": {"$type": "string"}},
                name="uq_inventory_store_product_variant",
            ),
        ],
        COLL_FAQS: [
            IndexModel(
                [("store_id", ASCENDING), ("faq_id", ASCENDING)],
                unique=True,
                name="uq_faqs_store_faq_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("is_active", ASCENDING), ("topic", ASCENDING)],
                name="ix_faqs_store_active_topic",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("related_product_ids", ASCENDING)],
                name="ix_faqs_store_related_products",
            ),
        ],
        COLL_SALES_POLICIES: [
            IndexModel(
                [("store_id", ASCENDING), ("policy_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uq_policies_store_policy_version",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("topic", ASCENDING), ("is_active", ASCENDING), ("version", DESCENDING)],
                name="ix_policies_store_topic_active_version",
            ),
        ],
        COLL_CUSTOMERS: [
            IndexModel(
                [("store_id", ASCENDING), ("customer_id", ASCENDING)],
                unique=True,
                name="uq_customers_store_customer_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("platform", ASCENDING), ("platform_user_handle", ASCENDING)],
                unique=True,
                name="uq_customers_store_platform_handle",
            ),
        ],
        COLL_LIVESTREAM_SESSIONS: [
            IndexModel(
                [("store_id", ASCENDING), ("session_id", ASCENDING)],
                unique=True,
                name="uq_sessions_store_session_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("status", ASCENDING), ("started_at", DESCENDING)],
                name="ix_sessions_store_status_started",
            ),
        ],
        COLL_LIVESTREAM_COMMENTS: [
            IndexModel(
                [("store_id", ASCENDING), ("comment_id", ASCENDING)],
                unique=True,
                name="uq_comments_store_comment_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("session_id", ASCENDING), ("timestamp", DESCENDING)],
                name="ix_comments_store_session_timestamp",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("session_id", ASCENDING), ("lead_label", ASCENDING)],
                name="ix_comments_store_session_lead",
            ),
        ],
        COLL_ANSWER_DRAFTS: [
            IndexModel(
                [("store_id", ASCENDING), ("draft_id", ASCENDING)],
                unique=True,
                name="uq_drafts_store_draft_id",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("session_id", ASCENDING), ("comment_id", ASCENDING)],
                name="ix_drafts_store_session_comment",
            ),
            IndexModel(
                [("store_id", ASCENDING), ("session_id", ASCENDING), ("seller_action", ASCENDING), ("created_at", DESCENDING)],
                name="ix_drafts_store_session_action_created",
            ),
        ],
    }


async def ensure_indexes(database: AsyncDatabase[dict[str, Any]]) -> dict[str, list[str]]:
    specs = get_index_specifications()
    created: dict[str, list[str]] = {}
    for collection_name, index_models in specs.items():
        if not index_models:
            continue
        collection = database[collection_name]
        names = await collection.create_indexes(index_models)
        created[collection_name] = list(names)
    return created


def _non_empty_string_schema() -> dict[str, Any]:
    return {"bsonType": "string", "minLength": 1}


def get_collection_validators() -> dict[str, dict[str, Any]]:
    return {
        COLL_USERS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["user_id", "email", "display_name", "status", "created_at", "updated_at"],
                "properties": {
                    "user_id": _non_empty_string_schema(),
                    "email": _non_empty_string_schema(),
                    "display_name": _non_empty_string_schema(),
                    "status": {"enum": ["active", "suspended", "deactivated"]},
                },
            }
        },
        COLL_STORES: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "slug", "name", "owner_user_id", "status", "created_at", "updated_at"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "slug": _non_empty_string_schema(),
                    "name": _non_empty_string_schema(),
                    "owner_user_id": _non_empty_string_schema(),
                    "status": {"enum": ["active", "suspended", "archived"]},
                },
            }
        },
        COLL_STORE_MEMBERSHIPS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "membership_id", "user_id", "role", "status", "created_at", "updated_at"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "membership_id": _non_empty_string_schema(),
                    "user_id": _non_empty_string_schema(),
                    "role": {"enum": ["owner", "admin", "operator", "viewer"]},
                    "status": {"enum": ["active", "invited", "revoked"]},
                },
            }
        },
        COLL_PRODUCTS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "product_id", "sku_code", "name", "category", "price", "currency", "is_active"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "product_id": _non_empty_string_schema(),
                    "sku_code": _non_empty_string_schema(),
                    "name": _non_empty_string_schema(),
                    "price": {"bsonType": ["double", "int", "long"], "minimum": 0},
                    "is_active": {"bsonType": "bool"},
                },
            }
        },
        COLL_PRODUCT_VARIANTS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "variant_id", "product_id", "sku_code", "title", "price", "is_active"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "variant_id": _non_empty_string_schema(),
                    "product_id": _non_empty_string_schema(),
                    "sku_code": _non_empty_string_schema(),
                    "price": {"bsonType": ["double", "int", "long"], "minimum": 0},
                    "is_active": {"bsonType": "bool"},
                },
            }
        },
        COLL_INVENTORY: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": [
                    "store_id",
                    "inventory_id",
                    "product_id",
                    "inventory_scope_key",
                    "quantity_on_hand",
                    "quantity_reserved",
                    "in_stock",
                    "version",
                ],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "inventory_id": _non_empty_string_schema(),
                    "product_id": _non_empty_string_schema(),
                    "variant_id": {"bsonType": ["string", "null"]},
                    "inventory_scope_key": _non_empty_string_schema(),
                    "quantity_on_hand": {"bsonType": ["int", "long"], "minimum": 0},
                    "quantity_reserved": {"bsonType": ["int", "long"], "minimum": 0},
                    "in_stock": {"bsonType": "bool"},
                    "version": {"bsonType": ["int", "long"], "minimum": 1},
                },
            }
        },
        COLL_FAQS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "faq_id", "question", "answer", "is_active"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "faq_id": _non_empty_string_schema(),
                    "question": _non_empty_string_schema(),
                    "answer": _non_empty_string_schema(),
                    "is_active": {"bsonType": "bool"},
                },
            }
        },
        COLL_SALES_POLICIES: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "policy_id", "version", "topic", "title", "summary", "details", "is_active"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "policy_id": _non_empty_string_schema(),
                    "version": {"bsonType": ["int", "long"], "minimum": 1},
                    "topic": _non_empty_string_schema(),
                    "is_active": {"bsonType": "bool"},
                },
            }
        },
        COLL_CUSTOMERS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "customer_id", "platform", "platform_user_handle"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "customer_id": _non_empty_string_schema(),
                    "platform": _non_empty_string_schema(),
                    "platform_user_handle": _non_empty_string_schema(),
                },
            }
        },
        COLL_LIVESTREAM_SESSIONS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "session_id", "title", "platform", "status"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "session_id": _non_empty_string_schema(),
                    "title": _non_empty_string_schema(),
                    "platform": _non_empty_string_schema(),
                    "status": {"enum": ["scheduled", "live", "paused", "ended"]},
                },
            }
        },
        COLL_LIVESTREAM_COMMENTS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "session_id", "comment_id", "platform", "user_handle", "message", "timestamp"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "session_id": _non_empty_string_schema(),
                    "comment_id": _non_empty_string_schema(),
                    "user_handle": _non_empty_string_schema(),
                    "message": _non_empty_string_schema(),
                },
            }
        },
        COLL_ANSWER_DRAFTS: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["store_id", "session_id", "draft_id", "comment_id", "suggested_reply", "seller_action"],
                "properties": {
                    "store_id": _non_empty_string_schema(),
                    "session_id": _non_empty_string_schema(),
                    "draft_id": _non_empty_string_schema(),
                    "comment_id": _non_empty_string_schema(),
                    "seller_action": {"enum": ["pending", "approved", "edited", "dismissed"]},
                },
            }
        },
    }


async def ensure_collections_and_validators(database: AsyncDatabase[dict[str, Any]]) -> None:
    existing = set(await database.list_collection_names())
    validators = get_collection_validators()

    for collection_name in ALL_COLLECTIONS:
        validator = validators.get(collection_name)
        if collection_name not in existing:
            try:
                if validator:
                    await database.create_collection(
                        collection_name,
                        validator=validator,
                        validationLevel="strict",
                        validationAction="error",
                    )
                else:
                    await database.create_collection(collection_name)
            except (CollectionInvalid, OperationFailure):
                pass
        elif validator:
            try:
                await database.command(
                    {
                        "collMod": collection_name,
                        "validator": validator,
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
            except OperationFailure as exc:
                logger.debug("Skipping collMod on collection '%s': %s", collection_name, exc)


async def initialize_database(database: AsyncDatabase[dict[str, Any]]) -> dict[str, list[str]]:
    try:
        await ensure_collections_and_validators(database)
        return await ensure_indexes(database)
    except PyMongoError as exc:
        raise DatabaseOperationError(f"Failed to initialize MongoDB schemas and indexes: {exc}") from exc


@asynccontextmanager
async def database_lifespan(
    mongo_manager: MongoDatabaseManager,
    redis_manager: Any | None = None,
    *,
    auto_initialize_indexes: bool = True,
) -> AsyncIterator[None]:
    await mongo_manager.connect()
    if auto_initialize_indexes:
        await initialize_database(mongo_manager.database)
    if redis_manager is not None:
        await redis_manager.connect()
    try:
        yield
    finally:
        if redis_manager is not None:
            await redis_manager.close()
        await mongo_manager.close()
