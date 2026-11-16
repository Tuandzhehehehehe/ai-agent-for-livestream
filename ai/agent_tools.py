"""Minimal tool layer for the livestream AI agent.

This repository intentionally keeps a deterministic, evidence-first workflow.
The tools below wrap existing retrieval and summary logic instead of creating a
second implementation. They are explicit, validated wrappers rather than an
unrestricted autonomous agent API.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ai.customer_qa.schemas import Intent
from ai.customer_qa.intent_classifier import classify_intent
from ai.data import get_effective_policies, load_synthetic_data, search_records
from ai.data.access import get_product_by_id
from ai.data.models import Product, SyntheticDataset
from ai.operations.aggregation import aggregate_frequent_questions, aggregate_recurring_issues
from ai.operations.comment_classifier import classify_comment
from ai.operations.schemas import CommentClassification, SimulatedComment
from ai.operations.session_summarizer import summarize_session


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    handler: Any
    parameters: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    data: dict[str, Any]
    error: str | None = None


def _load_dataset(dataset: SyntheticDataset | None = None) -> SyntheticDataset:
    return dataset if dataset is not None else load_synthetic_data()


def _ensure_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    return stripped


def _normalize_comment(value: Any, *, index: int) -> SimulatedComment:
    if isinstance(value, SimulatedComment):
        return value
    if isinstance(value, Mapping):
        comment_id = value.get("comment_id")
        text = value.get("text")
        if not isinstance(comment_id, str) or not comment_id.strip():
            raise ValueError(f"comments[{index}].comment_id must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError(f"comments[{index}].text must be a string")
        return SimulatedComment(comment_id=comment_id.strip(), text=text)
    raise TypeError(f"comments[{index}] must be a SimulatedComment or mapping")


def search_products(
    query: str,
    *,
    limit: int = 5,
    dataset: SyntheticDataset | None = None,
) -> ToolResult:
    """Search the synthetic product catalog by user query."""
    try:
        clean_query = _ensure_text(query, field_name="query")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        records = _load_dataset(dataset)
        hits = []
        for product in records.products:
            haystack = f"{product.name} {product.description} {product.category}".casefold()
            if clean_query.casefold() in haystack:
                hits.append(
                    {
                        "product_id": product.id,
                        "name": product.name,
                        "category": product.category,
                        "price": product.price,
                        "stock_quantity": product.stock_quantity,
                        "synthetic_data": True,
                        "source_type": "product",
                        "source_id": product.id,
                        "title": product.name,
                        "excerpt": product.description,
                    }
                )
        if len(hits) > limit:
            hits = hits[:limit]
        return ToolResult(
            name="search_products",
            ok=True,
            data={
                "query": clean_query,
                "count": len(hits),
                "items": hits,
                "synthetic_data": True,
                "source_refs": [
                    {"source_type": item["source_type"], "source_id": item["source_id"], "title": item["title"], "excerpt": item["excerpt"]}
                    for item in hits
                ],
            },
        )
    except (TypeError, ValueError) as exc:
        return ToolResult(name="search_products", ok=False, data={}, error=str(exc))


def get_product_inventory(
    *,
    product_id: str | None = None,
    product_name: str | None = None,
    query: str | None = None,
    dataset: SyntheticDataset | None = None,
) -> ToolResult:
    """Return a single product's synthetic inventory and price metadata."""
    try:
        records = _load_dataset(dataset)
        product: Product | None = None
        if product_id is not None and str(product_id).strip():
            product = get_product_by_id(records.products, _ensure_text(product_id, field_name="product_id"))
        elif product_name is not None and str(product_name).strip():
            name = _ensure_text(product_name, field_name="product_name")
            product = next(
                (candidate for candidate in records.products if candidate.name.casefold() == name.casefold()),
                None,
            )
            if product is None:
                product = next(
                    (candidate for candidate in records.products if name.casefold() in candidate.name.casefold()),
                    None,
                )
        elif query is not None and str(query).strip():
            clean_query = _ensure_text(query, field_name="query").casefold()
            product = next(
                (
                    candidate
                    for candidate in records.products
                    if candidate.id.casefold() in clean_query
                    or candidate.name.casefold() in clean_query
                ),
                None,
            )
            if product is None:
                query_words = {
                    word for word in clean_query.replace("?", " ").replace(",", " ").split() if len(word) >= 4
                }
                product = next(
                    (
                        candidate
                        for candidate in records.products
                        if query_words.intersection(candidate.name.casefold().split())
                    ),
                    None,
                )
        if product is None:
            raise ValueError("No matching synthetic product was found")
        result = {
            "product_id": product.id,
            "name": product.name,
            "category": product.category,
            "price": product.price,
            "stock_quantity": product.stock_quantity,
            "inventory_status": "in_stock" if product.stock_quantity > 0 else "out_of_stock",
            "synthetic_data": True,
            "source_type": "product",
            "source_id": product.id,
            "title": product.name,
            "excerpt": product.description,
        }
        return ToolResult(name="get_product_inventory", ok=True, data={"item": result, "source_refs": [result]})
    except (TypeError, ValueError) as exc:
        return ToolResult(name="get_product_inventory", ok=False, data={}, error=str(exc))


