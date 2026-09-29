"""Deterministic keyword search over validated synthetic records."""

import re
from collections.abc import Iterable

from ai.data.models import FAQ, Product, SalesPolicy, SearchResult


def search_records(
    keyword: str,
    *,
    products: Iterable[Product],
    faqs: Iterable[FAQ],
    policies: Iterable[SalesPolicy],
) -> tuple[SearchResult, ...]:
    """Find records containing every query token, case-insensitively.

    Results are returned in product, FAQ, then policy order and preserve the
    input order within each record type. Search covers only the requested
    public text fields, not product attributes or policy metadata.
    """
    terms = _tokens(keyword)
    if not terms:
        return ()

    results: list[SearchResult] = []
    for product in products:
        text = f"{product.name} {product.description}"
        if _contains_all(text, terms):
            results.append(
                SearchResult("product", product.id, product.name, product.description)
            )
    for faq in faqs:
        text = f"{faq.question} {faq.answer}"
        if _contains_all(text, terms):
            results.append(SearchResult("faq", faq.id, faq.question, faq.answer))
    for policy in policies:
        if _contains_all(policy.content, terms):
            results.append(
                SearchResult("policy", policy.id, policy.category, policy.content)
            )
    return tuple(results)


def _tokens(text: str) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise TypeError("keyword must be a string")
    return tuple(re.findall(r"\w+", text.casefold()))


def _contains_all(text: str, terms: tuple[str, ...]) -> bool:
    normalized_text = text.casefold()
    return all(term in normalized_text for term in terms)