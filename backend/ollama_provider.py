"""Small local Ollama client for evidence-grounded answer drafts."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib import error, request

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OllamaProvider:
    """Thin wrapper around the local Ollama HTTP API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.enabled = enabled if enabled is not None else _env_flag("OLLAMA_ENABLED", False)
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or os.getenv("OLLAMA_MODEL") or "").strip()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else _env_float("OLLAMA_TIMEOUT_SECONDS", 30.0)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.model)

    def _json_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError("Ollama is disabled or no model is configured.")

        url = f"{self.base_url}/api/chat"
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Ollama request failed: %s", exc)
            raise RuntimeError(f"Ollama unavailable: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.warning("Ollama returned invalid JSON: %s", exc)
            raise RuntimeError("Ollama returned invalid JSON.") from exc

        if isinstance(parsed, dict) and parsed.get("error"):
            raise RuntimeError(f"Ollama error: {parsed['error']}")
        return parsed

    def generate_answer(self, question: str, *, evidence: tuple[str, ...]) -> str | None:
        """Return a concise answer grounded only in the supplied evidence."""
        if not self.is_configured:
            return None
        if not question or not evidence:
            return None

        evidence_block = "\n\n".join(f"[{index}] {item}" for index, item in enumerate(evidence, start=1))
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a support assistant for a synthetic livestream demo. "
                        "Answer only from the supplied evidence. If the evidence is insufficient or ambiguous, "
                        "say that you are not certain and ask an operator to review. Do not invent prices, stock, "
                        "promotions, policies, shipments, or refunds. Never claim that a real action is performed."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\nEvidence:\n{evidence_block}\n\n"
                        "Reply in plain language with a short answer grounded only in the evidence. "
                        "If the evidence is missing, say that the information is uncertain and needs review."
                    ),
                },
            ],
        }

        try:
            response = self._json_post(payload)
        except RuntimeError:
            return None

        message = response.get("message") if isinstance(response, dict) else None
        if isinstance(message, dict):
            content = message.get("content", "")
        else:
            content = response.get("response") if isinstance(response, dict) else None

        if not isinstance(content, str):
            logger.warning("Ollama response did not contain assistant content: %s", response)
            return None

        cleaned = content.strip()
        if not cleaned:
            return None
        return cleaned


def get_ollama_provider() -> OllamaProvider:
    return OllamaProvider()


def generate_answer_with_ollama(question: str, *, evidence: tuple[str, ...]) -> str | None:
    provider = get_ollama_provider()
    return provider.generate_answer(question, evidence=evidence)