def get_shipping_policy(*, dataset: SyntheticDataset | None = None) -> ToolResult:
    """Return the effective shipping policy from the synthetic policy set."""
    try:
        records = _load_dataset(dataset)
        policies = tuple(policy for policy in get_effective_policies(records.policies) if policy.category.casefold() == "shipping")
        if not policies:
            raise ValueError("No effective shipping policy is available in the synthetic dataset")
        items = [
            {
                "policy_id": policy.id,
                "category": policy.category,
                "content": policy.content,
                "status": policy.status,
                "synthetic_data": True,
                "source_type": "policy",
                "source_id": policy.id,
                "title": policy.category,
                "excerpt": policy.content,
            }
            for policy in policies
        ]
        return ToolResult(name="get_shipping_policy", ok=True, data={"items": items, "source_refs": items})
    except (TypeError, ValueError) as exc:
        return ToolResult(name="get_shipping_policy", ok=False, data={}, error=str(exc))


def get_return_policy(*, dataset: SyntheticDataset | None = None) -> ToolResult:
    """Return the effective return policy from the synthetic policy set."""
    try:
        records = _load_dataset(dataset)
        policies = tuple(policy for policy in get_effective_policies(records.policies) if policy.category.casefold() == "returns")
        if not policies:
            raise ValueError("No effective return policy is available in the synthetic dataset")
        items = [
            {
                "policy_id": policy.id,
                "category": policy.category,
                "content": policy.content,
                "status": policy.status,
                "synthetic_data": True,
                "source_type": "policy",
                "source_id": policy.id,
                "title": policy.category,
                "excerpt": policy.content,
            }
            for policy in policies
        ]
        return ToolResult(name="get_return_policy", ok=True, data={"items": items, "source_refs": items})
    except (TypeError, ValueError) as exc:
        return ToolResult(name="get_return_policy", ok=False, data={}, error=str(exc))


