# Multi-Tenant Database & Redis Integration Guide

This document defines the MongoDB and Redis database infrastructure, schemas, repository interfaces, failure-handling semantics, and integration contracts for the **AI Agent for Livestream Sales** backend.

The database layer (`backend.database`, `backend.repositories`, and `backend.infrastructure`) is completely decoupled from the `ai` domain package. It does not import or execute AI intent classification, retrieval, or answer generation.

---

## 1. Consolidated Package Layout (13 Files)

```text
backend/
  database/
    __init__.py      # Public exports for settings, managers, models, errors, and facade
    mongodb.py       # MongoSettings, DatabaseError hierarchy, MongoDatabaseManager,
                     # collection constants, $jsonSchema validators, compound indexes,
                     # initialize_database(), and database_lifespan()
    redis.py         # RedisSettings, DatabaseSettings, RedisManager, Redis errors
    models.py        # TenantContext + 12 Pydantic v2 collection document models
    contracts.py     # StoreDatabaseFacade and CommentPersistenceResult
  repositories/
    __init__.py      # Public repository exports
    base.py          # BaseRepository, BaseTenantRepository, compact_updates()
    tenant.py        # UserRepository, StoreRepository, MembershipRepository, CustomerRepository
    catalog.py       # ProductRepository (products, variants, inventory), FAQRepository, SalesPolicyRepository
    livestream.py    # LivestreamRepository, CommentRepository, AnswerRepository
  infrastructure/
    __init__.py      # Public Redis operational infrastructure exports
    cache.py         # RedisKeys, TTL constants, TenantOperationalCache, execute_redis(), execute_pipeline()
    streams.py       # RedisEventStream and StreamEventRecord
```

---

## 2. Storage Responsibilities & Architecture

| Layer | Technology | Responsibility |
| :--- | :--- | :--- |
| **Source of Truth** | MongoDB 7.0 (`pymongo.AsyncMongoClient`) | Authoritative persistence for users, stores, memberships, products, variants, inventory, FAQs, versioned sales policies, customers, livestream sessions, livestream comments, and answer drafts. |
| **Bounded Recent-Comment Cache** | Redis `LIST` (`redis.asyncio`) | Fast bounded window (`LPUSH` + `LTRIM`, default 100 items) of recent session comments at `store:{store_id}:session:{session_id}:comments`. Rebuilt from MongoDB via `recover_session_comment_cache_from_mongo()`. |
| **Event Ingestion Infrastructure** | Redis `STREAM` (`redis.asyncio`) | Ordered, consumer-group-capable event log (`XADD` with `MAXLEN ~ 5000`) at `store:{store_id}:session:{session_id}:events` for comment and operational events. |
| **Session State & Entity Cache** | Redis `HASH` / `STRING` / `PUBSUB` | Active livestream session state, deduplication keys (`SET NX EX`), product/session caches, and dashboard Pub/Sub notifications. |

---

## 3. Authentication vs. Tenant Authorization

- **Authentication is external** to the database layer. [`UserDocument`](../backend/database/models.py) stores identity metadata (`user_id`, `email`, `display_name`, `status`) and does not store passwords or issue tokens.
- **Tenant Authorization** is enforced via [`TenantContext`](../backend/database/models.py):
  - Never trust a client-supplied `store_id` directly.
  - Obtain a verified `TenantContext` by calling [`StoreDatabaseFacade.authorize_tenant(store_id=..., user_id=..., allowed_roles=...)`](../backend/database/contracts.py) or [`MembershipRepository.verify_tenant_access(...)`](../backend/repositories/tenant.py), which concurrently verifies that the store is `"active"` and the user has an `"active"` membership in that store.
  - For trusted internal background tasks, use `TenantContext.system_context(store_id)`.
- **Role-Based Write Protection:**
  - `WRITE_CAPABLE_ROLES = {"owner", "admin", "operator", "system"}`.
  - Users with the `"viewer"` role are strictly read-only. Every write method in [`BaseTenantRepository`](../backend/repositories/base.py), [`TenantOperationalCache`](../backend/infrastructure/cache.py), and [`RedisEventStream`](../backend/infrastructure/streams.py) invokes `ctx.require_write_permission()` and raises `TenantAuthorizationError` if called by a viewer.
