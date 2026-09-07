"""Rate limiting and anti-ban rails.

This bot types from a **real Telegram account**, and Telegram limits and bans
accounts that behave mechanically: instant replies, identical cadence,
unbounded volume, answering everyone at 4am. Nothing here is about being
polite to the API - it is about the account surviving.

The limiter is intentionally in-memory. Losing the counters on restart is
harmless (the process only restarts occasionally, and the counters are a
safety ceiling rather than an accounting record), and it keeps the hot path
free of database round trips.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from config import settings

__all__ = ["Decision", "RateLimiter", "limiter"]


@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a reply may be sent, and why not when it may not."""

    allowed: bool
    reason: str = ""
    #: Extra seconds to wait before replying, on top of typing simulation.
    extra_delay: float = 0.0

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


@dataclass
class RateLimiter:
    per_chat_cooldown: float = field(default_factory=lambda: settings.per_chat_cooldown)
    per_chat_hourly_limit: int = field(default_factory=lambda: settings.per_chat_hourly_limit)
    global_hourly_limit: int = field(default_factory=lambda: settings.global_hourly_limit)
    global_daily_limit: int = field(default_factory=lambda: settings.global_daily_limit)
    new_chat_extra_delay: float = field(default_factory=lambda: settings.new_chat_extra_delay)

    def __post_init__(self) -> None:
        self._last_reply: dict[int, float] = {}
        self._chat_hits: dict[int, deque[float]] = defaultdict(deque)
        self._global_hits: deque[float] = deque()
        self._known_chats: set[int] = set()
        self._in_flight: set[int] = set()
        self._day_hits: deque[float] = deque()

    # -- queries -----------------------------------------------------------
    def check(self, chat_id: int, *, now: float | None = None) -> Decision:
        """May the bot reply in ``chat_id`` right now?"""
        now = time.monotonic() if now is None else now
        self._expire(now)

        if chat_id in self._in_flight and settings.drop_while_replying:
            # A reply is already being composed for this chat. Answering the
            # follow-up too would produce two messages a second apart, which
            # is the most obvious bot tell there is.
            return Decision(False, "already replying in this chat")

        last = self._last_reply.get(chat_id)
        if last is not None and now - last < self.per_chat_cooldown:
            return Decision(False, "per-chat cooldown")

        if len(self._chat_hits[chat_id]) >= self.per_chat_hourly_limit:
            return Decision(False, "per-chat hourly limit reached")

        if len(self._global_hits) >= self.global_hourly_limit:
            return Decision(False, "global hourly limit reached")

        if self._today_count(now) >= self.global_daily_limit:
            return Decision(False, "global daily limit reached")

        # First contact with this chat: add a longer, more human pause.
        extra = 0.0
        if chat_id not in self._known_chats:
            extra = random.uniform(self.new_chat_extra_delay * 0.6, self.new_chat_extra_delay * 1.4)
        return Decision(True, extra_delay=extra)

    # -- bookkeeping -------------------------------------------------------
    def hold(self, chat_id: int) -> None:
        """Mark a chat as mid-reply."""
        self._in_flight.add(chat_id)

    def release(self, chat_id: int) -> None:
        self._in_flight.discard(chat_id)

    def record(self, chat_id: int, *, now: float | None = None) -> None:
        """Record that a reply was actually sent."""
        now = time.monotonic() if now is None else now
        self._last_reply[chat_id] = now
        self._chat_hits[chat_id].append(now)
        self._global_hits.append(now)
        self._known_chats.add(chat_id)
        self._day_hits.append(now)

    # -- internals ---------------------------------------------------------
    def _expire(self, now: float) -> None:
        hour_ago = now - 3600
        day_ago = now - 86400
        while self._global_hits and self._global_hits[0] < hour_ago:
            self._global_hits.popleft()
        while self._day_hits and self._day_hits[0] < day_ago:
            self._day_hits.popleft()
        for chat_id, hits in list(self._chat_hits.items()):
            while hits and hits[0] < hour_ago:
                hits.popleft()
            if not hits:
                del self._chat_hits[chat_id]

    def _today_count(self, now: float) -> int:
        return len(self._day_hits)

    # -- introspection -----------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        self._expire(now)
        return {
            "replies_last_hour": len(self._global_hits),
            "replies_last_day": len(self._day_hits),
            "active_chats_this_hour": len(self._chat_hits),
            "limits": {
                "per_chat_cooldown_s": self.per_chat_cooldown,
                "per_chat_hourly": self.per_chat_hourly_limit,
                "global_hourly": self.global_hourly_limit,
                "global_daily": self.global_daily_limit,
            },
        }

    def reset(self) -> None:
        """Drop all counters (used by tests and by /limits reset)."""
        self.__post_init__()


limiter = RateLimiter()