def get_order_status(
    *,
    order_id: str | None = None,
    customer_handle: str | None = None,
    query: str | None = None,
) -> ToolResult:
    """Look up synthetic livestream orders by order_id, customer_handle, or query text."""
    try:
        orders_path = Path(__file__).resolve().parent / "data" / "synthetic" / "orders.json"
        if not orders_path.is_file():
            return ToolResult(name="get_order_status", ok=True, data={"count": 0, "items": []})
        payload = json.loads(orders_path.read_text(encoding="utf-8"))
        records = payload.get("records", []) if isinstance(payload, dict) else []
        needle_id = (order_id or "").strip().casefold()
        needle_handle = (customer_handle or "").strip().casefold()
        needle_query = (query or "").strip().casefold()

        matched = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            oid = str(rec.get("order_id", "")).casefold()
            handle = str(rec.get("customer_handle", "")).casefold()
            cname = str(rec.get("customer_name", "")).casefold()
            if needle_id and needle_id == oid:
                matched.append(rec)
            elif needle_handle and (needle_handle == handle or needle_handle.lstrip("@") == handle.lstrip("@")):
                matched.append(rec)
            elif needle_query and (
                (oid and oid in needle_query)
                or (handle and handle in needle_query)
                or (cname and cname in needle_query)
            ):
                matched.append(rec)
            elif not needle_id and not needle_handle and not needle_query:
                matched.append(rec)

        return ToolResult(
            name="get_order_status",
            ok=True,
            data={"count": len(matched), "items": matched, "synthetic_data": True},
        )
    except (OSError, ValueError, TypeError) as exc:
        return ToolResult(name="get_order_status", ok=False, data={}, error=str(exc))


def aggregate_frequent_questions_tool(
    comments: Sequence[SimulatedComment | Mapping[str, Any]],
    classifications: Sequence[CommentClassification] | None = None,
    *,
    minimum_frequency: int = 2,
) -> ToolResult:
    """Group repeated customer questions using the existing operations aggregation logic."""
    try:
        parsed = tuple(_normalize_comment(comment, index=index) for index, comment in enumerate(comments))
        if classifications is None:
            product_names = tuple()
            classifications = tuple(classify_comment(comment, product_names=product_names) for comment in parsed)
        result = aggregate_frequent_questions(parsed, classifications, minimum_frequency=minimum_frequency)
        payload = [
            {
                "normalized_question": item.normalized_question,
                "category": item.category,
                "count": item.count,
                "comment_ids": list(item.comment_ids),
                "sources": [
                    {"source_type": source.source_type, "source_id": source.source_id, "title": source.title, "excerpt": source.excerpt}
                    for source in item.sources
                ],
            }
            for item in result
        ]
        return ToolResult(name="aggregate_frequent_questions", ok=True, data={"items": payload, "count": len(payload)})
    except (TypeError, ValueError) as exc:
        return ToolResult(name="aggregate_frequent_questions", ok=False, data={}, error=str(exc))


def identify_recurring_issues(
    comments: Sequence[SimulatedComment | Mapping[str, Any]],
    classifications: Sequence[CommentClassification] | None = None,
) -> ToolResult:
    """Group recurring review-worthy comments and events using the existing issue analysis."""
    try:
        parsed = tuple(_normalize_comment(comment, index=index) for index, comment in enumerate(comments))
        if classifications is None:
            product_names = tuple()
            classifications = tuple(classify_comment(comment, product_names=product_names) for comment in parsed)
        result = aggregate_recurring_issues(parsed, classifications)
        payload = [
            {
                "issue_type": item.issue_type,
                "source_type": item.source_type,
                "count": item.count,
                "comment_ids": list(item.comment_ids),
                "event_ids": list(item.event_ids),
                "sources": [
                    {"source_type": source.source_type, "source_id": source.source_id, "title": source.title, "excerpt": source.excerpt}
                    for source in item.sources
                ],
            }
            for item in result
        ]
        return ToolResult(name="identify_recurring_issues", ok=True, data={"items": payload, "count": len(payload)})
    except (TypeError, ValueError) as exc:
        return ToolResult(name="identify_recurring_issues", ok=False, data={}, error=str(exc))