- **NoSQL Injection Prevention:**
  - All identifier fields use `SafeIdStr` (`pattern=r"^[A-Za-z0-9._@-]+$"`), blocking `$` operator injection, whitespace, and colon delimiter injection at the model validation boundary.

---

## 4. MongoDB Collections, Schemas & Indexes

All 12 collections are initialized with strict `$jsonSchema` validators and compound indexes via [`initialize_database()`](../backend/database/mongodb.py). Every index on a tenant-owned collection is prefixed with `store_id`.

| Collection | Document Model | Unique Indexes | Supporting Indexes |
| :--- | :--- | :--- | :--- |
| `users` | `UserDocument` | `(user_id)`, `(email)` | — |
| `stores` | `StoreDocument` | `(store_id)`, `(store_id, slug)` | `(store_id, owner_user_id)` |
| `store_memberships` | `StoreMembershipDocument` | `(store_id, membership_id)`, `(store_id, user_id)` | `(store_id, status, role)` |
| `products` | `ProductDocument` | `(store_id, product_id)`, `(store_id, sku_code)` | `(store_id, is_active, category)` |
| `product_variants` | `ProductVariantDocument` | `(store_id, variant_id)`, `(store_id, sku_code)` | `(store_id, product_id, is_active)` |
| `inventory` | `InventoryDocument` | `(store_id, inventory_id)`, `(store_id, inventory_scope_key)`, partial `(store_id, product_id)` where `variant_id == null`, partial `(store_id, product_id, variant_id)` where `variant_id` is string | — |
| `faqs` | `FAQDocument` | `(store_id, faq_id)` | `(store_id, is_active, topic)`, `(store_id, related_product_ids)` |
| `sales_policies` | `SalesPolicyDocument` | `(store_id, policy_id, version)` | `(store_id, topic, is_active, version DESC)` |
| `customers` | `CustomerDocument` | `(store_id, customer_id)`, `(store_id, platform, platform_user_handle)` | — |
| `livestream_sessions` | `LivestreamSessionDocument` | `(store_id, session_id)` | `(store_id, status, started_at DESC)` |
| `livestream_comments` | `LivestreamCommentDocument` | `(store_id, comment_id)` | `(store_id, session_id, timestamp DESC)`, `(store_id, session_id, lead_label)` |
| `answer_drafts` | `AnswerDraftDocument` | `(store_id, draft_id)` | `(store_id, session_id, comment_id)`, `(store_id, session_id, seller_action, created_at DESC)` |

### Inventory Uniqueness Strategy
Products may manage inventory at the base-product level (`variant_id=None`) or per variant (`variant_id="VAR-..."`).
1. [`InventoryDocument`](../backend/database/models.py) automatically computes `inventory_scope_key` via [`build_inventory_scope_key()`](../backend/database/models.py):
   - Base product (`variant_id=None`): `"product:{product_id}"`
   - Variant (`variant_id="VAR-01"`): `"variant:{product_id}:VAR-01"`
2. The `inventory` collection enforces uniqueness via `(store_id, inventory_scope_key)` plus two partial unique indexes (`uq_inventory_store_base_product` for `{"variant_id": None}` and `uq_inventory_store_product_variant` for `{"variant_id": {"$type": "string"}}`).

---

## 5. Redis Key Conventions, Pipelining & Expiration Policies

All Redis keys are constructed through [`RedisKeys`](../backend/infrastructure/cache.py), which validates safe identifier segments (`^[A-Za-z0-9._@-]+$`). Multi-step Redis operations execute in a single network round-trip using [`execute_pipeline()`](../backend/infrastructure/cache.py).

| Key Builder | Key Pattern | Redis Type | Default TTL | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `RedisKeys.session_state` | `store:{store_id}:session:{session_id}:state` | `HASH` | `24h` (`86400s`) | Active livestream session state (`HSET` + `EXPIRE`) |
| `RedisKeys.session_comments` | `store:{store_id}:session:{session_id}:comments` | `LIST` | `24h` (`86400s`) | Bounded recent-comment cache (`LPUSH` + `LTRIM` 100 items + `EXPIRE`) |
| `RedisKeys.session_events_stream` | `store:{store_id}:session:{session_id}:events` | `STREAM` | `24h` (`86400s`) | Comment/event ingestion stream (`XADD MAXLEN ~ 5000` + `EXPIRE`) |
| `RedisKeys.session_dedupe` | `store:{store_id}:session:{session_id}:dedupe:{id}` | `STRING` | `6h` (`21600s`) | Event and comment deduplication (`SET NX EX`) |
| `RedisKeys.product_cache` | `store:{store_id}:cache:product:{product_id}` | `STRING` (JSON) | `1h` (`3600s`) | Product read-through cache |
| `RedisKeys.session_cache` | `store:{store_id}:cache:session:{session_id}` | `STRING` (JSON) | `1h` (`3600s`) | Livestream session metadata cache |
| `RedisKeys.session_pubsub_channel` | `store:{store_id}:session:{session_id}:pubsub` | `PUBSUB` | Ephemeral | Real-time dashboard notifications |

