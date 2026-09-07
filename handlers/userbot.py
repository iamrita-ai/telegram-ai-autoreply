"""The userbot itself: what happens when a message arrives.

Extracted from ``main.py``, where the whole pipeline lived inside one nested
function with a bare ``except Exception: print(...)``.

Order of checks matters, and it is deliberately cheapest-first: a locked bot
or a blacklisted sender must cost one lookup, not an AI call.
"""

from __future__ import annotations

import asyncio
import logging
import time

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from config import settings
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

#: De-duplication for owner alerts, so a spam wave cannot spam the owner too.
_last_alert: dict[str, float] = {}
_ALERT_COOLDOWN = 1800.0


async def _is_stranger(event, sender) -> bool:
    """Is this somebody the owner has no relationship with?

    Contacts and anyone already talked to are trusted. Everyone else gets
    the guardian treatment: longer pauses, content screening and a hard cap
    on how many replies they can pull out of the account.
    """
    if not event.is_private:
        return False  # group access is already gated by the allow-list
    if getattr(sender, "contact", False) or getattr(sender, "mutual_contact", False):
        return False
    history = await mongo.get_conversation(event.sender_id, limit=1, is_group=False)
    return not history


async def _alert_owner(client, message: str) -> None:
    """Tell the owner why the bot stayed quiet, without spamming them."""
    if not message:
        return
    now = time.monotonic()
    if now - _last_alert.get(message[:40], 0.0) < _ALERT_COOLDOWN:
        return
    _last_alert[message[:40]] = now
    for owner_id in settings.owner_ids:
        try:
            await client.send_message(owner_id, message)
        except Exception:  # an alert must never break the reply pipeline
            log.debug("could not alert owner %s", owner_id)
        break


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

    quiet = is_quiet_hours(await mongo.get_dnd())
    if quiet:
        return

    stranger = await _is_stranger(event, sender)
    limiter.note_incoming(event.chat_id, text)
    decision = limiter.check(
        event.chat_id,
        is_stranger=stranger,
        incoming_text=text,
        quiet_hours=quiet,
    )
    if not decision.allowed:
        log.info(
            "skipped reply in %s: %s%s",
            event.chat_id,
            decision.reason,
            " (stranger)" if stranger else "",
        )
        await _alert_owner(client, decision.notify_owner)
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
                _answer_fragment_later(
                    client,
                    event,
                    is_group=is_group,
                    display_name=display_name,
                    stranger=stranger,
                )
            )
            return

        if not result.text:
            # Every provider failed. Saying nothing is safer than sending a
            # canned "I'm busy" line: a stock sentence repeated to several
            # chats is exactly the pattern spam detection looks for, and the
            # sender simply sees the owner as not having replied yet.
            log.warning("no provider produced a reply for %s - staying silent", event.chat_id)
            return
        reply = result.text

        # Last gate before sending: never repeat ourselves.
        repeat = limiter.allow_text(event.chat_id, reply)
        if not repeat.allowed:
            log.info("suppressed a %s in %s", repeat.reason, event.chat_id)
            return

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

        limiter.record(event.chat_id, text=reply, is_stranger=stranger)
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


async def _answer_fragment_later(
    client, event, *, is_group: bool, display_name: str, stranger: bool = False
) -> None:
    """Answer a held fragment once the user has clearly stopped typing."""
    await asyncio.sleep(FRAGMENT_GRACE_SECONDS)

    decision = limiter.check(event.chat_id, is_stranger=stranger)
    if not decision.allowed:
        return

    limiter.hold(event.chat_id)
    try:
        result = await ai.flush_stale_fragment(
            event.sender_id, is_group=is_group, display_name=display_name
        )
        if not result.text:
            return
        if not limiter.allow_text(event.chat_id, result.text).allowed:
            log.info("suppressed a duplicate fragment answer in %s", event.chat_id)
            return
        await simulate_typing(client, event.chat_id, result.text, extra_delay=decision.extra_delay)
        await event.reply(result.text)
        limiter.record(event.chat_id, text=result.text, is_stranger=stranger)
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
