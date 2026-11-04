from __future__ import annotations

import asyncio
from typing import Any

from backend.database.models import (
    CustomerDocument,
    MembershipRole,
    MembershipStatus,
    StoreDocument,
    StoreMembershipDocument,
    StoreStatus,
    TenantContext,
    UserDocument,
    UserStatus,
)
from backend.database.mongodb import (
    COLL_CUSTOMERS,
    COLL_STORE_MEMBERSHIPS,
    COLL_STORES,
    COLL_USERS,
    TenantAuthorizationError,
)
from backend.repositories.base import BaseRepository, BaseTenantRepository, compact_updates


class UserRepository(BaseRepository[UserDocument]):
    collection_name = COLL_USERS
    model_cls = UserDocument
    id_field = "user_id"

    async def create(self, user: UserDocument) -> UserDocument:
        return await self._insert(user)

    async def get_by_user_id(self, user_id: str) -> UserDocument | None:
        return await self._find_one({"user_id": user_id})

    async def get_by_email(self, email: str) -> UserDocument | None:
        return await self._find_one({"email": email.strip().lower()})

    async def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        status: UserStatus | None = None,
        updates: dict[str, Any] | None = None,
    ) -> UserDocument | None:
        payload = compact_updates(updates, display_name=display_name, status=status)
        for immutable_key in ("user_id", "_id", "created_at"):
            payload.pop(immutable_key, None)
        if "email" in payload and isinstance(payload["email"], str):
            payload["email"] = payload["email"].strip().lower()
        return await self._update_one({"user_id": user_id}, payload)

    async def delete_user(self, user_id: str) -> bool:
        return await self._delete_one({"user_id": user_id})


class StoreRepository(BaseRepository[StoreDocument]):
    collection_name = COLL_STORES
    model_cls = StoreDocument
    id_field = "store_id"

    async def create(self, store: StoreDocument) -> StoreDocument:
        await self._assert_exists(
            COLL_USERS,
            {"user_id": store.owner_user_id},
            f"Owner user {store.owner_user_id!r} does not exist in '{COLL_USERS}'.",
        )
        return await self._insert(store)

    async def get_by_store_id(self, store_id: str) -> StoreDocument | None:
        return await self._find_one({"store_id": store_id})

    async def get_by_slug(self, slug: str) -> StoreDocument | None:
        return await self._find_one({"slug": slug.strip().lower()})

    async def list_by_owner(self, owner_user_id: str, *, limit: int = 50) -> list[StoreDocument]:
        return await self._find_many(
            {"owner_user_id": owner_user_id},
            sort=[("created_at", -1)],
            limit=limit,
        )

    async def update_store(
        self,
        ctx: TenantContext,
        *,
        name: str | None = None,
        status: StoreStatus | None = None,
        default_currency: str | None = None,
        timezone: str | None = None,
        platform_handles: dict[str, str] | None = None,
        settings: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
    ) -> StoreDocument | None:
        ctx.require_write_permission()
        payload = compact_updates(
            updates,
            name=name,
            status=status,
            default_currency=default_currency,
            timezone=timezone,
            platform_handles=platform_handles,
            settings=settings,
        )
        for immutable_key in ("store_id", "owner_user_id", "_id", "created_at"):
            payload.pop(immutable_key, None)
        return await self._update_one({"store_id": ctx.store_id}, payload)


