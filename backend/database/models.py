from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from backend.database.mongodb import TenantAuthorizationError

SafeIdStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._@-]+$",
    ),
]
ShortStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
MediumStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]
LongStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10000),
]
EmailStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
SlugStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]
CurrencyStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
]

UserStatus = Literal["active", "suspended", "deactivated"]
StoreStatus = Literal["active", "suspended", "archived"]
MembershipRole = Literal["owner", "admin", "operator", "viewer"]
MembershipStatus = Literal["active", "invited", "revoked"]
PolicyTopic = Literal["shipping", "returns", "warranty", "payment", "promotions", "general"]
LivestreamPlatform = Literal["tiktok", "youtube", "shopee", "facebook", "custom"]
SessionStatus = Literal["scheduled", "live", "paused", "ended"]
DraftActionStatus = Literal["pending", "approved", "edited", "dismissed"]
EvidenceSourceType = Literal["product", "variant", "inventory", "faq", "policy"]

WRITE_CAPABLE_ROLES: frozenset[str] = frozenset({"owner", "admin", "operator", "system"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_inventory_scope_key(product_id: str, variant_id: str | None = None) -> str:
    clean_product = product_id.strip()
    if not clean_product:
        raise ValueError("product_id must not be empty when building inventory_scope_key.")
    if variant_id is not None and variant_id.strip():
        return f"variant:{clean_product}:{variant_id.strip()}"
    return f"product:{clean_product}"


class DatabaseDocumentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def to_mongo_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")


class TenantDocumentModel(DatabaseDocumentModel):
    store_id: SafeIdStr


class TenantContext(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    store_id: SafeIdStr
    user_id: SafeIdStr
    role: MembershipRole | Literal["system"]
    verified_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def system_context(cls, store_id: str, *, actor_id: str = "system") -> TenantContext:
        return cls(store_id=store_id, user_id=actor_id, role="system")

    def has_role(self, *allowed_roles: MembershipRole | Literal["system"]) -> bool:
        if self.role == "system":
            return True
        return self.role in allowed_roles

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_CAPABLE_ROLES

    def require_write_permission(self) -> None:
        if not self.can_write:
            raise TenantAuthorizationError(
                f"User {self.user_id!r} with read-only role {self.role!r} is not permitted "
                f"to perform write operations in store {self.store_id!r}."
            )


class UserDocument(DatabaseDocumentModel):
    user_id: SafeIdStr
    email: EmailStr
    display_name: ShortStr
    status: UserStatus = "active"


class StoreDocument(DatabaseDocumentModel):
    store_id: SafeIdStr
    slug: SlugStr
    name: ShortStr
    owner_user_id: SafeIdStr
    status: StoreStatus = "active"
    default_currency: CurrencyStr = "USD"
    timezone: ShortStr = "UTC"
    platform_handles: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class StoreMembershipDocument(TenantDocumentModel):
    membership_id: SafeIdStr
    user_id: SafeIdStr
    role: MembershipRole = "operator"
    status: MembershipStatus = "active"
    invited_by_user_id: SafeIdStr | None = None


class ProductDocument(TenantDocumentModel):
    product_id: SafeIdStr
    sku_code: SafeIdStr
    name: ShortStr
    category: ShortStr
    subcategory: str = Field(default="", max_length=120)
    price: float = Field(..., ge=0.0)
    currency: CurrencyStr = "USD"
    short_description: str = Field(default="", max_length=1000)
    key_benefits: list[str] = Field(default_factory=list)
    common_questions: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    has_variants: bool = False
    is_active: bool = True


class ProductVariantDocument(TenantDocumentModel):
    variant_id: SafeIdStr
    product_id: SafeIdStr
    sku_code: SafeIdStr
    title: ShortStr
    price: float = Field(..., ge=0.0)
    currency: CurrencyStr = "USD"
    options: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class InventoryDocument(TenantDocumentModel):
    inventory_id: SafeIdStr
    product_id: SafeIdStr
    variant_id: SafeIdStr | None = None
    inventory_scope_key: str = Field(default="")
    quantity_on_hand: int = Field(default=0, ge=0)
    quantity_reserved: int = Field(default=0, ge=0)
    in_stock: bool = True
    stock_note: str = Field(default="", max_length=500)
    version: int = Field(default=1, ge=1)

    @field_validator("variant_id", mode="before")
    @classmethod
    def _normalize_empty_variant_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned if cleaned else None

    @model_validator(mode="after")
    def _set_scope_key_and_availability(self) -> InventoryDocument:
        expected_key = build_inventory_scope_key(self.product_id, self.variant_id)
        if self.inventory_scope_key != expected_key:
            object.__setattr__(self, "inventory_scope_key", expected_key)
        if (self.quantity_on_hand - self.quantity_reserved) <= 0 and self.quantity_on_hand == 0:
            object.__setattr__(self, "in_stock", False)
        return self

    @property
    def quantity_available(self) -> int:
        return max(0, self.quantity_on_hand - self.quantity_reserved)


class FAQDocument(TenantDocumentModel):
    faq_id: SafeIdStr
    question: MediumStr
    answer: LongStr
    topic: str = Field(default="general", max_length=120)
    related_product_ids: list[SafeIdStr] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    is_active: bool = True


class SalesPolicyDocument(TenantDocumentModel):
    policy_id: SafeIdStr
    version: int = Field(default=1, ge=1)
    topic: PolicyTopic
    title: ShortStr
    summary: MediumStr
    details: LongStr
    conditions: list[str] = Field(default_factory=list)
    is_active: bool = True
    effective_from: datetime = Field(default_factory=utc_now)
    effective_to: datetime | None = None


class CustomerDocument(TenantDocumentModel):
    customer_id: SafeIdStr
    platform: LivestreamPlatform
    platform_user_handle: ShortStr
    display_name: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class SessionMetricsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_comments: int = Field(default=0, ge=0)
    high_lead_comments: int = Field(default=0, ge=0)
    drafts_generated: int = Field(default=0, ge=0)
    drafts_approved: int = Field(default=0, ge=0)


class LivestreamSessionDocument(TenantDocumentModel):
    session_id: SafeIdStr
    title: ShortStr
    platform: LivestreamPlatform
    status: SessionStatus = "scheduled"
    host_user_id: SafeIdStr | None = None
    pinned_product_ids: list[SafeIdStr] = Field(default_factory=list)
    active_promotion_notes: str = Field(default="", max_length=2000)
    metrics_summary: SessionMetricsSummary = Field(default_factory=SessionMetricsSummary)
    started_at: datetime | None = None
    ended_at: datetime | None = None


class LivestreamCommentDocument(TenantDocumentModel):
    session_id: SafeIdStr
    comment_id: SafeIdStr
    customer_id: SafeIdStr | None = None
    platform: LivestreamPlatform
    user_handle: ShortStr
    message: MediumStr
    timestamp: datetime = Field(default_factory=utc_now)
    intent: str | None = None
    intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    lead_label: str | None = None
    lead_score: int | None = Field(default=None, ge=0, le=100)
    matched_product_ids: list[SafeIdStr] = Field(default_factory=list)
    safety_blocked: bool = False
    safety_reason: str | None = None


class EvidenceSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: EvidenceSourceType
    source_id: SafeIdStr
    title: ShortStr
    snippet: MediumStr
    relevance_score: float = Field(default=1.0, ge=0.0)
    version: int | None = Field(default=None, ge=1)


class AnswerDraftDocument(TenantDocumentModel):
    session_id: SafeIdStr
    draft_id: SafeIdStr
    comment_id: SafeIdStr
    suggested_reply: LongStr
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding_passed: bool = True
    fallback_used: bool = False
    sources: list[EvidenceSourceSnapshot] = Field(default_factory=list)
    seller_action: DraftActionStatus = "pending"
    final_reply_text: str | None = Field(default=None, max_length=5000)
    acted_by_user_id: SafeIdStr | None = None
    acted_at: datetime | None = None
