"""Deterministic evidence retrieval over the validated synthetic dataset."""

import re
from collections import Counter

from ai.customer_qa.intent_classifier import classify_intent
from ai.customer_qa.schemas import Intent, SourceReference
from ai.data import get_effective_policies, load_synthetic_data, search_records
from ai.data.models import FAQ, Product, SyntheticDataset

_STOP_WORDS = {
    "a", "an", "and", "are", "about", "can", "could", "do", "does", "for",
    "from", "have", "how", "i", "in", "is", "it", "me", "my", "of", "on",
    "please", "the", "this", "to", "what", "when", "where", "which", "with",
    "would", "much", "many", "tell", "policy", "question", "product", "price",
    "cost", "stock", "shipping", "delivery", "return", "promotion", "promo",
    "discount", "sale", "available", "availability", "inventory", "quantity",
}
_POLICY_CATEGORY: dict[Intent, str] = {
    "shipping_policy": "shipping",
    "return_policy": "returns",
    "promotion_question": "promotions",
}


def retrieve_evidence(
    question: str,
    *,
    intent: Intent | None = None,
    dataset: SyntheticDataset | None = None,
) -> tuple[SourceReference, ...]:
    """Return relevant, stable references; inactive policies are never included.

    Existing keyword search generates candidates. Candidates are then filtered
    by product-name/entity matches, FAQ topic overlap, and policy category.
    Product and policy records preserve catalog order for deterministic output.
    """
    if not isinstance(question, str):
        raise TypeError("question must be a string")
    if not question.strip():
        return ()
    selected_intent = intent or classify_intent(question).intent
    if selected_intent in {
        "prompt_injection",
        "refund_request",
        "price_change_request",
        "consequential_action",
        "complaint",
        "spam_or_irrelevant",
        "unknown",
    }:
        return ()

    records = dataset if dataset is not None else load_synthetic_data()
    hits = _search_hits(question, records)
    product_matches = _matched_products(question, records.products, hits)
    product_ids = {product.id for product in product_matches}
    faq_matches = _matched_faqs(
        question,
        selected_intent,
        records.faqs,
        product_matches,
        hits,
    )

    references: list[SourceReference] = []
    if selected_intent in {
        "product_information",
        "price_question",
        "stock_question",
    }:
        references.extend(_product_reference(product) for product in product_matches)
        if selected_intent == "product_information":
            references.extend(_faq_reference(faq) for faq in faq_matches)
    else:
        references.extend(_faq_reference(faq) for faq in faq_matches)
        category = _POLICY_CATEGORY.get(selected_intent)
        if category is not None:
            effective = get_effective_policies(records.policies)
            references.extend(
                SourceReference(
                    source_type="policy",
                    source_id=policy.id,
                    title=policy.category,
                    excerpt=policy.content,
                )
                for policy in effective
                if policy.category.casefold() == category
            )

    # Stable de-duplication also prevents repeated query terms duplicating sources.
    unique: dict[tuple[str, str], SourceReference] = {}
    for reference in references:
        unique.setdefault((reference.source_type, reference.source_id), reference)
    return tuple(unique.values())


def _search_hits(question: str, dataset: SyntheticDataset) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    effective_policies = get_effective_policies(dataset.policies)
    for token in _tokens(question):
        if token in _STOP_WORDS:
            continue
        matches = search_records(
            token,
            products=dataset.products,
            faqs=dataset.faqs,
            policies=effective_policies,
        )
        hits.update((match.record_type, match.record_id) for match in matches)
    return hits


def _matched_products(
    question: str,
    products: tuple[Product, ...],
    hits: set[tuple[str, str]],
) -> tuple[Product, ...]:
    query_tokens = set(_tokens(question))
    compact_question = re.sub(r"[^a-z0-9]", "", question.casefold())
    name_counts = Counter(
        token
        for product in products
        for token in set(_tokens(product.name))
        if len(token) >= 4
    )
    matches: list[Product] = []
    for product in products:
        name_tokens = set(_tokens(product.name))
        distinctive_matches = {
            token
            for token in name_tokens & query_tokens
            if len(token) >= 4 and name_counts[token] == 1
        }
        id_token = re.sub(r"[^a-z0-9]", "", product.id.casefold())
        explicit_name = bool(name_tokens) and name_tokens.issubset(query_tokens)
        explicit_id = id_token in compact_question
        if (explicit_name or explicit_id or distinctive_matches) and (
            explicit_id or ("product", product.id) in hits or explicit_name
        ):
            matches.append(product)
    return tuple(matches)


def _matched_faqs(
    question: str,
    intent: Intent,
    faqs: tuple[FAQ, ...],
    products: tuple[Product, ...],
    hits: set[tuple[str, str]],
) -> tuple[FAQ, ...]:
    query_tokens = set(_tokens(question)) - _STOP_WORDS
    for product in products:
        query_tokens.difference_update(_tokens(product.name))
    policy_category = _POLICY_CATEGORY.get(intent)
    matched: list[FAQ] = []
    for faq in faqs:
        if ("faq", faq.id) not in hits:
            continue
        if policy_category is not None:
            if faq.category.casefold() == policy_category:
                matched.append(faq)
            continue
        if products and faq.product_ids and not any(
            product.id in faq.product_ids for product in products
        ):
            continue
        if query_tokens.intersection(_tokens(faq.question)):
            matched.append(faq)
    return tuple(matched)


def _product_reference(product: Product) -> SourceReference:
    attributes = ", ".join(
        f"{key}={value}" for key, value in sorted(product.attributes.items())
    )
    excerpt = (
        f"Name: {product.name}. Description: {product.description} "
        f"Price: {product.price:.2f} (synthetic demo). "
        f"Stock quantity: {product.stock_quantity} (simulated). "
        f"Category: {product.category}. Attributes: {attributes}."
    )
    return SourceReference("product", product.id, product.name, excerpt)


def _faq_reference(faq: FAQ) -> SourceReference:
    return SourceReference("faq", faq.id, faq.question, faq.answer)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))