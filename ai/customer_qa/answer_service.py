"""Deterministic, evidence-grounded customer answer drafts."""

import re

from ai.customer_qa.intent_classifier import classify_intent
from ai.customer_qa.retrieval import _matched_products, _search_hits, retrieve_evidence
from ai.customer_qa.schemas import (
    CustomerAnswerDraft,
    CustomerQuestionRequest,
    Intent,
    SourceReference,
)
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.data import load_synthetic_data
from ai.data.models import FAQ, Product, SyntheticDataset

_ACTION_INTENTS: dict[Intent, str] = {
    "refund_request": "Refunds are consequential actions and cannot be processed here.",
    "price_change_request": "Price changes and discounts require an operator.",
    "consequential_action": "This order or payment action requires an operator and cannot be executed here.",
    "complaint": "This concern requires review by a human operator.",
    "spam_or_irrelevant": "This message is outside supported product and policy questions.",
    "unknown": "This question is outside the information supported by the data layer.",
}
_WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def draft_customer_answer(
    request: CustomerQuestionRequest,
    *,
    dataset: SyntheticDataset | None = None,
    intent_override: Intent | None = None,
) -> CustomerAnswerDraft:
    """Return an offline deterministic draft; never performs business actions."""
    if not isinstance(request.text, str) or not request.text.strip():
        return _review(
            "unknown",
            "The customer question is empty or invalid.",
        )
    if len(request.text) > MAX_CUSTOMER_MESSAGE_LENGTH:
        return _review(
            "unknown",
            f"The customer message exceeds the {MAX_CUSTOMER_MESSAGE_LENGTH}-character limit.",
        )

    classification = classify_intent(request.text)
    intent = classification.intent
    if intent == "unknown" and intent_override is not None:
        intent = intent_override
    if intent == "prompt_injection":
        return _review(
            intent,
            "The message contains untrusted instructions and needs operator review.",
        )
    if intent in _ACTION_INTENTS and intent != "unknown":
        return _review(intent, _ACTION_INTENTS[intent])

    records = dataset if dataset is not None else load_synthetic_data()
    if intent == "unknown":
        classification = classify_intent(
            request.text,
            product_names=(product.name for product in records.products),
        )
        intent = classification.intent
        if intent == "unknown":
            return _review(intent, _ACTION_INTENTS[intent])
    references = retrieve_evidence(request.text, intent=intent, dataset=records)
    if intent in {"shipping_policy", "return_policy", "promotion_question"}:
        return _answer_policy_question(request.text, intent, references)
    if intent in {"price_question", "stock_question", "product_information"}:
        return _answer_product_question(request.text, intent, records, references)
    return _review(intent, "No supported answer rule exists for this intent.")


def _answer_policy_question(
    question: str,
    intent: Intent,
    references: tuple[SourceReference, ...],
) -> CustomerAnswerDraft:
    policy_references = tuple(
        reference for reference in references if reference.source_type == "policy"
    )
    unsupported_reason = _unsupported_policy_detail(
        question,
        intent,
        policy_references,
    )
    if unsupported_reason is not None:
        return _review(
            intent,
            unsupported_reason,
            references=policy_references,
        )
    if intent == "promotion_question" and _asks_for_specific_promotion(question):
        return _review(
            intent,
            "Specific promotion details are not present in the approved synthetic records.",
            references=policy_references,
        )
    if not policy_references:
        return _review(
            intent,
            "No effective policy record supports this question.",
            references=references,
        )
    if len(policy_references) > 1:
        ids = ", ".join(reference.source_id for reference in policy_references)
        return _review(
            intent,
            f"Multiple effective policy records match this category ({ids}); "
            "an operator must resolve them.",
            references=policy_references,
        )
    reference = policy_references[0]
    faq_references = tuple(item for item in references if item.source_type == "faq")
    conflicting_faqs = tuple(
        item for item in faq_references if _policy_faq_conflict(reference, item)
    )
    if conflicting_faqs:
        return _review(
            intent,
            "An FAQ and the effective policy contain conflicting time intervals.",
            references=(reference,) + conflicting_faqs,
        )
    return _answered(
        intent,
        f"Draft (synthetic demo data): {reference.excerpt}",
        (reference,),
    )


