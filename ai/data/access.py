"""Simple typed accessors for validated records."""

from collections.abc import Iterable

from ai.data.models import FAQ, Product, SalesPolicy


def get_product_by_id(
    products: Iterable[Product], product_id: str
) -> Product | None:
    """Return the matching product, or ``None`` for an unknown ID."""
    return next((product for product in products if product.id == product_id), None)


def get_faqs(
    faqs: Iterable[FAQ],
    *,
    category: str | None = None,
    product_id: str | None = None,
) -> tuple[FAQ, ...]:
    """Filter FAQs by category and/or associated product ID."""
    normalized_category = category.casefold().strip() if category is not None else None
    return tuple(
        faq
        for faq in faqs
        if (normalized_category is None or faq.category.casefold() == normalized_category)
        and (product_id is None or product_id in faq.product_ids)
    )


def get_effective_policies(
    policies: Iterable[SalesPolicy],
) -> tuple[SalesPolicy, ...]:
    """Return only policies explicitly marked effective."""
    return tuple(policy for policy in policies if policy.status == "effective")