"""Scheduled daily messages.

Two bugs from the previous version are fixed here:

* **Timezone.** Times were matched against the server clock, which is UTC on
  Render, so a schedule set for 08:00 fired at 13:30 in India. Matching now
  happens in the configured local timezone.
* **Duplicate sends.** The loop compared ``HH:MM`` every 60 seconds, so a
  slow iteration could match the same minute twice and send twice. Each
  schedule is now marked as sent for that day.

Greetings are **always** written by the model. There is no canned list to
fall back on: a fixed set of strings sent every morning is both obviously
robotic to the person receiving it and, once it repeats across chats, the
clearest spam signal an account can emit. If every provider is down the
greeting is retried a few times and then skipped for the day.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from config import settings
from core.personas import get_persona
from core.safety import limiter, normalise
from database import mongo
from handlers import ai

log = logging.getLogger(__name__)

__all__ = ["run_scheduler"]

#: (user_id, kind) -> date already handled, so a schedule fires once a day.
_sent_today: dict[tuple[int, str], str] = {}
#: Recent greetings per user, so no two mornings sound the same.
_recent: dict[int, list[str]] = {}

#: How many times to ask the model for a *fresh* greeting before giving up.
_COMPOSE_ATTEMPTS = 3
#: Pause between attempts, to let a rate-limited provider recover.
_RETRY_DELAY = 20.0


async def _compose(kind: str, user_id: int) -> str | None:
    """Ask the model for a greeting nobody has been sent before.

    Returns ``None`` when no provider produced anything usable. The caller
    skips the greeting in that case - there is deliberately no canned text.
    """
    persona = get_persona(await mongo.get_persona_key())
    avoid = _recent.get(user_id, [])[-8:]
    seen = {normalise(a) for a in avoid}

    system = (
        f"{persona.prompt}\n"
        f"Write a short {kind} greeting to send first, unprompted. "
        "One or two sentences, English, at most one emoji. "
        "Make it sound spontaneous and different every time - vary the "
        "opening word, the length and the rhythm. "
        "Return only the message itself, with no quotes."
    )
    if avoid:
        system += "\nYou have already sent these, so write something different:\n"
        system += "\n".join(f"- {a}" for a in avoid)

    for attempt in range(1, _COMPOSE_ATTEMPTS + 1):
        reply, provider = await ai._complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Send the {kind} greeting."},
            ]
        )
        if reply and normalise(reply) not in seen:
            log.debug("composed %s greeting via %s on attempt %d", kind, provider, attempt)
            return reply
        if reply:
            log.info("model repeated a previous %s greeting - asking again", kind)
        if attempt < _COMPOSE_ATTEMPTS:
            await asyncio.sleep(_RETRY_DELAY)
    log.warning(
        "no provider produced a fresh %s greeting for %s - skipping it today "
        "rather than sending a canned line",
        kind,
        user_id,
    )
    return None


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
            if not text:
                continue
            # The same guard the reply path uses: never send a chat something
            # it has already had.
            if not limiter.allow_text(user_id, text).allowed:
                log.info("skipped a duplicate %s greeting for %s", kind, user_id)
                continue
            await client.send_message(user_id, text)
            limiter.record(user_id, text=text)
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