def _answer_product_question(
    question: str,
    intent: Intent,
    dataset: SyntheticDataset,
    references: tuple[SourceReference, ...],
) -> CustomerAnswerDraft:
    hits = _search_hits(question, dataset)
    products = _matched_products(question, dataset.products, hits)
    if len(products) != 1:
        reason = (
            "More than one product matches the question; an operator must clarify."
            if products
            else "No matching product or product-specific evidence was found."
        )
        return _review(intent, reason, references=references)

    product = products[0]
    product_reference = next(
        (
            reference
            for reference in references
            if reference.source_type == "product" and reference.source_id == product.id
        ),
        None,
    )
    if product_reference is None:
        return _review(
            intent,
            "The matching product could not be retrieved as supporting evidence.",
        )

    if intent == "price_question":
        conflicts = _conflicting_product_faqs(product, dataset.faqs, "price")
        if conflicts:
            return _review(
                intent,
                "The catalog and a product FAQ contain conflicting price information.",
                references=(product_reference,)
                + tuple(_faq_source(faq) for faq in conflicts),
            )
        return _answered(
            intent,
            f"Draft (synthetic demo data): the listed price for {product.name} is "
            f"{product.price:.2f}. This is a fictional demo price.",
            (product_reference,),
        )

    if intent == "stock_question":
        conflicts = _conflicting_product_faqs(product, dataset.faqs, "stock")
        if conflicts:
            return _review(
                intent,
                "The catalog and a product FAQ contain conflicting stock information.",
                references=(product_reference,)
                + tuple(_faq_source(faq) for faq in conflicts),
            )
        return _answered(
            intent,
            f"Draft (synthetic demo data): the catalog lists {product.stock_quantity} "
            f"simulated units for {product.name}. This is not live inventory.",
            (product_reference,),
        )

    faq_references = tuple(
        reference
        for reference in references
        if reference.source_type == "faq"
        and any(faq.id == reference.source_id for faq in dataset.faqs)
    )
    if len(faq_references) == 1:
        faq = next(faq for faq in dataset.faqs if faq.id == faq_references[0].source_id)
        if _faq_conflicts_with_product(question, faq, product):
            return _review(
                intent,
                "The product FAQ conflicts with a structured product attribute.",
                references=(faq_references[0], product_reference),
            )
        return _answered(
            intent,
            f"Draft (synthetic demo data): {faq.answer}",
            (faq_references[0],),
        )
    if len(faq_references) > 1:
        return _review(
            intent,
            "Multiple product FAQ records match; an operator must select the relevant fact.",
            references=faq_references,
        )

    attribute = _requested_attribute(question, product)
    if attribute is not None:
        label, value = attribute
        return _answered(
            intent,
            f"Draft (synthetic demo data): {product.name} {label}: {value}.",
            (product_reference,),
        )
    if _is_general_product_question(question, product):
        return _answered(
            intent,
            f"Draft (synthetic demo data): {product.description}",
            (product_reference,),
        )
    return _review(
        intent,
        "The product is known, but the requested detail is not present in its records.",
        references=(product_reference,),
    )


def _requested_attribute(question: str, product: Product) -> tuple[str, str] | None:
    terms = set(_tokens(question))
    attributes = product.attributes
    if terms.intersection({"color", "colour"}) and "color" in attributes:
        return "color", str(attributes["color"])
    if "capacity" in terms:
        for key, unit in (("capacity_ml", "ml"), ("capacity_l", "L")):
            if key in attributes:
                return "capacity", f"{attributes[key]} {unit}"
    if terms.intersection({"mode", "modes"}) and "light_modes" in attributes:
        return "light modes", str(attributes["light_modes"])
    if "material" in terms and "material" in attributes:
        return "material", str(attributes["material"])
    if terms.intersection({"port", "ports"}) and "ports" in attributes:
        return "ports", str(attributes["ports"])
    return None


def _is_general_product_question(question: str, product: Product) -> bool:
    terms = set(_tokens(question))
    question_words = {
        "what", "is", "the", "a", "an", "this", "that", "tell", "me",
        "about", "describe", "give", "overview", "details",
    }
    product_words = set(_tokens(product.name))
    return not (terms - question_words - product_words)


def _conflicting_product_faqs(
    product: Product,
    faqs: tuple[FAQ, ...],
    fact_type: str,
) -> tuple[FAQ, ...]:
    conflicts: list[FAQ] = []
    for faq in faqs:
        if product.id not in faq.product_ids:
            continue
        text = f"{faq.question} {faq.answer}"
        if fact_type == "price" and re.search(r"\b(price|cost)\b", text, re.I):
            matches = re.findall(
                r"\b(?:price|cost)\b[^\d$]{0,40}\$?\s*(\d+(?:\.\d+)?)",
                text,
                re.I,
            )
            if any(abs(float(value) - product.price) > 0.005 for value in matches):
                conflicts.append(faq)
        if fact_type == "stock" and re.search(
            r"\b(stock|inventory|quantity|available)\b", text, re.I
        ):
            matches = re.findall(
                r"\b(?:stock|inventory|quantity|available)\b[^\d]{0,20}(\d+)",
                text,
                re.I,
            )
            if any(int(value) != product.stock_quantity for value in matches):
                conflicts.append(faq)
    return tuple(conflicts)


