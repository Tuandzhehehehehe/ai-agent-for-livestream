"""In-memory viewer rate limiting and basic livestream chat moderation."""

import os
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from math import ceil
from typing import Callable, Sequence

DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW_SECONDS = 10
MAX_TRACKED_VIEWERS = 10_000
DEFAULT_BLOCKED_TERMS = (
    "fuck",
    "fucking",
    "shit",
    "bitch",
    "asshole",
    "motherfucker",
    "địt",
    "đụ",
    "lồn",
    "cặc",
    "đĩ",
    "đéo",
)
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ModerationResult:
    allowed: bool
    reason: str | None = None


class ViewerRateLimiter:
    def __init__(
        self,
        *,
        max_comments: int = DEFAULT_RATE_LIMIT,
        window_seconds: int = DEFAULT_RATE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_comments < 1 or window_seconds < 1:
            raise ValueError("Rate limit values must be positive")
        self.max_comments = max_comments
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def consume(self, viewer: str) -> int | None:
        """Return retry seconds when over limit, otherwise record the comment."""
        key = viewer.strip().casefold()
        now = self.clock()
        cutoff = now - self.window_seconds

        with self._lock:
            events = self._events.get(key)
            if events is None:
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)

            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= self.max_comments:
                return max(1, ceil(self.window_seconds - (now - events[0])))

            events.append(now)
            while len(self._events) > MAX_TRACKED_VIEWERS:
                self._events.popitem(last=False)
            return None


class ContentModerator:
    def __init__(
        self,
        *,
        blocked_terms: Sequence[str] | None = None,
        max_links: int = 2,
    ) -> None:
        if max_links < 0:
            raise ValueError("max_links cannot be negative")
        terms = tuple(
            term.strip()
            for term in (*DEFAULT_BLOCKED_TERMS, *(blocked_terms or ()))
            if term.strip()
        )
        self.max_links = max_links
        self._blocked_pattern = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)",
                re.IGNORECASE,
            )
            if terms
            else None
        )

    @classmethod
    def from_environment(cls) -> "ContentModerator":
        configured_terms = os.environ.get("CHAT_BLOCKED_TERMS", "")
        extra_terms = tuple(term for term in configured_terms.split(",") if term.strip())
        return cls(blocked_terms=extra_terms)

    def inspect(self, text: str) -> ModerationResult:
        if self._blocked_pattern and self._blocked_pattern.search(text):
            return ModerationResult(False, "blocked_term")
        if len(_URL_PATTERN.findall(text)) > self.max_links:
            return ModerationResult(False, "excessive_links")
        return ModerationResult(True)


def configured_rate_limiter() -> ViewerRateLimiter:
    max_comments = int(os.environ.get("CHAT_RATE_LIMIT_MAX", DEFAULT_RATE_LIMIT))
    window_seconds = int(
        os.environ.get("CHAT_RATE_LIMIT_WINDOW_SECONDS", DEFAULT_RATE_WINDOW_SECONDS)
    )
    return ViewerRateLimiter(
        max_comments=max_comments,
        window_seconds=window_seconds,
    )