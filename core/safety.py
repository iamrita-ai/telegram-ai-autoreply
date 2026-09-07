"""Rate limiting and anti-ban rails.

This bot types from a **real Telegram account**, and Telegram limits and bans
accounts that behave mechanically: instant replies, identical cadence,
unbounded volume, answering everyone at 4am, repeating the same sentence.
Nothing here is about being polite to the API - it is about the account
surviving.

The protection is deliberately layered, because no single check catches
everything:

===========================  ====================================================
Layer                        What it stops
===========================  ====================================================
in-flight guard              two replies to one chat a second apart
per-chat cooldown            machine-gunning a single conversation
account-wide minimum gap     a burst spread thinly over many chats
per-chat / hourly / daily    sheer volume, the thing reports are counted against
echo-loop guard              ping-pong with another bot or a stuck client
stranger screening           auto-replying to scams, phishing and ad blasts
stranger reply cap           being reportable by somebody you never met
duplicate guard              sending the same text twice (the clearest spam tell)
burst damping                speeding up exactly when you should slow down
quiet-hours damping          human-speed replies at 3am
===========================  ====================================================

Volume caps apply to people the account does *not* know. Saved contacts are
exempt from them (``CONTACT_UNLIMITED``, on by default): a cap exists so the
account cannot spray messages at people who never asked for them, and someone
in your own address book is the opposite of that. Pacing, the duplicate guard,
the echo-loop guard and the in-flight guard still apply to everybody - those
are what actually keep the account alive.

The limiter is intentionally in-memory. Losing the counters on restart is
harmless (the process only restarts occasionally, and the counters are a
safety ceiling rather than an accounting record), and it keeps the hot path
free of database round trips.
"""

from __future__ import annotations

import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from config import settings

__all__ = [
    "Decision",
    "RateLimiter",
    "aggregate_snapshot",
    "limiter_for",
    "normalise",
    "reset_all",
    "screen_stranger_text",
]


# ──────────────────────────────────────────────────────────────────────────
#  Stranger screening
# ──────────────────────────────────────────────────────────────────────────
#: Patterns that make a message from an unknown sender not worth answering.
#: Replying to these is how a personal account ends up reported: the sender
#: is usually a bot farm, and any reply confirms the number is live.
_SCAM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bt\.me/(joinchat|\+)", "invite link"),
    (r"\b(?:https?://)?(?:bit\.ly|tinyurl|cutt\.ly|rb\.gy|shorturl)\b", "shortened link"),
    (
        r"\b(crypto|bitcoin|btc|usdt|eth|binance|forex|trading)\b.{0,40}\b"
        r"(profit|invest|earn|signal|double|guarantee)",
        "investment pitch",
    ),
    (r"\b(earn|make)\b.{0,20}\$?\d{3,}", "money offer"),
    (r"\b(free|claim)\b.{0,20}\b(gift|prize|reward|bonus|airdrop|giveaway)\b", "prize bait"),
    (r"\b(verify|confirm|update)\b.{0,25}\b(account|wallet|password|otp|kyc)\b", "phishing"),
    (r"\b(seed phrase|private key|recovery phrase)\b", "wallet phishing"),
    (r"\b(nude|sexy|hot girls?|dating|escort|onlyfans)\b", "adult spam"),
    (r"\b(loan|casino|betting|jackpot|lottery)\b", "gambling or loan spam"),
    (r"\bwork from home\b|\bpart[- ]time job\b", "job spam"),
    (r"\b(subscribe|join)\b.{0,20}\b(channel|group)\b.{0,20}\b(now|fast|hurry)\b", "channel promo"),
)

_COMPILED_SCAM = tuple((re.compile(p, re.I | re.S), label) for p, label in _SCAM_PATTERNS)

#: A stranger opening with a wall of text is broadcasting, not talking.
_STRANGER_MAX_CHARS = 1200


def screen_stranger_text(text: str) -> str:
    """Return a reason to stay silent, or ``""`` when the message looks fine."""
    if not settings.stranger_screening:
        return ""
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) > _STRANGER_MAX_CHARS:
        return "bulk message"
    for pattern, label in _COMPILED_SCAM:
        if pattern.search(body):
            return label
    # Three or more links from somebody with no history is an advert.
    if len(re.findall(r"https?://|www\.|t\.me/", body, re.I)) >= 3:
        return "link spam"
    return ""