---

## 6. Idempotency & Failure Strategy Across MongoDB and Redis

MongoDB and Redis do not share distributed atomic transactions. [`StoreDatabaseFacade.record_comment_with_operational_sync()`](../backend/database/contracts.py) implements the following failure and recovery rules:

1. **Authoritative Write First:** Writes to MongoDB first via [`CommentRepository.append_comment()`](../backend/repositories/livestream.py), which enforces the unique index `(store_id, comment_id)`. If a duplicate `comment_id` is received, it returns `(existing_doc, False)` idempotently without incrementing session metrics twice.
2. **Pipelined Redis Propagation:** Pushes newly inserted comments to the bounded Redis `LIST` cache (`LPUSH + LTRIM + EXPIRE`) and publishes a `"comment.ingested"` event to the Redis `STREAM` (`XADD + EXPIRE`) guarded by `SET NX EX` deduplication.
3. **Graceful Degradation & Cache Recovery:** If Redis is temporarily unreachable after the MongoDB write succeeds, `CommentPersistenceResult` returns `inserted_into_mongo=True`, `cached_in_redis=False`, and `redis_error="<reason>"`. Call [`StoreDatabaseFacade.recover_session_comment_cache_from_mongo(ctx, session_id)`](../backend/database/contracts.py) to rebuild the Redis recent-comment `LIST` from authoritative MongoDB history.

---

## 7. Domain Repositories & Facade Contracts

### Error Hierarchy (`backend.database`)
- `DatabaseError` (base exception)
  - `DatabaseConnectionError` / `RedisConnectionError`: MongoDB or Redis unreachable.
  - `DatabaseOperationError` / `RedisOperationError`: Query or command execution failure.
  - `DuplicateDocumentError`: Unique constraint violation.
  - `DocumentNotFoundError`: Target document not found.
  - `ReferentialIntegrityError`: Referenced store, user, product, variant, customer, session, or comment does not exist in the tenant scope.
  - `TenantIsolationError`: Cross-tenant `store_id` mismatch or invalid `TenantContext`.
  - `TenantAuthorizationError`: Unauthorized user/store membership or write attempt by a read-only `"viewer"`.

### Repository Modules (`backend.repositories`)
- [`tenant.py`](../backend/repositories/tenant.py):
  - `UserRepository`: `create`, `get_by_user_id`, `get_by_email`, `update_user`, `delete_user`
  - `StoreRepository`: `create`, `get_by_store_id`, `get_by_slug`, `list_by_owner`, `update_store`
  - `MembershipRepository`: `add_member`, `get_membership`, `list_members_for_store`, `list_stores_for_user`, `update_membership`, `verify_tenant_access`
  - `CustomerRepository`: `get_by_platform_identity`, `upsert_customer_profile`
- [`catalog.py`](../backend/repositories/catalog.py):
  - `ProductRepository`: `create_in_tenant`, `get_by_id`, `get_by_sku`, `list_active_products`, `delete_product_cascade`, `create_variant`, `get_variant_by_id`, `list_variants_for_product`, `update_variant`, `delete_variant`, `upsert_inventory`, `get_inventory`, `list_inventory_for_store`, `adjust_inventory_quantity`
  - `FAQRepository`: `create_in_tenant`, `list_active_faqs`
  - `SalesPolicyRepository`: `create_policy_version`, `list_active_policies`, `get_policy_version`
- [`livestream.py`](../backend/repositories/livestream.py):
  - `LivestreamRepository` (`LivestreamSessionRepository`): `create_session`, `list_sessions`, `transition_status`, `set_pinned_products`, `increment_metrics`
  - `CommentRepository` (`LivestreamCommentRepository`): `append_comment`, `list_recent_for_session`, `list_chronological_for_session`, `update_processing_metadata`
  - `AnswerRepository` (`AnswerDraftRepository`): `save_draft`, `get_latest_for_comment`, `list_for_session`, `record_seller_action`

