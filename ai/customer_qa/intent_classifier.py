"""Transparent keyword-based baseline for customer question intents."""

import re
from collections import Counter
from collections.abc import Iterable

from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH, detect_prompt_injection
from ai.customer_qa.schemas import Intent, IntentClassification

# Earlier entries take precedence when a message matches multiple categories.
_RULES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    ("prompt_injection", ()),
    ("refund_request", ("refund", "money back", "return my money")),
    (
        "price_change_request",
        (
            "change price",
            "change the price",
            "update the price",
            "lower the price",
            "raise the price",
            "match the price",
            "apply discount",
            "give me a discount",
            "offer me a discount",
        ),
    ),
    (
        "consequential_action",
        (
            "place order",
            "place an order",
            "confirm order",
            "confirm my order",
            "cancel order",
            "cancel my order",
            "purchase for me",
            "buy it for me",
            "process payment",
            "charge my card",
            "change my address",
            "update my address",
            "start a return",
            "request a return",
            "process my return",
            "i want to return",
            "i need to return",
        ),
    ),
    (
        "complaint",
        (
            "complaint",
            "complain",
            "broken",
            "damaged",
            "defective",
            "wrong item",
            "not happy",
            "angry",
        ),
    ),
    (
        "spam_or_irrelevant",
        (
            "buy followers",
            "subscribe to my channel",
            "click this link",
            "crypto giveaway",
        ),
    ),
    (
        "promotion_question",
        ("promotion", "promo", "discount", "sale", "voucher", "coupon"),
    ),
    (
        "shipping_policy",
        ("shipping", "ship", "delivery", "deliver", "dispatch", "tracking"),
    ),
    (
        "return_policy",
        ("return", "exchange", "send it back"),
    ),
    (
        "stock_question",
        ("stock", "in stock", "available", "availability", "inventory", "quantity"),
    ),
    ("price_question", ("price", "how much", "cost", "costs")),
    (
        "product_information",
        (
            "product details",
            "tell me about",
            "describe",
            "color",
            "colour",
            "capacity",
            "feature",
            "material",
            "light mode",
            "ports",
        ),
    ),
)


def classify_intent(
    message: str,
    *,
    product_names: Iterable[str] = (),
) -> IntentClassification:
    """Classify text using precedence rules and optional known catalog names."""
    if not isinstance(message, str):
        raise TypeError("message must be a string")
    if len(message) > MAX_CUSTOMER_MESSAGE_LENGTH:
        return IntentClassification("unknown", (), ())

    injection_matches = detect_prompt_injection(message)
    matched: list[tuple[Intent, tuple[str, ...]]] = []
    for intent, keywords in _RULES:
        if intent == "prompt_injection":
            if injection_matches:
                matched.append((intent, injection_matches))
            continue
        found = tuple(keyword for keyword in keywords if _matches(message, keyword))
        if intent == "price_change_request" and not found:
            found = _price_change_matches(message)
        if intent == "consequential_action" and not found:
            found = _consequential_action_matches(message)
        if found:
            matched.append((intent, found))

    if not matched:
        product_name_matches = _known_product_name_matches(message, product_names)
        if product_name_matches:
            matched.append(("product_information", product_name_matches))
        else:
            return IntentClassification("unknown", (), ())
    matched_intents = [intent for intent, _ in matched]
    if "price_change_request" in matched_intents:
        matched_intents = [
            intent
            for intent in matched_intents
            if intent not in {"price_question", "promotion_question"}
        ]
    if "refund_request" in matched_intents:
        matched_intents = [
            intent for intent in matched_intents if intent != "return_policy"
        ]
    selected_intent, selected_keywords = matched[0]
    return IntentClassification(
        intent=selected_intent,
        matched_intents=tuple(matched_intents),
        matched_keywords=selected_keywords,
    )


def _matches(message: str, keyword: str) -> bool:
    pattern = r"\b" + r"\s+".join(re.escape(part) for part in keyword.split()) + r"\b"
    return re.search(pattern, message, flags=re.IGNORECASE) is not None


def _price_change_matches(message: str) -> tuple[str, ...]:
    action = r"(?:change|update|lower|raise|increase|decrease|reduce|set|adjust|match)"
    patterns = (
        rf"\b{action}\b.{{0,60}}\bprice\b",
        rf"\bprice\b.{{0,60}}\b(?:to|lower|raise|increase|decrease|reduce|adjust)\b",
        r"\b(?:offer|give|apply|waive|make|knock)\b.{0,60}\b(?:discount|\d+\s*%\s*off)\b",
    )
    if any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns):
        return ("price change action",)
    return ()


def _consequential_action_matches(message: str) -> tuple[str, ...]:
    patterns = (
        r"\b(?:place|confirm|cancel|modify|change|update)\b.{0,50}"
        r"\b(?:order|purchase|address)\b",
        r"\b(?:buy|purchase)\b.{0,50}\bfor me\b",
        r"\b(?:process|submit|complete)\b.{0,40}\b(?:payment|checkout|order)\b",
    )
    if any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns):
        return ("consequential action",)
    return ()


def _known_product_name_matches(
    message: str,
    product_names: Iterable[str],
) -> tuple[str, ...]:
    names = tuple(product_names)
    query_tokens = set(re.findall(r"[a-z0-9]+", message.casefold()))
    name_tokens = [
        set(re.findall(r"[a-z0-9]+", name.casefold())) for name in names
    ]
    token_counts = Counter(token for tokens in name_tokens for token in tokens)
    for tokens in name_tokens:
        if tokens and (
            tokens.issubset(query_tokens)
            or any(
                token in query_tokens and len(token) >= 4 and token_counts[token] == 1
                for token in tokens
            )
        ):
            return ("known product name",)
    return ()