"""Scheduled daily messages.

Two bugs from the previous version are fixed here:

* **Timezone.** Times were matched against the server clock, which is UTC on
  Render, so a schedule set for 08:00 fired at 13:30 in India. Matching now
  happens in the configured local timezone.
* **Duplicate sends.** The loop compared ``HH:MM`` every 60 seconds, so a
  slow iteration could match the same minute twice and send twice. Each
  schedule is now marked as sent for that day.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random

from config import settings
from core.personas import get_persona
from database import mongo
from handlers import ai

log = logging.getLogger(__name__)

__all__ = ["run_scheduler"]

_FALLBACKS = {
    "morning": (
        "Morning! Hope today treats you well.",
        "Good morning ☀️",
        "Morning — have a good one.",
    ),
    "afternoon": (
        "Afternoon! How's the day going?",
        "Hey, hope the day's going well.",
        "Afternoon check-in — all good?",
    ),
    "night": (
        "Night! Sleep well.",
        "Goodnight 🌙",
        "Heading off — talk tomorrow.",
    ),
}

#: (user_id, kind) -> date already handled, so a schedule fires once a day.
_sent_today: dict[tuple[int, str], str] = {}
#: Recent texts per user, so the greeting is not identical every morning.
_recent: dict[int, list[str]] = {}


async def _compose(kind: str, user_id: int) -> str:
    """Ask the model for a fresh greeting, falling back to a canned one."""
    persona = get_persona(await mongo.get_persona_key())
    avoid = _recent.get(user_id, [])[-5:]
    system = (
        f"{persona.prompt}\n"
        f"Write a short {kind} greeting to send first, unprompted. "
        "One or two sentences, English, at most one emoji. "
        "Return only the message itself, with no quotes."
    )
    if avoid:
        system += "\nDo not reuse any of these:\n" + "\n".join(f"- {a}" for a in avoid)

    reply, _ = await ai._complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Send the {kind} greeting."},
        ]
    )
    if reply:
        return reply

    options = [m for m in _FALLBACKS.get(kind, ("Hey!",)) if m not in avoid]
    return random.choice(options or list(_FALLBACKS.get(kind, ("Hey!",))))


def _remember(user_id: int, text: str) -> None:
    history = _recent.setdefault(user_id, [])
    history.append(text)
    del history[:-10]


async def run_scheduler(client, *, interval: float = 30.0) -> None:
    """Check every 30 s whether a scheduled message is due."""
    log.info("scheduler started (timezone %s)", settings.timezone)
    while True:
        try:
            await _tick(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


async def _tick(client, *, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(settings.tz)
    current = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    sent = 0

    if await mongo.is_locked():
        return 0

    for row in await mongo.get_active_schedules():
        user_id, kind = row.get("user_id"), row.get("type")
        if row.get("time") != current:
            continue
        if _sent_today.get((user_id, kind)) == today:
            continue

        _sent_today[(user_id, kind)] = today
        try:
            text = await _compose(kind, user_id)
            await client.send_message(user_id, text)
            _remember(user_id, text)
            await mongo.increment_stat("total_replies")
            await mongo.increment_today()
            sent += 1
            log.info("sent %s greeting to %s", kind, user_id)
        except Exception as exc:
            # Leave it marked as sent: retrying every 30 s against a user who
            # has blocked the account is exactly the behaviour that gets an
            # account limited.
            log.warning("could not send %s greeting to %s: %s", kind, user_id, exc)
    return sent
