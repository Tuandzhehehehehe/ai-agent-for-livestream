"""Idempotent database seeder for demo products, inventory, FAQs, policies, customers, and orders."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ai.data import get_effective_policies, load_synthetic_data
from backend.database.contracts import StoreDatabaseFacade
from backend.database.models import (
    CustomerDocument,
    FAQDocument,
    InventoryDocument,
    LivestreamSessionDocument,
    OrderDocument,
    OrderItemSnapshot,
    ProductDocument,
    ProductVariantDocument,
    SalesPolicyDocument,
    StoreDocument,
    StoreMembershipDocument,
    TenantContext,
    UserDocument,
    utc_now,
)
from backend.database.mongodb import (
    DuplicateDocumentError,
    MongoDatabaseManager,
    initialize_database,
)
from backend.database.redis import DatabaseSettings

logger = logging.getLogger(__name__)

ORDERS_JSON_PATH = (
    Path(__file__).resolve().parents[2] / "ai" / "data" / "synthetic" / "orders.json"
)


def load_demo_orders(store_id: str = "default-store", session_id: str = "youtube-live") -> list[OrderDocument]:
    if not ORDERS_JSON_PATH.is_file():
        return []
    raw = json.loads(ORDERS_JSON_PATH.read_text(encoding="utf-8"))
    records = raw.get("records", []) if isinstance(raw, dict) else []
    orders: list[OrderDocument] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        orders.append(
            OrderDocument(
                store_id=store_id,
                order_id=str(item["order_id"]),
                session_id=session_id,
                customer_id=item.get("customer_id"),
                customer_handle=str(item.get("customer_handle", "@viewer")),
                customer_name=str(item.get("customer_name", "Livestream Buyer")),
                platform="youtube",
                status=item.get("status", "confirmed"),
                payment_status=item.get("payment_status", "paid"),
                items=[
                    OrderItemSnapshot(
                        product_id=str(line["product_id"]),
                        sku_code=str(line.get("sku_code") or f"SKU-{line['product_id']}"),
                        product_name=str(line["product_name"]),
                        quantity=int(line.get("quantity", 1)),
                        unit_price=float(line["unit_price"]),
                        line_total=float(line["line_total"]),
                    )
                    for line in item.get("items", [])
                ],
                subtotal=float(item.get("subtotal", 0.0)),
                shipping_fee=float(item.get("shipping_fee", 0.0)),
                discount_amount=float(item.get("discount_amount", 0.0)),
                total_amount=float(item.get("total_amount", 0.0)),
                currency=str(item.get("currency", "USD")),
                shipping_address=str(item.get("shipping_address", "")),
                tracking_code=item.get("tracking_code"),
                notes=str(item.get("notes", "")),
            )
        )
    return orders


async def seed_demo_store_data(
    facade: StoreDatabaseFacade,
    tenant_ctx: TenantContext,
    session_id: str = "youtube-live",
    *,
    reset_existing: bool = False,
) -> dict[str, int]:
    """Seed users, store, products, inventory, FAQs, policies, customers, session, and orders."""
    store_id = tenant_ctx.store_id
    dataset = load_synthetic_data()
    counts = {
        "users": 0,
        "stores": 0,
        "store_memberships": 0,
        "products": 0,
        "product_variants": 0,
        "inventory": 0,
        "faqs": 0,
        "policies": 0,
        "customers": 0,
        "livestream_sessions": 0,
        "orders": 0,
    }

    # 1. Owner User, Store & Membership
    owner_id = "user-owner-001"
    if await facade.users.get_by_user_id(owner_id) is None:
        try:
            await facade.users.create(
                UserDocument(
                    user_id=owner_id,
                    email="owner@livestream-demo.local",
                    display_name="Livestream Store Owner",
                    status="active",
                )
            )
        except DuplicateDocumentError:
            pass
    counts["users"] += 1

    if await facade.stores.get_by_store_id(store_id) is None:
        try:
            await facade.stores.create(
                StoreDocument(
                    store_id=store_id,
                    slug=store_id.lower().replace("_", "-"),
                    name="Livestream AI Demo Store",
                    owner_user_id=owner_id,
                    status="active",
                    default_currency="USD",
                    timezone="Asia/Ho_Chi_Minh",
                    platform_handles={"youtube": "@LivestreamAIDemoStore"},
                    settings={"auto_draft_enabled": True, "auto_order_enabled": True},
                )
            )
        except DuplicateDocumentError:
            pass
    counts["stores"] += 1

    if await facade.memberships.get_membership(store_id, owner_id) is None:
        try:
            await facade.memberships.add_member(
                StoreMembershipDocument(
                    store_id=store_id,
                    membership_id=f"mem-{store_id}-{owner_id}",
                    user_id=owner_id,
                    role="owner",
                    status="active",
                    invited_by_user_id=owner_id,
                )
            )
        except DuplicateDocumentError:
            pass
    counts["store_memberships"] += 1

    # 2. Products, Product Variants & Inventory
    seeded_product_docs: list[ProductDocument] = []
    for product in dataset.products:
        sku = f"SKU-{product.id.replace('SYN-', '')}"
        has_color_variant = "color" in product.attributes
        prod_doc = ProductDocument(
            store_id=store_id,
            product_id=product.id,
            sku_code=sku,
            name=product.name,
            category=product.category,
            subcategory=f"{product.category}-livestream",
            price=product.price,
            currency="USD",
            short_description=product.description,
            key_benefits=[f"Featured in {product.category} livestream flash sale"],
            common_questions=[f"What is the price of {product.name}?"],
            attributes=dict(product.attributes),
            has_variants=has_color_variant,
            is_active=True,
        )
        existing_prod = await facade.products.get_by_id(tenant_ctx, product.id)
        if existing_prod is None:
            await facade.products.create_in_tenant(tenant_ctx, prod_doc)
        seeded_product_docs.append(prod_doc)
        counts["products"] += 1

        existing_inv = await facade.products.get_inventory(
            tenant_ctx, product_id=product.id
        )
        if reset_existing or existing_inv is None:
            await facade.products.upsert_inventory(
                tenant_ctx,
                InventoryDocument(
                    store_id=store_id,
                    inventory_id=f"INV-{product.id}",
                    product_id=product.id,
                    quantity_on_hand=product.stock_quantity,
                    quantity_reserved=0,
                    in_stock=product.stock_quantity > 0,
                    stock_note="Seeded from synthetic catalog",
                ),
            )
        counts["inventory"] += 1

        if has_color_variant:
            color_val = str(product.attributes["color"])
            variant_id = f"VAR-{product.id}-01"
            if await facade.products.get_variant_by_id(tenant_ctx, variant_id) is None:
                try:
                    await facade.products.create_variant(
                        tenant_ctx,
                        ProductVariantDocument(
                            store_id=store_id,
                            variant_id=variant_id,
                            product_id=product.id,
                            sku_code=f"{sku}-V1",
                            title=f"{product.name} ({color_val.title()})",
                            price=product.price,
                            currency="USD",
                            options={"color": color_val},
                            attributes=dict(product.attributes),
                            is_active=True,
                        ),
                    )
                except DuplicateDocumentError:
                    pass
            counts["product_variants"] += 1

    # 3. FAQs
    for faq in dataset.faqs:
        if await facade.faqs.get_by_id(tenant_ctx, faq.id) is None:
            await facade.faqs.create_in_tenant(
                tenant_ctx,
                FAQDocument(
                    store_id=store_id,
                    faq_id=faq.id,
                    question=faq.question,
                    answer=faq.answer,
                    topic=faq.category,
                    related_product_ids=list(faq.product_ids),
                    keywords=[faq.category, *list(faq.product_ids)],
                    is_active=True,
                ),
            )
        counts["faqs"] += 1

    # 4. Sales Policies
    topic_map = {
        "shipping": "shipping",
        "returns": "returns",
        "promotions": "promotions",
        "warranty": "warranty",
    }
    for policy in get_effective_policies(dataset.policies):
        topic = topic_map.get(policy.category.casefold(), "general")
        if await facade.sales_policies.get_policy_version(tenant_ctx, policy.id, 1) is None:
            await facade.sales_policies.create_policy_version(
                tenant_ctx,
                SalesPolicyDocument(
                    store_id=store_id,
                    policy_id=policy.id,
                    version=1,
                    topic=topic,  # type: ignore[arg-type]
                    title=f"{policy.category.title()} Policy",
                    summary=policy.content[:240],
                    details=policy.content,
                    conditions=[f"Applies to active {policy.category.lower()} requests"],
                    is_active=True,
                ),
                supersede_previous_topic_versions=False,
            )
        counts["policies"] += 1

    # 5. Active Livestream Session
    session_doc = await facade.sessions.get_by_id(tenant_ctx, session_id)
    if session_doc is None:
        session_doc = await facade.sessions.create_session(
            tenant_ctx,
            LivestreamSessionDocument(
                store_id=store_id,
                session_id=session_id,
                title="YouTube Live Flash Sale Demo",
                platform="youtube",
                status="live",
                host_user_id=owner_id,
                pinned_product_ids=[p.id for p in dataset.products[:4]],
                active_promotion_notes="Free shipping on livestream creator bundles.",
                started_at=utc_now(),
            ),
        )
    counts["livestream_sessions"] += 1

    # 6. Customers & Orders
    orders = load_demo_orders(store_id=store_id, session_id=session_id)
    for order in orders:
        if order.customer_id:
            await facade.customers.upsert_customer_profile(
                tenant_ctx,
                CustomerDocument(
                    store_id=store_id,
                    customer_id=order.customer_id,
                    platform="youtube",
                    platform_user_handle=order.customer_handle,
                    display_name=order.customer_name,
                    tags=["livestream_buyer"],
                    notes=f"Seeded buyer for order {order.order_id}",
                ),
            )
            counts["customers"] += 1
        if reset_existing or await facade.orders.get_by_id(tenant_ctx, order.order_id) is None:
            await facade.orders.upsert_order(tenant_ctx, order)
        counts["orders"] += 1

    # 7. Populate Redis Data Structures (Section 3.2.4.2) when Redis is attached
    if facade.cache is not None and session_doc is not None:
        try:
            await facade.cache.set_session_state(
                tenant_ctx,
                session_id,
                {
                    "status": session_doc.status,
                    "title": session_doc.title,
                    "platform": session_doc.platform,
                    "host_user_id": session_doc.host_user_id or owner_id,
                    "pinned_product_ids": session_doc.pinned_product_ids,
                },
            )
            await facade.cache.cache_session(tenant_ctx, session_doc)
            for prod_doc in seeded_product_docs:
                await facade.cache.cache_product(tenant_ctx, prod_doc)
            await facade.cache.publish_dashboard_notification(
                tenant_ctx,
                session_id,
                {"event": "session.seeded", "products": counts["products"]},
            )
        except Exception as exc:
            logger.debug("Redis cache seed skipped: %s", exc)

    if facade.streams is not None:
        try:
            await facade.streams.publish_event(
                tenant_ctx,
                session_id,
                event_id=f"seed-{session_id}",
                event_type="session.started",
                payload={"session_id": session_id, "status": "live"},
                deduplicate=True,
            )
        except Exception as exc:
            logger.debug("Redis stream seed skipped: %s", exc)

    logger.info("Seeded demo store '%s': %s", store_id, counts)
    return counts


async def _run_cli_seed() -> None:
    settings = DatabaseSettings.from_env()
    mongo = MongoDatabaseManager(settings.mongo)
    await mongo.connect()
    try:
        await initialize_database(mongo.database)
        facade = StoreDatabaseFacade(mongo, None)
        ctx = TenantContext.system_context("default-store")
        counts = await seed_demo_store_data(facade, ctx, session_id="youtube-live")
        print("Database seeded successfully:", json.dumps(counts, indent=2))
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(_run_cli_seed())