---

## 8. AI Backend Integration & Model Mapping Guide

Because the database layer is decoupled from `ai.*`, the AI backend developer can map between database documents and `ai` dataclasses at the service boundary:

```python
from ai.customer_qa import CustomerAnswerDraft, CustomerQuestionRequest
from ai.data.models import FAQ, Product, SalesPolicy, SyntheticDataset
from backend.database import (
    AnswerDraftDocument,
    EvidenceSourceSnapshot,
    StoreDatabaseFacade,
    TenantContext,
)


async def load_tenant_ai_dataset(
    facade: StoreDatabaseFacade,
    ctx: TenantContext,
) -> SyntheticDataset:
    products = await facade.products.list_active_products(ctx)
    inventories = await facade.products.list_inventory_for_store(ctx)
    faqs = await facade.faqs.list_active_faqs(ctx)
    policies = await facade.sales_policies.list_active_policies(ctx)

    inv_by_product = {
        inv.product_id: inv
        for inv in inventories
        if inv.variant_id is None
    }

    return SyntheticDataset(
        products=tuple(
            Product(
                product_id=p.product_id,
                name=p.name,
                category=p.category,
                price=p.price,
                currency=p.currency,
                in_stock=inv_by_product[p.product_id].in_stock if p.product_id in inv_by_product else True,
                stock_note=inv_by_product[p.product_id].stock_note if p.product_id in inv_by_product else "",
                short_description=p.short_description,
                key_benefits=tuple(p.key_benefits),
                common_questions=tuple(p.common_questions),
                attributes=dict(p.attributes),
            )
            for p in products
        ),
        faqs=tuple(
            FAQ(
                faq_id=f.faq_id,
                question=f.question,
                answer=f.answer,
                topic=f.topic,
                related_product_ids=tuple(f.related_product_ids),
                keywords=tuple(f.keywords),
            )
            for f in faqs
        ),
        policies=tuple(
            SalesPolicy(
                policy_id=pol.policy_id,
                topic=pol.topic,
                title=pol.title,
                summary=pol.summary,
                details=pol.details,
                conditions=tuple(pol.conditions),
            )
            for pol in policies
        ),
    )


async def persist_ai_draft(
    facade: StoreDatabaseFacade,
    ctx: TenantContext,
    *,
    draft_id: str,
    request: CustomerQuestionRequest,
    ai_draft: CustomerAnswerDraft,
) -> AnswerDraftDocument:
    draft_doc = AnswerDraftDocument(
        store_id=ctx.store_id,
        session_id=request.session_id,
        draft_id=draft_id,
        comment_id=request.comment_id or "unlinked",
        suggested_reply=ai_draft.answer or "",
        confidence=1.0 if ai_draft.status == "answered" else 0.0,
        grounding_passed=not ai_draft.needs_human_review,
        fallback_used=ai_draft.status != "answered",
        sources=[
            EvidenceSourceSnapshot(
                source_type=src.source_type,
                source_id=src.source_id,
                title=src.title,
                snippet=src.snippet,
            )
            for src in ai_draft.sources
        ],
    )
    return await facade.answer_drafts.save_draft(ctx, draft_doc)
```

---

## 9. Docker Compose & Environment Setup

1. Copy [`.env.example`](../.env.example) to `.env` and set strong credentials for `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD`, and `REDIS_PASSWORD`.
2. Start MongoDB and Redis (bound to loopback `127.0.0.1` by default):
   ```bash
   docker compose up -d
   ```
3. Wire [`database_lifespan`](../backend/database/mongodb.py) into FastAPI startup/shutdown to connect both managers and initialize MongoDB validators and indexes automatically:
   ```python
   from contextlib import asynccontextmanager
   from fastapi import FastAPI
   from backend.database import (
       DatabaseSettings,
       MongoDatabaseManager,
       RedisManager,
       StoreDatabaseFacade,
       database_lifespan,
   )

   settings = DatabaseSettings.from_env()
   mongo_manager = MongoDatabaseManager(settings.mongo)
   redis_manager = RedisManager(settings.redis)

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       async with database_lifespan(mongo_manager, redis_manager):
           app.state.db = StoreDatabaseFacade(mongo_manager, redis_manager)
           yield
   ```