# ──────────────────────────────────────────────────────────────────────────
#  Text normalisation for duplicate detection
# ──────────────────────────────────────────────────────────────────────────
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Fold text so that trivial edits still count as the same message."""
    return _SPACE.sub(" ", _PUNCT.sub("", (text or "").lower())).strip()


def _too_similar(a: str, b: str, threshold: float = 0.92) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # Cheap length gate before paying for the ratio.
    if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


# ──────────────────────────────────────────────────────────────────────────
#  Decision
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Decision:
    """Whether a reply may be sent, and why not when it may not."""

    allowed: bool
    reason: str = ""
    #: Extra seconds to wait before replying, on top of typing simulation.
    extra_delay: float = 0.0
    #: When set, the owner should be told why the bot stayed silent.
    notify_owner: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


# ──────────────────────────────────────────────────────────────────────────
#  Limiter
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class RateLimiter:
    per_chat_cooldown: float = field(default_factory=lambda: settings.per_chat_cooldown)
    per_chat_hourly_limit: int = field(default_factory=lambda: settings.per_chat_hourly_limit)
    global_hourly_limit: int = field(default_factory=lambda: settings.global_hourly_limit)
    global_daily_limit: int = field(default_factory=lambda: settings.global_daily_limit)
    new_chat_extra_delay: float = field(default_factory=lambda: settings.new_chat_extra_delay)
    global_min_gap: float = field(default_factory=lambda: settings.global_min_gap)

    def __post_init__(self) -> None:
        self._last_reply: dict[int, float] = {}
        self._chat_hits: dict[int, deque[float]] = defaultdict(deque)
        self._global_hits: deque[float] = deque()
        self._known_chats: set[int] = set()
        self._in_flight: set[int] = set()
        self._day_hits: deque[float] = deque()
        self._last_send: float = 0.0
        #: chat -> recent outgoing normalised texts, for duplicate detection.
        self._sent_texts: dict[int, deque[tuple[float, str]]] = defaultdict(deque)
        #: chat -> (normalised incoming text, consecutive count).
        self._last_incoming: dict[int, tuple[str, int]] = {}
        #: stranger chat -> replies sent so far.
        self._stranger_replies: dict[int, int] = defaultdict(int)
        self._blocked_reasons: deque[tuple[float, str]] = deque(maxlen=50)

    # -- queries -----------------------------------------------------------
    def check(
        self,
        chat_id: int,
        *,
        now: float | None = None,
        is_stranger: bool = False,
        is_contact: bool = False,
        incoming_text: str | None = None,
        quiet_hours: bool = False,
    ) -> Decision:
        """May the bot reply in ``chat_id`` right now?

        ``is_contact`` marks somebody in the signed-in account's own contact
        list. Those conversations are not capped or cooled down - see
        ``CONTACT_UNLIMITED`` - because a volume ceiling protects strangers
        from the account, not the account from its own friends.
        """
        now = time.monotonic() if now is None else now
        self._expire(now)

        unlimited = bool(is_contact and settings.contact_unlimited and not is_stranger)

        if chat_id in self._in_flight and settings.drop_while_replying:
            # A reply is already being composed for this chat. Answering the
            # follow-up too would produce two messages a second apart, which
            # is the most obvious bot tell there is. This applies to contacts
            # too: it prevents double-sends, it is not a volume limit.
            return self._deny("already replying in this chat")

        if not unlimited:
            last = self._last_reply.get(chat_id)
            if last is not None and now - last < self.per_chat_cooldown:
                return self._deny("per-chat cooldown")

            if len(self._chat_hits[chat_id]) >= self.per_chat_hourly_limit:
                return self._deny("per-chat hourly limit reached")

            if len(self._global_hits) >= self.global_hourly_limit:
                return self._deny("global hourly limit reached")

            if len(self._day_hits) >= self.global_daily_limit:
                return self._deny("global daily limit reached")

        # Ping-pong guard: the same text arriving over and over is another
        # bot, a stuck client, or somebody testing. Answering every time is
        # an unbounded loop that reads as spam from Telegram's side.
        if incoming_text is not None:
            key = normalise(incoming_text)
            seen, count = self._last_incoming.get(chat_id, ("", 0))
            if key and key == seen and count >= settings.echo_loop_threshold:
                return self._deny(
                    f"the same message repeated {count}x - not answering again",
                    notify=f"Ignoring a repeated message in chat {chat_id} (loop guard).",
                )

        if is_stranger:
            if incoming_text:
                flagged = screen_stranger_text(incoming_text)
                if flagged:
                    return self._deny(
                        f"stranger message looks like {flagged}",
                        notify=(
                            f"🛡 Blocked an auto-reply to an unknown sender "
                            f"(chat {chat_id}) - looks like {flagged}."
                        ),
                    )
            if self._stranger_replies[chat_id] >= settings.stranger_max_replies:
                return self._deny(
                    "stranger reply cap reached",
                    notify=(
                        f"🛡 Reached the {settings.stranger_max_replies}-reply limit "
                        f"for an unknown sender (chat {chat_id}). "
                        f"Reply yourself if you know them."
                    ),
                )

        # Account-wide pacing: Telegram's heuristics look at the account, so
        # ten chats answered in ten seconds is still a burst. Contacts get a
        # much smaller gap - a real conversation with a friend does not have
        # eight seconds of dead air before every message - but not zero,
        # because Telegram's flood limits do not care who you are talking to.
        min_gap = (
            min(self.global_min_gap, settings.contact_min_gap)
            if unlimited
            else (self.global_min_gap)
        )
        gap = now - self._last_send if self._last_send else None
        extra = 0.0
        if gap is not None and gap < min_gap:
            extra += min_gap - gap

        # First contact with this chat: add a longer, more human pause.
        if chat_id not in self._known_chats:
            first_touch = random.uniform(
                self.new_chat_extra_delay * 0.6, self.new_chat_extra_delay * 1.4
            )
            extra += first_touch * 0.34 if unlimited else first_touch
        if is_stranger:
            extra += random.uniform(
                settings.stranger_extra_delay * 0.7, settings.stranger_extra_delay * 1.3
            )

        # Burst damping: the busier the last 10 minutes were, the slower the
        # account gets. This is self-correcting and needs no tuning. An
        # active chat with a contact is damped far more gently, otherwise a
        # normal back-and-forth would grind to a halt after ten messages.
        recent = sum(1 for hit in self._global_hits if hit > now - 600)
        if recent:
            penalty = min(recent * settings.burst_delay_step, settings.burst_delay_max)
            extra += min(penalty * 0.15, 4.0) if unlimited else penalty

        if quiet_hours:
            extra *= settings.night_delay_factor

        return Decision(True, extra_delay=round(extra, 2))

    def allow_text(self, chat_id: int, text: str, *, now: float | None = None) -> Decision:
        """Would sending ``text`` to ``chat_id`` be a repeat?

        Called immediately before sending, so it also catches the case where
        the model independently produced the same sentence twice.
        """
        now = time.monotonic() if now is None else now
        key = normalise(text)
        if not key:
            return Decision(False, "empty message")
        cutoff = now - settings.duplicate_window
        for stamp, seen in self._sent_texts.get(chat_id, ()):
            if stamp >= cutoff and _too_similar(key, seen):
                return self._deny("duplicate of a recent message")
        return Decision(True)

    # -- bookkeeping -------------------------------------------------------
    def hold(self, chat_id: int) -> None:
        """Mark a chat as mid-reply."""
        self._in_flight.add(chat_id)

    def release(self, chat_id: int) -> None:
        self._in_flight.discard(chat_id)

    def note_incoming(self, chat_id: int, text: str) -> int:
        """Track consecutive identical incoming messages. Returns the count."""
        key = normalise(text)
        seen, count = self._last_incoming.get(chat_id, ("", 0))
        count = count + 1 if key and key == seen else 1
        self._last_incoming[chat_id] = (key, count)
        return count

    def record(
        self,
        chat_id: int,
        *,
        now: float | None = None,
        text: str | None = None,
        is_stranger: bool = False,
    ) -> None:
        """Record that a reply was actually sent."""
        now = time.monotonic() if now is None else now
        self._last_reply[chat_id] = now
        self._last_send = now
        self._chat_hits[chat_id].append(now)
        self._global_hits.append(now)
        self._known_chats.add(chat_id)
        self._day_hits.append(now)
        if is_stranger:
            self._stranger_replies[chat_id] += 1
        if text:
            history = self._sent_texts[chat_id]
            history.append((now, normalise(text)))
            while len(history) > settings.duplicate_memory:
                history.popleft()

    def trust(self, chat_id: int) -> None:
        """Stop treating a chat as a stranger (the owner vouched for it)."""
        self._stranger_replies.pop(chat_id, None)
        self._known_chats.add(chat_id)

    # -- internals ---------------------------------------------------------
    def _deny(self, reason: str, *, notify: str = "") -> Decision:
        self._blocked_reasons.append((time.time(), reason))
        return Decision(False, reason, notify_owner=notify)

    def _expire(self, now: float) -> None:
        hour_ago = now - 3600
        day_ago = now - 86400
        dup_ago = now - settings.duplicate_window
        while self._global_hits and self._global_hits[0] < hour_ago:
            self._global_hits.popleft()
        while self._day_hits and self._day_hits[0] < day_ago:
            self._day_hits.popleft()
        for chat_id, hits in list(self._chat_hits.items()):
            while hits and hits[0] < hour_ago:
                hits.popleft()
            if not hits:
                del self._chat_hits[chat_id]
        for chat_id, texts in list(self._sent_texts.items()):
            while texts and texts[0][0] < dup_ago:
                texts.popleft()
            if not texts:
                del self._sent_texts[chat_id]

    # -- introspection -----------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        self._expire(now)
        recent = sum(1 for hit in self._global_hits if hit > now - 600)
        return {
            "replies_last_hour": len(self._global_hits),
            "replies_last_day": len(self._day_hits),
            "active_chats_this_hour": len(self._chat_hits),
            "strangers_answered": len(self._stranger_replies),
            "current_burst_penalty_s": round(
                min(recent * settings.burst_delay_step, settings.burst_delay_max), 1
            ),
            "recently_blocked": [reason for _, reason in list(self._blocked_reasons)[-5:]],
            "limits": {
                "per_chat_cooldown_s": self.per_chat_cooldown,
                "global_min_gap_s": self.global_min_gap,
                "per_chat_hourly": self.per_chat_hourly_limit,
                "global_hourly": self.global_hourly_limit,
                "global_daily": self.global_daily_limit,
                "stranger_max_replies": settings.stranger_max_replies,
                "contacts_exempt_from_limits": settings.contact_unlimited,
            },
        }

    def reset(self) -> None:
        """Drop all counters (used by tests and by /limits reset)."""
        self.__post_init__()


# ──────────────────────────────────────────────────────────────────────────
#  One limiter per signed-in account
# ──────────────────────────────────────────────────────────────────────────
# The bot is multi-user, and Telegram rate-limits each *account* separately.
# A shared limiter would mean one busy user throttling everybody else, and
# two users talking to the same contact id would collide in the same bucket.
_limiters: dict[int, RateLimiter] = {}


def limiter_for(owner: int) -> RateLimiter:
    """The rate limiter for one owner's account, created on first use."""
    instance = _limiters.get(owner)
    if instance is None:
        instance = _limiters[owner] = RateLimiter()
    return instance


def forget_limiter(owner: int) -> None:
    _limiters.pop(owner, None)


def aggregate_snapshot() -> dict[str, object]:
    """Totals across every account, for the health endpoint."""
    snapshots = [limiter.snapshot() for limiter in _limiters.values()]
    return {
        "accounts": len(snapshots),
        "replies_last_hour": sum(int(s["replies_last_hour"]) for s in snapshots),
        "replies_last_day": sum(int(s["replies_last_day"]) for s in snapshots),
        "strangers_answered": sum(int(s["strangers_answered"]) for s in snapshots),
    }


def reset_all() -> None:
    _limiters.clear()
