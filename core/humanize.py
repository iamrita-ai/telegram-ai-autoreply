"""Human-like behaviour: typing rhythm, reactions, quiet hours.

An auto-reply that lands in 200 ms with a perfectly even cadence is what
gets an account flagged. Everything here exists to make the account behave
like a person holding a phone.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
from dataclasses import dataclass

from config import settings

log = logging.getLogger(__name__)

__all__ = [
    "deserves_big_reaction",
    "detect_sentiment",
    "is_quiet_hours",
    "send_reaction",
    "simulate_typing",
    "typing_plan",
]

_POSITIVE = frozenset(
    [
        "good",
        "great",
        "awesome",
        "love",
        "happy",
        "thanks",
        "thank",
        "nice",
        "wow",
        "amazing",
        "perfect",
        "excellent",
        "brilliant",
        "congrats",
        "lovely",
        "beautiful",
        "glad",
        "cool",
        "yay",
    ]
)
_NEGATIVE = frozenset(
    [
        "bad",
        "hate",
        "sad",
        "angry",
        "worst",
        "problem",
        "boring",
        "terrible",
        "awful",
        "horrible",
        "annoyed",
        "upset",
        "sorry",
        "unfortunately",
        "hurt",
        "tired",
        "sick",
    ]
)

POSITIVE_REACTIONS = ("❤️", "🔥", "👍", "😍", "🤩", "💯", "🎉", "✨")
NEGATIVE_REACTIONS = ("😢", "💔", "😮", "🫂")
NEUTRAL_REACTIONS = ("👍", "👀", "🤔", "😂")

#: Words that mean the message is an event, not small talk. Telegram plays
#: the full-screen animation for a "big" reaction, and that is worth doing
#: for news worth celebrating, not for "ok".
_BIG_MOMENTS = frozenset(
    [
        "congrats",
        "congratulations",
        "engaged",
        "married",
        "wedding",
        "birthday",
        "promoted",
        "promotion",
        "selected",
        "passed",
        "cleared",
        "won",
        "winner",
        "hired",
        "job",
        "offer",
        "graduated",
        "topper",
        "first",
        "accepted",
        "approved",
        "finally",
        "launched",
        "shipped",
        "baby",
        "anniversary",
        "love",
        "miss",
        "proud",
    ]
)


def deserves_big_reaction(text: str) -> bool:
    """Is this a moment, rather than a message?

    Telegram's big reaction is an animation that takes over the screen. Used
    on everything it is noise, and noise from an account that replies to
    everything is exactly the pattern that gets reported. Used on the two
    or three messages a week that actually deserve it, it reads as a person
    paying attention.
    """
    body = (text or "").strip()
    if not body or len(body) > 300:
        return False
    words = {w.strip(".,!?;:'\"").lower() for w in body.split()}
    if words & _BIG_MOMENTS:
        return True
    if body.count("!") >= 3:
        return True
    # SHOUTING is either excitement or a crisis, and both are big.
    letters = [c for c in body if c.isalpha()]
    return len(letters) >= 6 and all(c.isupper() for c in letters)


def detect_sentiment(text: str) -> str:
    words = {w.strip(".,!?;:'\"").lower() for w in (text or "").split()}
    positive = len(words & _POSITIVE)
    negative = len(words & _NEGATIVE)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


@dataclass(frozen=True, slots=True)
class TypingPlan:
    """How long each stage of answering should take, in seconds."""

    read: float
    think: float
    type: float

    @property
    def total(self) -> float:
        return round(self.read + self.think + self.type, 2)


def typing_plan(reply_text: str, source_text: str = "", *, jitter: bool = True) -> TypingPlan:
    """Work out a human timing plan for one reply.

    Every stage scales with length, which is the whole point: a two-word
    answer should come back in about two seconds, and a four-line answer
    should take as long as four lines take to write. A constant delay makes
    short replies feel dead and long replies look pre-written.

        "ok"                    ->  ~1.5 s total
        a normal sentence       ->  ~4 s
        a long paragraph        ->  ~15 s

    Pure and deterministic with ``jitter=False`` so it can be tested.
    """
    reply = reply_text or ""
    source = source_text or ""

    def wobble(value: float, spread: float) -> float:
        return value * random.uniform(1 - spread, 1 + spread) if jitter else value

    # Reading: proportional to what was actually received.
    read = min(len(source) * settings.reading_speed, settings.max_reading_time)
    read = wobble(read, 0.25) + (random.uniform(0.2, 0.7) if jitter else 0.35)

    # Thinking: a longer answer implies more to think about. Short answers
    # get almost none, which is what keeps "yeah" from taking ten seconds.
    think = min(len(reply) * settings.thinking_speed, settings.max_thinking_time)
    think = wobble(think, 0.3) + (random.uniform(0.1, 0.4) if jitter else 0.2)

    # Typing: the dominant term, straight from the reply length.
    type_time = len(reply) * settings.typing_speed
    type_time = wobble(type_time, 0.15)
    type_time = max(settings.min_typing_time, min(type_time, settings.max_typing_time))

    return TypingPlan(round(read, 2), round(think, 2), round(type_time, 2))


async def simulate_typing(
    client,
    chat_id: int,
    reply_text: str,
    *,
    source_text: str = "",
    extra_delay: float = 0.0,
) -> None:
    """Read, think, then type - at roughly human speed, scaled to length.

    Long replies are typed in two bursts with a pause, because nobody types
    250 characters without stopping.
    """
    if extra_delay > 0:
        await asyncio.sleep(extra_delay)

    plan = typing_plan(reply_text, source_text)
    await asyncio.sleep(plan.read + plan.think)

    typing_time = plan.type
    try:
        if typing_time > 4.0:
            # A long message is typed in two goes: type, pause (re-read what
            # you wrote), finish. The indicator dropping and coming back is
            # what a real person looks like from the other side.
            first = typing_time * random.uniform(0.45, 0.60)
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(first)
            await asyncio.sleep(random.uniform(0.5, 1.4))
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(typing_time - first)
        else:
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(typing_time)
    except Exception as exc:  # pragma: no cover - cosmetic only
        log.debug("typing indicator failed: %s", exc)
        await asyncio.sleep(typing_time)

    await asyncio.sleep(random.uniform(0.1, 0.3))


async def send_reaction(client, event, sentiment: str = "neutral", *, big: bool = False) -> None:
    """React to a message, if the chat allows reactions at all.

    ``big`` plays Telegram's full-screen animation. See
    :func:`deserves_big_reaction` for when that is appropriate.
    """
    if not settings.reactions_enabled:
        return
    pool = {
        "positive": POSITIVE_REACTIONS,
        "negative": NEGATIVE_REACTIONS,
    }.get(sentiment, NEUTRAL_REACTIONS)

    try:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji

        # The parameter is "reaction", singular, even though it takes a
        # list. It was "reactions" here for a long time, and because the
        # whole call is wrapped in a swallow, every reaction this bot has
        # ever tried to send failed silently as a TypeError.
        await client(
            SendReactionRequest(
                peer=event.chat_id,
                msg_id=event.id,
                big=bool(big),
                add_to_recent=True,
                reaction=[ReactionEmoji(emoticon=random.choice(pool))],
            )
        )
    except Exception as exc:
        # Plenty of chats disable reactions, and some reject specific emoji.
        # This is decoration: never let it interfere with the actual reply.
        log.debug("reaction skipped: %s", type(exc).__name__)


def parse_quiet_hours(value: str | None) -> tuple[int, int] | None:
    """Parse ``HH:MM-HH:MM`` into minutes-from-midnight, or None."""
    if not value:
        return None
    try:
        start_raw, end_raw = value.split("-")
        start_h, start_m = (int(x) for x in start_raw.strip().split(":"))
        end_h, end_m = (int(x) for x in end_raw.strip().split(":"))
    except (ValueError, AttributeError):
        return None
    if not (0 <= start_h < 24 and 0 <= end_h < 24):
        return None
    if not (0 <= start_m < 60 and 0 <= end_m < 60):
        return None
    return start_h * 60 + start_m, end_h * 60 + end_m


def is_quiet_hours(value: str | None, *, now: dt.datetime | None = None) -> bool:
    """Is the current local time inside the quiet window?

    Handles windows that wrap midnight, which is the normal case for sleep.
    """
    window = parse_quiet_hours(value)
    if window is None:
        return False
    start, end = window
    now = now or dt.datetime.now(settings.tz)
    current = now.hour * 60 + now.minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


async def mark_as_read(client, event) -> bool:
    """Send the read receipt: the sender's single tick becomes a double one.

    Done *after* replying, never before. Marking a message read the instant
    it arrives and then taking twenty seconds to answer is a worse tell than
    not marking it at all: no human reads at machine speed and then pauses.
    """
    if not settings.mark_as_read:
        return False
    try:
        await client.send_read_acknowledge(event.chat_id, max_id=event.id)
    except Exception as exc:  # a read receipt is never worth an error
        log.debug("could not mark as read: %s", type(exc).__name__)
        return False
    return True
