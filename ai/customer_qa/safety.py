"""Small deterministic checks for common instruction-injection attempts."""

import re

MAX_CUSTOMER_MESSAGE_LENGTH = 4096

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override or ignore rules",
        re.compile(
            r"\b(?:ignore|forget|disregard|override|bypass)\s+"
            r"(?:(?:all|the|these|your|previous|prior|above|earlier)\s+)*"
            r"(?:rules|instructions|policies|guardrails)\b",
            re.I,
        ),
    ),
    (
        "make up a discount",
        re.compile(r"\bmake\s+up\s+(?:a\s+)?(?:discount|price|policy|fact)\b", re.I),
    ),
    (
        "pretend a refund was completed",
        re.compile(r"\bpretend\b.{0,60}\brefund\b.{0,30}\b(?:completed|processed|issued)\b", re.I),
    ),
    (
        "reveal hidden instructions",
        re.compile(
            r"\b(?:reveal|show|print|display|repeat|share|tell me)\b"
            r".{0,60}\b(?:internal|system|hidden|private)\b"
            r".{0,30}\b(?:instructions|prompt|rules)\b",
            re.I,
        ),
    ),
)


def detect_prompt_injection(message: str) -> tuple[str, ...]:
    """Return matched common injection patterns; this is not a full detector."""
    if not isinstance(message, str):
        raise TypeError("message must be a string")
    if len(message) > MAX_CUSTOMER_MESSAGE_LENGTH:
        return ("message exceeds safety input limit",)
    return tuple(label for label, pattern in _INJECTION_PATTERNS if pattern.search(message))