def summarize_livestream_session(
    comments: Iterable[Mapping[str, Any] | SimulatedComment] | None = None,
    events: Iterable[Mapping[str, Any] | Any] | None = None,
    *,
    dataset: SyntheticDataset | None = None,
    minimum_question_frequency: int = 2,
) -> ToolResult:
    """Wrap the deterministic session summary logic and expose a structured result."""
    try:
        summary = summarize_session(
            comments=comments,
            events=events,
            dataset=dataset,
            minimum_question_frequency=minimum_question_frequency,
        )
        return ToolResult(
            name="summarize_livestream_session",
            ok=True,
            data={
                "overview": summary.overview,
                "comment_count": summary.comment_count,
                "event_count": summary.event_count,
                "frequent_questions": [
                    {
                        "normalized_question": item.normalized_question,
                        "category": item.category,
                        "count": item.count,
                        "comment_ids": list(item.comment_ids),
                    }
                    for item in summary.frequent_questions
                ],
                "recurring_issues": [
                    {
                        "issue_type": item.issue_type,
                        "source_type": item.source_type,
                        "count": item.count,
                        "comment_ids": list(item.comment_ids),
                        "event_ids": list(item.event_ids),
                    }
                    for item in summary.recurring_issues
                ],
                "recommendations": [
                    {"text": item.text, "reason": item.reason}
                    for item in summary.recommendations
                ],
                "synthetic_data": True,
            },
        )
    except (TypeError, ValueError) as exc:
        return ToolResult(name="summarize_livestream_session", ok=False, data={}, error=str(exc))


INTENT_TO_TOOL: dict[Intent, str] = {
    "shipping_policy": "get_shipping_policy",
    "return_policy": "get_return_policy",
    "promotion_question": "search_products",
    "stock_question": "get_product_inventory",
    "price_question": "get_product_inventory",
    "product_information": "search_products",
    "unknown": "search_products",
}


AGENT_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_products": ToolSpec(
        name="search_products",
        description="Search the synthetic product catalog for product facts and storefront details.",
        handler=search_products,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword or product query from the customer.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of products to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    ),
    "get_product_inventory": ToolSpec(
        name="get_product_inventory",
        description="Fetch a single product's synthetic price and stock information.",
        handler=get_product_inventory,
        parameters={
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "Exact product ID if known (e.g. SYN-PROD-001).",
                },
                "product_name": {
                    "type": "string",
                    "description": "Product name mentioned by the viewer.",
                },
                "query": {
                    "type": "string",
                    "description": "Customer question used to match a product if ID/name is not extracted.",
                },
            },
        },
    ),
    "get_shipping_policy": ToolSpec(
        name="get_shipping_policy",
        description="Return the effective synthetic shipping policy.",
        handler=get_shipping_policy,
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    "get_return_policy": ToolSpec(
        name="get_return_policy",
        description="Return the effective synthetic return policy.",
        handler=get_return_policy,
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    "aggregate_frequent_questions": ToolSpec(
        name="aggregate_frequent_questions",
        description="Group repeated customer questions using the existing operations aggregation logic.",
        handler=aggregate_frequent_questions_tool,
        parameters={
            "type": "object",
            "properties": {
                "minimum_question_frequency": {
                    "type": "integer",
                    "description": "Minimum count to classify a question as frequent.",
                    "default": 2,
                },
            },
        },
    ),
    "identify_recurring_issues": ToolSpec(
        name="identify_recurring_issues",
        description="Identify recurring complaint and review-worthy issue groups.",
        handler=identify_recurring_issues,
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    "summarize_livestream_session": ToolSpec(
        name="summarize_livestream_session",
        description="Summarize a livestream session using the existing synthetic-session analytics.",
        handler=summarize_livestream_session,
        parameters={
            "type": "object",
            "properties": {
                "minimum_question_frequency": {
                    "type": "integer",
                    "description": "Minimum count to include a repeated question.",
                    "default": 2,
                },
            },
        },
    ),
    "get_order_status": ToolSpec(
        name="get_order_status",
        description="Look up synthetic customer orders by order ID (e.g. ORD-2026-001) or customer handle.",
        handler=get_order_status,
        parameters={
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order ID such as ORD-2026-001.",
                },
                "customer_handle": {
                    "type": "string",
                    "description": "Customer handle such as @minh_anh_99.",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text query mentioning order ID or customer name.",
                },
            },
        },
    ),
}


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI / Ollama / LangChain compatible function-calling tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters or {"type": "object", "properties": {}},
            },
        }
        for spec in AGENT_TOOL_REGISTRY.values()
    ]


