"""The userbot itself: what happens when a message arrives.

Extracted from ``main.py``, where the whole pipeline lived inside one nested
function with a bare ``except Exception: print(...)``.

Order of checks matters, and it is deliberately cheapest-first: a locked bot
or a blacklisted sender must cost one lookup, not an AI call.
"""

from __future__ import annotations

import asyncio
import logging

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from core.humanize import detect_sentiment, is_quiet_hours, send_reaction, simulate_typing
from core.safety import limiter
from database import mongo
from handlers import ai

log = logging.getLogger(__name__)

__all__ = ["attach", "background_tasks"]

#: Strong references to fire-and-forget tasks. Without this the garbage
#: collector can cancel an in-flight reply mid-sentence.
background_tasks: set[asyncio.Task] = set()

#: How long to wait for the rest of a sentence before answering a fragment.
FRAGMENT_GRACE_SECONDS = 12.0


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def attach(client, *, me_id: int, display_name: str) -> None:
    """Register the incoming-message handler on a signed-in user client."""

    @client.on(events.NewMessage(incoming=True))
    async def _on_message(event) -> None:
        try:
            await _handle(client, event, me_id=me_id, display_name=display_name)
        except FloodWaitError as exc:
            # Telegram is explicitly telling us to slow down. Respect it.
            log.warning("flood wait: sleeping %ss", exc.seconds)
            await asyncio.sleep(min(exc.seconds, 300))
        except Exception:
            log.exception("failed handling message")


async def _handle(client, event, *, me_id: int, display_name: str) -> None:
    if await mongo.is_locked():
        return
    if event.sender_id == me_id or event.out:
        return

    text = (event.text or "").strip()
    if not text:
        return  # media, stickers and service messages are ignored
    if len(text) > 2000:
        return  # a wall of text is almost always a forward or a spam blast

    sender = await event.get_sender()
    if isinstance(sender, User) and (sender.bot or sender.deleted):
        return
    if await mongo.is_blacklisted(event.sender_id):
        return

    is_group = not event.is_private
    if is_group:
        # Two locks on groups: the chat must be allowed *and* we must be
        # addressed. Replying to everything in a group is the fastest way to
        # get an account reported.
        if not event.mentioned:
            return
        if not await mongo.is_group_allowed(event.chat_id):
            return

    if is_quiet_hours(await mongo.get_dnd()):
        return

    decision = limiter.check(event.chat_id)
    if not decision.allowed:
        log.info("skipped reply in %s: %s", event.chat_id, decision.reason)
        return

    limiter.hold(event.chat_id)
    try:
        result = await ai.generate_reply(
            event.sender_id,
            text,
            is_group=is_group,
            display_name=display_name,
        )

        if result.buffered:
            # The user looks mid-thought. Wait briefly; if nothing else
            # arrives, answer what we have rather than losing the message.
            _spawn(
                _answer_fragment_later(client, event, is_group=is_group, display_name=display_name)
            )
            return

        reply = result.text or ai.busy_message()

        if not is_group:
            await send_reaction(client, event, detect_sentiment(text))
        await simulate_typing(
            client,
            event.chat_id,
            reply,
            source_text=text,
            extra_delay=decision.extra_delay,
        )
        await event.reply(reply)

        limiter.record(event.chat_id)
        await mongo.increment_stat("total_replies")
        await mongo.increment_today()
        log.info(
            "replied in %s via %s (%d chars)",
            event.chat_id,
            result.provider or "fallback",
            len(reply),
        )
    finally:
        limiter.release(event.chat_id)


async def _answer_fragment_later(client, event, *, is_group: bool, display_name: str) -> None:
    """Answer a held fragment once the user has clearly stopped typing."""
    await asyncio.sleep(FRAGMENT_GRACE_SECONDS)

    decision = limiter.check(event.chat_id)
    if not decision.allowed:
        return

    limiter.hold(event.chat_id)
    try:
        result = await ai.flush_stale_fragment(
            event.sender_id, is_group=is_group, display_name=display_name
        )
        if not result.text:
            return
        await simulate_typing(client, event.chat_id, result.text)
        await event.reply(result.text)
        limiter.record(event.chat_id)
        await mongo.increment_stat("total_replies")
        await mongo.increment_today()
        log.info("answered held fragment in %s", event.chat_id)
    except FloodWaitError as exc:
        log.warning("flood wait on fragment: %ss", exc.seconds)
    except Exception:
        log.exception("failed answering held fragment")
    finally:
        limiter.release(event.chat_id)


async def cleanup_loop(interval: float = 3600.0) -> None:
    """Trim history past its retention window, hourly."""
    while True:
        try:
            removed = await mongo.auto_cleanup_history()
            if removed["dms"] or removed["groups"]:
                log.info(
                    "history cleanup: %d dm, %d group messages removed",
                    removed["dms"],
                    removed["groups"],
                )
        except Exception:
            log.exception("history cleanup failed")
        await asyncio.sleep(interval)