def _faq_source(faq: FAQ) -> SourceReference:
    return SourceReference("faq", faq.id, faq.question, faq.answer)


def _faq_conflicts_with_product(question: str, faq: FAQ, product: Product) -> bool:
    terms = set(_tokens(question))
    answer = faq.answer.casefold()
    if terms.intersection({"color", "colour"}) and "color" in product.attributes:
        return str(product.attributes["color"]).casefold() not in answer
    if terms.intersection({"mode", "modes"}) and "light_modes" in product.attributes:
        expected = int(product.attributes["light_modes"])
        numbers = {
            int(value) if value.isdigit() else _word_number(value)
            for value in re.findall(r"\b(?:\d+|[a-z]+)\b", answer)
            if value.isdigit() or value in _WORD_NUMBERS
        }
        return expected not in numbers
    return False


def _word_number(value: str) -> int:
    return _WORD_NUMBERS[value]


def _asks_for_specific_promotion(question: str) -> bool:
    return bool(
        re.search(r"\b\d+\s*%", question)
        or re.search(r"\b(?:promo|coupon|discount)\s+code\b", question, re.I)
        or re.search(r"\b(?:tonight|tomorrow|upcoming|next week|at \d{1,2})\b", question, re.I)
    )


def _policy_faq_conflict(
    policy: SourceReference,
    faq: SourceReference,
) -> bool:
    duration_pattern = (
        r"\b(\d+|[a-z]+)\s+(?:(?:simulated|business)\s+){0,2}days?\b"
    )
    policy_durations = _duration_values(policy.excerpt, duration_pattern)
    faq_durations = _duration_values(faq.excerpt, duration_pattern)
    return bool(
        policy_durations
        and faq_durations
        and policy_durations.isdisjoint(faq_durations)
    )


def _duration_values(text: str, pattern: str) -> set[int]:
    values: set[int] = set()
    for value in re.findall(pattern, text.casefold()):
        if value.isdigit():
            values.add(int(value))
        elif value in _WORD_NUMBERS:
            values.add(_WORD_NUMBERS[value])
    return values


def _unsupported_policy_detail(
    question: str,
    intent: Intent,
    policy_references: tuple[SourceReference, ...],
) -> str | None:
    terms = set(_tokens(question))
    if intent == "shipping_policy":
        unsupported_terms = {
            "date", "arrival", "arrive", "tracking", "track", "guarantee",
            "guaranteed", "today", "tomorrow", "tonight", "monday", "tuesday",
            "wednesday", "thursday", "friday", "saturday", "sunday", "fee",
            "fees", "cost", "costs", "charge", "carrier", "courier",
        }
        if terms.intersection(unsupported_terms) or re.search(
            r"\b\d{1,2}(?:st|nd|rd|th)?\b", question
        ) or re.search(r"\bhow\s+much\b", question, re.I):
            return (
                "Synthetic shipping data covers only a general dispatch interval, "
                "not fees, dates, arrival, tracking, carriers, or guarantees."
            )
    if intent == "return_policy":
        duration_pattern = (
            r"\b(\d+|[a-z]+)\s+(?:(?:simulated|business)\s+){0,2}days?\b"
        )
        requested_durations = _duration_values(question, duration_pattern)
        policy_durations = set().union(
            *(
                _duration_values(reference.excerpt, duration_pattern)
                for reference in policy_references
            )
        ) if policy_references else set()
        if requested_durations and policy_durations and not requested_durations.issubset(
            policy_durations
        ):
            return (
                "The synthetic return policy does not establish eligibility for the "
                "specific duration in this question."
            )
        if terms.intersection({"opened", "used", "receipt", "packaging", "worn"}):
            return "The synthetic return policy does not establish this item-specific eligibility."
    return None


def _answered(
    intent: Intent,
    answer: str,
    references: tuple[SourceReference, ...],
) -> CustomerAnswerDraft:
    if not references:
        return _review(intent, "No source reference supports this draft.")
    return CustomerAnswerDraft(
        answer=answer,
        intent=intent,
        sources=references,
        status="answered",
        needs_human_review=False,
        reason=None,
    )


def _review(
    intent: Intent,
    reason: str,
    *,
    references: tuple[SourceReference, ...] = (),
) -> CustomerAnswerDraft:
    return CustomerAnswerDraft(
        answer="Draft: This message needs review by a human operator before a reply is sent.",
        intent=intent,
        sources=references,
        status="needs_review",
        needs_human_review=True,
        reason=reason,
    )


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))