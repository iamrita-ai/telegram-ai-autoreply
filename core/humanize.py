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

from config import settings

log = logging.getLogger(__name__)

__all__ = ["detect_sentiment", "is_quiet_hours", "send_reaction", "simulate_typing"]

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


def detect_sentiment(text: str) -> str:
    words = {w.strip(".,!?;:'\"").lower() for w in (text or "").split()}
    positive = len(words & _POSITIVE)
    negative = len(words & _NEGATIVE)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


async def simulate_typing(
    client,
    chat_id: int,
    reply_text: str,
    *,
    source_text: str = "",
    extra_delay: float = 0.0,
) -> None:
    """Read, think, then type - at roughly human speed.

    Long replies are typed in two bursts with a pause, because nobody types
    250 characters without stopping.
    """
    if extra_delay > 0:
        await asyncio.sleep(extra_delay)

    # Reading the incoming message.
    words = len(source_text.split()) if source_text else 0
    await asyncio.sleep(min(words * 0.09, 3.5) + random.uniform(0.4, 1.2))

    typing_time = min(len(reply_text) * settings.typing_speed, settings.max_typing_time)
    typing_time = max(0.8, typing_time + random.uniform(-0.3, 0.5))

    try:
        if typing_time > 3.5:
            first = typing_time * random.uniform(0.45, 0.60)
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(first)
            await asyncio.sleep(random.uniform(0.4, 0.9))
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(typing_time - first)
        else:
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(typing_time)
    except Exception as exc:  # pragma: no cover - cosmetic only
        log.debug("typing indicator failed: %s", exc)
        await asyncio.sleep(typing_time)

    await asyncio.sleep(random.uniform(0.1, 0.3))


async def send_reaction(client, event, sentiment: str = "neutral") -> None:
    """React to a message, if the chat allows reactions at all."""
    if not settings.reactions_enabled:
        return
    pool = {
        "positive": POSITIVE_REACTIONS,
        "negative": NEGATIVE_REACTIONS,
    }.get(sentiment, NEUTRAL_REACTIONS)

    try:
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji

        await client(
            SendReactionRequest(
                peer=event.chat_id,
                msg_id=event.id,
                big=False,
                reactions=[ReactionEmoji(emoticon=random.choice(pool))],
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
