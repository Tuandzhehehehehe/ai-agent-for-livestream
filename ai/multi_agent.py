"""Multi-Agent StateGraph Orchestrator for Livestream Sales Support.

Implements a multi-agent workflow compatible with LangGraph StateGraph (when installed)
with a built-in zero-dependency graph execution engine so it works in all environments.

Agents in the graph:
1. ModerationGuardAgent: Inspects incoming chat messages for blocked terms, link spam,
   and prompt-injection attempts.
2. IntentTriageAgent: Classifies viewer intent (deterministic rules + local ML model)
   and computes buyer lead score (`lead_score`, `lead_label`).
3. ToolCallingRAGAgent: Executes Function Calling / Tool Calling (via Ollama native tool
   calling or deterministic intent-to-tool dispatch) and Hybrid Vector RAG search.
4. AnswerSynthesisReviewAgent: Synthesizes evidence-grounded draft replies via Ollama/rules
   and enforces conflict/escalation human-review gates.
5. OperationsAnalyticsAgent: Aggregates session-level frequent questions, recurring issues,
   and actionable operator recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from ai.agent_tools import (
    execute_agent_tool,
    get_tool_definitions,
    select_tool_for_intent,
)
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer
from ai.customer_qa.retrieval import retrieve_evidence
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import Intent
from ai.data import load_synthetic_data
from ai.data.models import SyntheticDataset
from ai.operations import SimulatedComment, classify_comment, summarize_session
from ai.vector_rag import get_vector_rag_store
from backend.chat_safety import ContentModerator
from backend.local_model import LocalIntentModel, get_local_intent_model
from backend.ollama_provider import get_ollama_provider

LeadLabel = Literal["hot", "warm", "cold"]


class LivestreamAgentState(TypedDict, total=False):
    session_id: str
    comment_id: str
    sender: str
    author_type: str
    text: str
    moderation_allowed: bool
    moderation_reason: str | None
    intent: Intent
    intent_source: Literal["rules", "local_model", "llm_tool"]
    intent_confidence: float | None
    lead_score: int
    lead_label: LeadLabel
    selected_tool: str | None
    tool_arguments: dict[str, Any]
    tool_output: dict[str, Any] | None
    vector_rag_hits: list[dict[str, Any]]
    answer_draft: dict[str, Any] | None
    needs_human_review: bool
    review_reason: str | None
    agent_trace: list[dict[str, Any]]


@dataclass(slots=True)
class MultiAgentExecutionResult:
    state: LivestreamAgentState
    orchestrator_backend: Literal["langgraph", "builtin_stategraph"] = "builtin_stategraph"
    vector_backend: str = "local_vector"


def compute_lead_score(text: str, intent: Intent) -> tuple[int, LeadLabel]:
    """Compute buyer purchase-intent lead score (0-100) and label (hot/warm/cold)."""
    base_scores: dict[Intent, int] = {
        "price_question": 82,
        "stock_question": 85,
        "promotion_question": 78,
        "product_information": 70,
        "shipping_policy": 65,
        "return_policy": 55,
        "price_change_request": 60,
        "refund_request": 30,
        "consequential_action": 40,
        "complaint": 25,
        "unknown": 15,
        "spam_or_irrelevant": 0,
        "prompt_injection": 0,
    }
    score = base_scores.get(intent, 15)
    lowered = text.casefold()
    high_intent_keywords = (
        "buy",
        "order",
        "checkout",
        "mua",
        "chốt",
        "đặt hàng",
        "còn hàng",
        "in stock",
        "how much",
        "giá bao nhiêu",
        "ship",
    )
    bonus = sum(4 for kw in high_intent_keywords if kw in lowered)
    final_score = max(0, min(100, score + bonus))
    if final_score >= 75:
        return final_score, "hot"
    if final_score >= 45:
        return final_score, "warm"
    return final_score, "cold"


class MultiAgentOrchestrator:
    """Multi-Agent workflow coordinator supporting LangGraph StateGraph and built-in graph execution."""

    def __init__(
        self,
        *,
        dataset: SyntheticDataset | None = None,
        intent_model: LocalIntentModel | None = None,
        moderator: ContentModerator | None = None,
    ) -> None:
        self.dataset = dataset or load_synthetic_data()
        self.intent_model = intent_model or get_local_intent_model()
        self.moderator = moderator or ContentModerator.from_environment()
        self.vector_store = get_vector_rag_store(self.dataset)
        self._langgraph_app: Any = None
        self._orchestrator_backend: Literal["langgraph", "builtin_stategraph"] = "builtin_stategraph"
        self._try_compile_langgraph()

    def _try_compile_langgraph(self) -> None:
        try:
            from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]

            workflow = StateGraph(LivestreamAgentState)
            workflow.add_node("moderation_guard_agent", self._node_moderation_guard)
            workflow.add_node("intent_triage_agent", self._node_intent_triage)
            workflow.add_node("tool_calling_rag_agent", self._node_tool_calling_rag)
            workflow.add_node("answer_synthesis_review_agent", self._node_answer_synthesis_review)

            workflow.set_entry_point("moderation_guard_agent")
            workflow.add_conditional_edges(
                "moderation_guard_agent",
                lambda s: "blocked" if not s.get("moderation_allowed", True) else "allowed",
                {
                    "blocked": END,
                    "allowed": "intent_triage_agent",
                },
            )
            workflow.add_edge("intent_triage_agent", "tool_calling_rag_agent")
            workflow.add_edge("tool_calling_rag_agent", "answer_synthesis_review_agent")
            workflow.add_edge("answer_synthesis_review_agent", END)
            self._langgraph_app = workflow.compile()
            self._orchestrator_backend = "langgraph"
        except Exception:
            self._langgraph_app = None
            self._orchestrator_backend = "builtin_stategraph"

    @property
    def architecture_summary(self) -> dict[str, Any]:
        ollama = get_ollama_provider()
        return {
            "orchestrator_backend": self._orchestrator_backend,
            "vector_rag_backend": self.vector_store.backend,
            "vector_Indexed_documents": self.vector_store.document_count,
            "ollama_enabled": ollama.enabled,
            "ollama_model": ollama.model or "qwen2.5:3b-instruct (configurable)",
            "agents": [
                {
                    "id": "moderation_guard_agent",
                    "name": "Moderation & Safety Guard Agent",
                    "role": "Filters profanity, link spam, and untrusted prompt injection payloads.",
                },
                {
                    "id": "intent_triage_agent",
                    "name": "Intent & Lead Triage Agent",
                    "role": "Classifies comment intent (Rules + Local ML Model) and scores buyer lead priority.",
                },
                {
                    "id": "tool_calling_rag_agent",
                    "name": "Tool-Calling & Vector RAG Agent",
                    "role": "Dispatches function calls (catalog, inventory, policies) and retrieves semantic vector hits.",
                },
                {
                    "id": "answer_synthesis_review_agent",
                    "name": "Answer Synthesis & Review Gate Agent",
                    "role": "Synthesizes grounded draft replies via Ollama/rules and flags conflicts for human operators.",
                },
                {
                    "id": "operations_analytics_agent",
                    "name": "Livestream Operations & Analytics Agent",
                    "role": "Aggregates frequent questions, recurring issues, and session-level recommendations.",
                },
            ],
            "edges": [
                {"from": "moderation_guard_agent", "to": "intent_triage_agent", "condition": "moderation_allowed == true"},
                {"from": "intent_triage_agent", "to": "tool_calling_rag_agent", "condition": "always"},
                {"from": "tool_calling_rag_agent", "to": "answer_synthesis_review_agent", "condition": "always"},
            ],
            "tools": [spec["function"]["name"] for spec in get_tool_definitions()],
        }

    def _node_moderation_guard(self, state: LivestreamAgentState) -> LivestreamAgentState:
        text = state.get("text", "")
        trace = list(state.get("agent_trace", []))
        inspection = self.moderator.inspect(text)
        if not inspection.allowed:
            trace.append(
                {
                    "agent": "ModerationGuardAgent",
                    "action": "block_comment",
                    "detail": f"Blocked by content moderation ({inspection.reason}).",
                }
            )
            return {
                **state,
                "moderation_allowed": False,
                "moderation_reason": inspection.reason,
                "intent": "spam_or_irrelevant",
                "intent_source": "rules",
                "lead_score": 0,
                "lead_label": "cold",
                "needs_human_review": True,
                "review_reason": "Comment blocked by chat moderation.",
                "agent_trace": trace,
            }

        trace.append(
            {
                "agent": "ModerationGuardAgent",
                "action": "allow_comment",
                "detail": "Passed safety and link-spam checks.",
            }
        )
        return {
            **state,
            "moderation_allowed": True,
            "moderation_reason": None,
            "agent_trace": trace,
        }

    def _node_intent_triage(self, state: LivestreamAgentState) -> LivestreamAgentState:
        text = state.get("text", "")
        comment_id = state.get("comment_id", "c-1")
        trace = list(state.get("agent_trace", []))

        classification = classify_comment(
            SimulatedComment(comment_id=comment_id, text=text),
            product_names=tuple(p.name for p in self.dataset.products),
        )
        intent: Intent = classification.intent
        intent_source: Literal["rules", "local_model", "llm_tool"] = "rules"
        intent_confidence: float | None = None
        needs_review = classification.needs_human_review
        review_reason = classification.reason

        if intent == "unknown" and len(text) <= MAX_CUSTOMER_MESSAGE_LENGTH:
            prediction = self.intent_model.predict(text)
            intent_confidence = prediction.confidence
            if prediction.intent != "unknown":
                intent = prediction.intent
                intent_source = "local_model"

        lead_score, lead_label = compute_lead_score(text, intent)
        trace.append(
            {
                "agent": "IntentTriageAgent",
                "action": "classify_and_score",
                "detail": f"Intent={intent} (source={intent_source}), Lead={lead_label} ({lead_score}/100).",
            }
        )
        return {
            **state,
            "intent": intent,
            "intent_source": intent_source,
            "intent_confidence": intent_confidence,
            "lead_score": lead_score,
            "lead_label": lead_label,
            "needs_human_review": needs_review,
            "review_reason": review_reason,
            "agent_trace": trace,
        }

    def _node_tool_calling_rag(self, state: LivestreamAgentState) -> LivestreamAgentState:
        text = state.get("text", "")
        intent: Intent = state.get("intent", "unknown")
        trace = list(state.get("agent_trace", []))

        ollama = get_ollama_provider()
        tool_name = select_tool_for_intent(intent)
        tool_args: dict[str, Any] = {"query": text}

        if ollama.is_configured and intent in {
            "shipping_policy",
            "return_policy",
            "promotion_question",
            "stock_question",
            "price_question",
            "product_information",
            "unknown",
        }:
            llm_tool = ollama.select_tool_call(text, tools=get_tool_definitions())
            if llm_tool and llm_tool.get("name"):
                tool_name = llm_tool["name"]
                tool_args = {**tool_args, **(llm_tool.get("arguments") or {})}

        tool_output: dict[str, Any] | None = None
        if tool_name:
            res = execute_agent_tool(
                tool_name,
                query=str(tool_args.get("query") or text),
                product_id=tool_args.get("product_id"),
                product_name=tool_args.get("product_name"),
                dataset=self.dataset,
            )
            tool_output = {
                "tool_name": res.name,
                "ok": res.ok,
                "data": res.data,
                "error": res.error,
            }

        vector_hits = self.vector_store.search(text, top_k=4)
        vector_payload = [
            {
                "source_type": hit.source_type,
                "source_id": hit.source_id,
                "title": hit.title,
                "excerpt": hit.excerpt,
                "score": hit.score,
                "backend": hit.backend,
            }
            for hit in vector_hits
        ]

        trace.append(
            {
                "agent": "ToolCallingRAGAgent",
                "action": "tool_and_vector_retrieval",
                "detail": (
                    f"Executed tool={tool_name or 'none'} "
                    f"(ok={tool_output['ok'] if tool_output else False}), "
                    f"retrieved {len(vector_payload)} vector RAG chunks ({self.vector_store.backend})."
                ),
            }
        )
        return {
            **state,
            "selected_tool": tool_name,
            "tool_arguments": tool_args,
            "tool_output": tool_output,
            "vector_rag_hits": vector_payload,
            "agent_trace": trace,
        }

    def _node_answer_synthesis_review(self, state: LivestreamAgentState) -> LivestreamAgentState:
        text = state.get("text", "")
        session_id = state.get("session_id", "youtube-live")
        comment_id = state.get("comment_id", "c-1")
        intent = state.get("intent", "unknown")
        intent_source = state.get("intent_source", "rules")
        trace = list(state.get("agent_trace", []))

        draft = draft_customer_answer(
            CustomerQuestionRequest(
                session_id=session_id,
                comment_id=comment_id,
                text=text,
            ),
            dataset=self.dataset,
            intent_override=intent if intent_source == "local_model" else None,
        )

        final_intent = draft.intent if intent == "unknown" and draft.intent != "unknown" else intent
        lead_score, lead_label = compute_lead_score(text, final_intent)

        draft_dict = {
            "answer": draft.answer,
            "intent": draft.intent,
            "status": draft.status,
            "needs_human_review": draft.needs_human_review,
            "reason": draft.reason,
            "is_draft": draft.is_draft,
            "sources": [
                {
                    "source_type": s.source_type,
                    "source_id": s.source_id,
                    "title": s.title,
                    "excerpt": s.excerpt,
                }
                for s in draft.sources
            ],
        }
        trace.append(
            {
                "agent": "AnswerSynthesisReviewAgent",
                "action": "synthesize_and_verify",
                "detail": (
                    f"Status={draft.status}, needs_human_review={draft.needs_human_review}, "
                    f"sources={len(draft.sources)}."
                ),
            }
        )
        return {
            **state,
            "intent": final_intent,
            "lead_score": lead_score,
            "lead_label": lead_label,
            "answer_draft": draft_dict,
            "needs_human_review": draft.needs_human_review,
            "review_reason": draft.reason,
            "agent_trace": trace,
        }

    def run(
        self,
        *,
        text: str,
        session_id: str = "youtube-live",
        comment_id: str = "comment-1",
        sender: str = "Viewer",
        author_type: str = "viewer",
    ) -> MultiAgentExecutionResult:
        initial_state: LivestreamAgentState = {
            "session_id": session_id,
            "comment_id": comment_id,
            "sender": sender,
            "author_type": author_type,
            "text": text,
            "agent_trace": [],
        }
        if self._langgraph_app is not None:
            final_state = self._langgraph_app.invoke(initial_state)
        else:
            state = self._node_moderation_guard(initial_state)
            if state.get("moderation_allowed", True):
                state = self._node_intent_triage(state)
                state = self._node_tool_calling_rag(state)
                state = self._node_answer_synthesis_review(state)
            final_state = state

        return MultiAgentExecutionResult(
            state=final_state,
            orchestrator_backend=self._orchestrator_backend,
            vector_backend=self.vector_store.backend,
        )


_DEFAULT_ORCHESTRATOR: MultiAgentOrchestrator | None = None


def get_multi_agent_orchestrator() -> MultiAgentOrchestrator:
    global _DEFAULT_ORCHESTRATOR
    if _DEFAULT_ORCHESTRATOR is None:
        _DEFAULT_ORCHESTRATOR = MultiAgentOrchestrator()
    return _DEFAULT_ORCHESTRATOR