def list_agent_tools() -> tuple[str, ...]:
    """Return the stable tool registry names in declaration order."""
    return tuple(AGENT_TOOL_REGISTRY)


def select_tool_for_intent(intent: Intent | str | None) -> str | None:
    """Select the tool for a deterministic intent-to-tool dispatch.

    This project does not simulate native LLM function calling; it uses an
    explicit, reviewable dispatcher to map intents to existing retrieval and
    operations logic.
    """
    if intent is None:
        return None
    normalized = str(intent).strip()
    return INTENT_TO_TOOL.get(normalized, None)


def execute_agent_tool(
    tool_name: str,
    *,
    query: str | None = None,
    product_id: str | None = None,
    product_name: str | None = None,
    comments: Sequence[SimulatedComment | Mapping[str, Any]] | None = None,
    classifications: Sequence[CommentClassification] | None = None,
    events: Iterable[Mapping[str, Any] | Any] | None = None,
    dataset: SyntheticDataset | None = None,
    minimum_question_frequency: int = 2,
) -> ToolResult:
    """Execute one registered tool with validated, explicit arguments."""
    if tool_name not in AGENT_TOOL_REGISTRY:
        raise ValueError(f"Unsupported tool name: {tool_name!r}")
    handler = AGENT_TOOL_REGISTRY[tool_name].handler
    if tool_name == "search_products":
        return handler(query or "", limit=5, dataset=dataset)
    if tool_name == "get_product_inventory":
        return handler(product_id=product_id, product_name=product_name, query=query, dataset=dataset)
    if tool_name == "get_shipping_policy":
        return handler(dataset=dataset)
    if tool_name == "get_return_policy":
        return handler(dataset=dataset)
    if tool_name == "aggregate_frequent_questions":
        return handler(comments or (), classifications=classifications, minimum_frequency=minimum_question_frequency)
    if tool_name == "identify_recurring_issues":
        return handler(comments or (), classifications=classifications)
    if tool_name == "summarize_livestream_session":
        return handler(comments, events, dataset=dataset, minimum_question_frequency=minimum_question_frequency)
    if tool_name == "get_order_status":
        return handler(query=query)
    raise ValueError(f"No argument mapping defined for tool {tool_name!r}")


def run_agent_workflow(
    message: str,
    *,
    dataset: SyntheticDataset | None = None,
    comments: Sequence[SimulatedComment | Mapping[str, Any]] | None = None,
    events: Iterable[Mapping[str, Any] | Any] | None = None,
    minimum_question_frequency: int = 2,
) -> dict[str, Any]:
    """Run the explicit intent-to-tool workflow used by the agent.

    This is a deterministic dispatcher, not native LLM function calling.
    """
    question = _ensure_text(message, field_name="message")
    intent = classify_intent(question, product_names=tuple(product.name for product in (dataset or load_synthetic_data()).products)).intent
    tool_name = select_tool_for_intent(intent)
    if tool_name is None:
        return {
            "intent": intent,
            "tool_name": None,
            "status": "needs_review",
            "evidence": [],
            "reason": "No supported tool matches this intent.",
            "synthetic_data": True,
        }
    tool_result = execute_agent_tool(
        tool_name,
        query=question,
        dataset=dataset,
        comments=comments,
        events=events,
        minimum_question_frequency=minimum_question_frequency,
    )
    return {
        "intent": intent,
        "tool_name": tool_name,
        "status": "ok" if tool_result.ok else "needs_review",
        "evidence": tool_result.data.get("source_refs") or tool_result.data.get("items") or [],
        "error": tool_result.error,
        "result": tool_result.data,
        "synthetic_data": True,
    }
