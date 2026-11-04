from __future__ import annotations

import asyncio
from typing import Any

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from backend.database.models import (
    FAQDocument,
    InventoryDocument,
    PolicyTopic,
    ProductDocument,
    ProductVariantDocument,
    SalesPolicyDocument,
    TenantContext,
    build_inventory_scope_key,
    utc_now,
)
from backend.database.mongodb import (
    COLL_FAQS,
    COLL_INVENTORY,
    COLL_PRODUCT_VARIANTS,
    COLL_PRODUCTS,
    COLL_SALES_POLICIES,
    DatabaseOperationError,
    DuplicateDocumentError,
)
from backend.repositories.base import BaseTenantRepository


class _VariantSubRepository(BaseTenantRepository[ProductVariantDocument]):
    collection_name = COLL_PRODUCT_VARIANTS
    model_cls = ProductVariantDocument
    id_field = "variant_id"


class _InventorySubRepository(BaseTenantRepository[InventoryDocument]):
    collection_name = COLL_INVENTORY
    model_cls = InventoryDocument
    id_field = "inventory_id"


class ProductRepository(BaseTenantRepository[ProductDocument]):
    collection_name = COLL_PRODUCTS
    model_cls = ProductDocument
    id_field = "product_id"

    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        super().__init__(database)
        self._variants = _VariantSubRepository(database)
        self._inventory = _InventorySubRepository(database)

    @property
    def variants_collection(self) -> AsyncCollection[dict[str, Any]]:
        return self._variants.collection

    @property
    def inventory_collection(self) -> AsyncCollection[dict[str, Any]]:
        return self._inventory.collection

    async def get_by_sku(self, ctx: TenantContext, sku_code: str) -> ProductDocument | None:
        return await self._find_one(self._scoped_filter(ctx, {"sku_code": sku_code}))

    async def list_active_products(
        self,
        ctx: TenantContext,
        *,
        category: str | None = None,
        limit: int = 200,
        skip: int = 0,
    ) -> list[ProductDocument]:
        extra: dict[str, Any] = {"is_active": True}
        if category:
            extra["category"] = category
        return await self.list_in_tenant(
            ctx,
            extra,
            sort=[("name", 1)],
            limit=limit,
            skip=skip,
        )

    async def delete_product_cascade(self, ctx: TenantContext, product_id: str) -> bool:
        scoped_filter = self._scoped_filter(ctx, {"product_id": product_id}, require_write=True)
        try:
            _, _, prod_res = await asyncio.gather(
                self._variants.collection.delete_many(scoped_filter),
                self._inventory.collection.delete_many(scoped_filter),
                self._collection.delete_one(scoped_filter),
            )
            return bool(prod_res.deleted_count and prod_res.deleted_count > 0)
        except PyMongoError as exc:
            raise DatabaseOperationError(f"Failed to cascade-delete product {product_id!r}: {exc}") from exc

    async def create_variant(
        self,
        ctx: TenantContext,
        variant: ProductVariantDocument,
    ) -> ProductVariantDocument:
        self._variants._assert_document_tenant(ctx, variant)
        await self._assert_scoped_ref_exists(
            ctx,
            COLL_PRODUCTS,
            "product_id",
            variant.product_id,
            label="Product",
        )
        return await self._variants.create_in_tenant(ctx, variant)

    async def get_variant_by_id(
        self,
        ctx: TenantContext,
        variant_id: str,
    ) -> ProductVariantDocument | None:
        return await self._variants.get_by_id(ctx, variant_id)

    async def list_variants_for_product(
        self,
        ctx: TenantContext,
        product_id: str,
        *,
        only_active: bool = False,
    ) -> list[ProductVariantDocument]:
        extra: dict[str, Any] = {"product_id": product_id}
        if only_active:
            extra["is_active"] = True
        return await self._variants.list_in_tenant(ctx, extra, sort=[("sku_code", 1)], limit=200)

    async def update_variant(
        self,
        ctx: TenantContext,
        variant_id: str,
        updates: dict[str, Any],
    ) -> ProductVariantDocument | None:
        protected = {"_id", "store_id", "variant_id", "product_id", "created_at"}
        sanitized = {k: v for k, v in updates.items() if k not in protected}
        return await self._variants.update_by_id(ctx, variant_id, sanitized)

    async def delete_variant(self, ctx: TenantContext, variant_id: str) -> bool:
        scoped_filter = self._scoped_filter(ctx, {"variant_id": variant_id}, require_write=True)
        await self._inventory.collection.delete_many(scoped_filter)
        return await self._variants.delete_by_id(ctx, variant_id)

    async def upsert_inventory(
        self,
        ctx: TenantContext,
        inventory: InventoryDocument,
    ) -> InventoryDocument:
        self._inventory._assert_document_tenant(ctx, inventory)
        await self._assert_scoped_ref_exists(
            ctx,
            COLL_PRODUCTS,
            "product_id",
            inventory.product_id,
            label="Product",
        )
        if inventory.variant_id is not None:
            await self._assert_exists(
                COLL_PRODUCT_VARIANTS,
                {
                    "store_id": ctx.store_id,
                    "product_id": inventory.product_id,
                    "variant_id": inventory.variant_id,
                },
                f"Variant {inventory.variant_id!r} for product {inventory.product_id!r} "
                f"does not exist in store {ctx.store_id!r}.",
            )

        existing = await self.get_inventory(
            ctx,
            product_id=inventory.product_id,
            variant_id=inventory.variant_id,
        )
        if existing is None:
            return await self._inventory.create_in_tenant(ctx, inventory)

        updated = await self._inventory.update_by_id(
            ctx,
            existing.inventory_id,
            {
                "quantity_on_hand": inventory.quantity_on_hand,
                "quantity_reserved": inventory.quantity_reserved,
                "in_stock": inventory.in_stock,
                "stock_note": inventory.stock_note,
            },
            extra_operators={"$inc": {"version": 1}},
        )
        assert updated is not None
        return updated

    async def get_inventory(
        self,
        ctx: TenantContext,
        *,
        product_id: str,
        variant_id: str | None = None,
    ) -> InventoryDocument | None:
        scope_key = build_inventory_scope_key(product_id, variant_id)
        return await self._inventory._find_one(
            self._scoped_filter(ctx, {"inventory_scope_key": scope_key})
        )

    async def list_inventory_for_store(
        self,
        ctx: TenantContext,
        *,
        product_id: str | None = None,
        limit: int = 500,
    ) -> list[InventoryDocument]:
        extra: dict[str, Any] = {}
        if product_id is not None:
            extra["product_id"] = product_id
        return await self._inventory.list_in_tenant(ctx, extra, limit=limit)

    async def adjust_inventory_quantity(
        self,
        ctx: TenantContext,
        *,
        product_id: str,
        variant_id: str | None = None,
        delta_on_hand: int = 0,
        delta_reserved: int = 0,
        expected_version: int | None = None,
    ) -> InventoryDocument | None:
        scope_key = build_inventory_scope_key(product_id, variant_id)
        query = self._scoped_filter(ctx, {"inventory_scope_key": scope_key}, require_write=True)
        if expected_version is not None:
            query["version"] = expected_version

        current = await self._inventory._find_one(query)
        if current is None:
            return None

        new_on_hand = max(0, current.quantity_on_hand + delta_on_hand)
        new_reserved = max(0, current.quantity_reserved + delta_reserved)
        in_stock = (new_on_hand - new_reserved) > 0

        try:
            raw = await self._inventory.collection.find_one_and_update(
                {
                    "store_id": ctx.store_id,
                    "inventory_id": current.inventory_id,
                    "version": current.version,
                },
                {
                    "$set": {
                        "quantity_on_hand": new_on_hand,
                        "quantity_reserved": new_reserved,
                        "in_stock": in_stock,
                        "updated_at": utc_now(),
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            return self._inventory._deserialize(raw)
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError("Inventory duplicate constraint violation.") from exc
        except PyMongoError as exc:
            raise DatabaseOperationError(f"Failed to adjust inventory: {exc}") from exc


class FAQRepository(BaseTenantRepository[FAQDocument]):
    collection_name = COLL_FAQS
    model_cls = FAQDocument
    id_field = "faq_id"

    async def create_in_tenant(self, ctx: TenantContext, document: FAQDocument) -> FAQDocument:
        self._assert_document_tenant(ctx, document)
        await self._assert_scoped_refs_exist(
            ctx,
            COLL_PRODUCTS,
            "product_id",
            document.related_product_ids,
            label="Related product",
        )
        return await self._insert(document)

    async def list_active_faqs(
        self,
        ctx: TenantContext,
        *,
        topic: str | None = None,
        product_id: str | None = None,
        limit: int = 200,
    ) -> list[FAQDocument]:
        extra: dict[str, Any] = {"is_active": True}
        if topic:
            extra["topic"] = topic
        if product_id:
            extra["related_product_ids"] = product_id
        return await self.list_in_tenant(ctx, extra, sort=[("faq_id", 1)], limit=limit)


class SalesPolicyRepository(BaseTenantRepository[SalesPolicyDocument]):
    collection_name = COLL_SALES_POLICIES
    model_cls = SalesPolicyDocument
    id_field = "policy_id"

    async def create_policy_version(
        self,
        ctx: TenantContext,
        policy: SalesPolicyDocument,
        *,
        supersede_previous_topic_versions: bool = True,
    ) -> SalesPolicyDocument:
        self._assert_document_tenant(ctx, policy)
        if supersede_previous_topic_versions and policy.is_active:
            try:
                await self._collection.update_many(
                    {
                        "store_id": ctx.store_id,
                        "topic": policy.topic,
                        "is_active": True,
                        "version": {"$ne": policy.version},
                    },
                    {
                        "$set": {
                            "is_active": False,
                            "effective_to": utc_now(),
                            "updated_at": utc_now(),
                        }
                    },
                )
            except PyMongoError as exc:
                raise DatabaseOperationError(
                    f"Failed to supersede previous policy versions for topic={policy.topic!r}: {exc}"
                ) from exc
        return await self._insert(policy)

    async def list_active_policies(
        self,
        ctx: TenantContext,
        *,
        topic: PolicyTopic | None = None,
        limit: int = 100,
    ) -> list[SalesPolicyDocument]:
        extra: dict[str, Any] = {"is_active": True}
        if topic is not None:
            extra["topic"] = topic
        return await self.list_in_tenant(
            ctx,
            extra,
            sort=[("topic", 1), ("version", -1)],
            limit=limit,
        )

    async def get_policy_version(
        self,
        ctx: TenantContext,
        policy_id: str,
        version: int,
    ) -> SalesPolicyDocument | None:
        return await self._find_one(
            self._scoped_filter(ctx, {"policy_id": policy_id, "version": version})
        )