class MembershipRepository(BaseTenantRepository[StoreMembershipDocument]):
    collection_name = COLL_STORE_MEMBERSHIPS
    model_cls = StoreMembershipDocument
    id_field = "membership_id"

    async def add_member(self, membership: StoreMembershipDocument) -> StoreMembershipDocument:
        await asyncio.gather(
            self._assert_exists(
                COLL_STORES,
                {"store_id": membership.store_id},
                f"Store {membership.store_id!r} does not exist in '{COLL_STORES}'.",
            ),
            self._assert_exists(
                COLL_USERS,
                {"user_id": membership.user_id},
                f"User {membership.user_id!r} does not exist in '{COLL_USERS}'.",
            ),
        )
        return await self._insert(membership)

    async def get_membership(self, store_id: str, user_id: str) -> StoreMembershipDocument | None:
        return await self._find_one({"store_id": store_id, "user_id": user_id})

    async def list_members_for_store(
        self,
        ctx: TenantContext,
        *,
        status: MembershipStatus | None = "active",
        limit: int = 100,
    ) -> list[StoreMembershipDocument]:
        extra: dict[str, Any] = {}
        if status is not None:
            extra["status"] = status
        return await self.list_in_tenant(ctx, extra, sort=[("created_at", 1)], limit=limit)

    async def list_stores_for_user(
        self,
        user_id: str,
        *,
        status: MembershipStatus | None = "active",
        limit: int = 100,
    ) -> list[StoreMembershipDocument]:
        query: dict[str, Any] = {"user_id": user_id}
        if status is not None:
            query["status"] = status
        return await self._find_many(query, sort=[("created_at", 1)], limit=limit)

    async def update_membership(
        self,
        ctx: TenantContext,
        user_id: str,
        *,
        role: MembershipRole | None = None,
        status: MembershipStatus | None = None,
    ) -> StoreMembershipDocument | None:
        payload = compact_updates(role=role, status=status)
        if not payload:
            return await self.get_membership(ctx.store_id, user_id)
        return await self._update_one(
            self._scoped_filter(ctx, {"user_id": user_id}, require_write=True),
            payload,
        )

    async def verify_tenant_access(
        self,
        *,
        store_id: str,
        user_id: str,
        allowed_roles: set[MembershipRole] | None = None,
    ) -> TenantContext:
        normalized_store_id = store_id.strip()
        normalized_user_id = user_id.strip()
        if not normalized_store_id or not normalized_user_id:
            raise TenantAuthorizationError("Both store_id and user_id are required for tenant authorization.")

        store_doc, membership = await asyncio.gather(
            self._db[COLL_STORES].find_one({"store_id": normalized_store_id}),
            self.get_membership(normalized_store_id, normalized_user_id),
        )
        if store_doc is None:
            raise TenantAuthorizationError(f"Store {normalized_store_id!r} does not exist.")
        if store_doc.get("status") != "active":
            raise TenantAuthorizationError(
                f"Store {normalized_store_id!r} is not active (status={store_doc.get('status')!r})."
            )
        if membership is None:
            raise TenantAuthorizationError(
                f"User {normalized_user_id!r} is not a member of store {normalized_store_id!r}."
            )
        if membership.status != "active":
            raise TenantAuthorizationError(
                f"Membership for user {normalized_user_id!r} in store {normalized_store_id!r} is not active."
            )
        if allowed_roles is not None and membership.role not in allowed_roles:
            raise TenantAuthorizationError(
                f"User {normalized_user_id!r} role {membership.role!r} is not in allowed_roles={sorted(allowed_roles)!r}."
            )

        return TenantContext(
            store_id=normalized_store_id,
            user_id=normalized_user_id,
            role=membership.role,
        )


class CustomerRepository(BaseTenantRepository[CustomerDocument]):
    collection_name = COLL_CUSTOMERS
    model_cls = CustomerDocument
    id_field = "customer_id"

    async def get_by_platform_identity(
        self,
        ctx: TenantContext,
        platform: str,
        platform_user_handle: str,
    ) -> CustomerDocument | None:
        return await self._find_one(
            self._scoped_filter(
                ctx,
                {
                    "platform": platform.strip().lower(),
                    "platform_user_handle": platform_user_handle.strip(),
                },
            )
        )

    async def upsert_customer_profile(
        self,
        ctx: TenantContext,
        customer: CustomerDocument,
    ) -> CustomerDocument:
        self._assert_document_tenant(ctx, customer)
        existing = await self.get_by_platform_identity(
            ctx,
            customer.platform,
            customer.platform_user_handle,
        )
        if existing is None:
            return await self._insert(customer)

        updated = await self.update_by_id(
            ctx,
            existing.customer_id,
            {
                "display_name": customer.display_name or existing.display_name,
                "tags": sorted(set(existing.tags) | set(customer.tags)),
                "notes": customer.notes if customer.notes is not None else existing.notes,
                "last_seen_at": customer.last_seen_at,
            },
        )
        return updated or existing

