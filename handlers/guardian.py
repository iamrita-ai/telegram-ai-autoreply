"""Guardian bot: deciding who gets into a group.

Bot API 10.1 (11 June 2026) introduced **guardian bots**: a bot can answer a
join request query directly with approve, decline or queue, or open a Mini
App to show a captcha before deciding.

Telethon 1.44 speaks MTProto, where the same job is done by
``messages.hideChatJoinRequest``, and the update carries no ``query_id``.
Both paths are implemented here: if a query id ever arrives, the Bot API
method is used, and otherwise the MTProto call does the same work. The
policy in between is identical either way, which is the part that matters.

Three modes per group:

``off``
    Do nothing. Requests sit in the queue for a human, which is Telegram's
    own behaviour and the right default.
``auto``
    Screen the requester and approve real accounts, decline the obvious
    ones (bots, deleted accounts, and accounts Telegram itself has flagged
    as scam or fake).
``captcha``
    Screen, then ask the person to tap the right button in a private
    message. Approve when they do. A wrong answer declines; silence leaves
    the request pending, because a slow human is not a spammer.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from telethon import Button, events, utils
from telethon.tl.functions.messages import HideChatJoinRequestRequest
from telethon.tl.types import UpdateBotChatInviteRequester

from core import botapi
from database import mongo

log = logging.getLogger(__name__)

__all__ = [
    "MODES",
    "captcha_challenge",
    "pending_count",
    "register",
    "screen",
]

#: What a group's join requests can be set to.
MODES = ("off", "auto", "captcha")

#: Emoji used for the captcha. Deliberately dissimilar, so the challenge is
#: readable at a glance and not a puzzle.
_CAPTCHA_EMOJI = ("🍎", "🚗", "🌙", "🎈", "🔑", "🐟", "⚽", "🌵")

#: (chat_id, user_id) -> (correct emoji, deadline). In memory on purpose: a
#: restart cancelling an unfinished captcha simply leaves the request in the
#: queue for a human, which is the safe outcome.
_pending: dict[tuple[int, int], tuple[str, float]] = {}

#: How long somebody has to answer.
CAPTCHA_TIMEOUT = 600.0


def pending_count() -> int:
    return len(_pending)


def screen(sender, *, banned: bool = False) -> tuple[str, str]:
    """Decide what to do about one requester, before any captcha.

    Returns ``(verdict, reason)`` where verdict is ``"decline"``,
    ``"approve"`` or ``"ask"``.
    """
    if banned:
        return "decline", "banned from this bot"
    if getattr(sender, "bot", False):
        return "decline", "the requester is a bot"
    if getattr(sender, "deleted", False):
        return "decline", "deleted account"
    if getattr(sender, "scam", False) or getattr(sender, "fake", False):
        return "decline", "Telegram has flagged this account"
    return "ask", ""


def captcha_challenge(seed: int | None = None) -> tuple[str, list[str]]:
    """Pick the right answer and the options to show alongside it."""
    rng = random.Random(seed)
    options = rng.sample(_CAPTCHA_EMOJI, 4)
    return rng.choice(options), options


async def _resolve(client, chat_id: int, user_id: int, *, approved: bool, query_id=None) -> None:
    """Approve or decline, by whichever route is available."""
    if query_id:
        # Bot API 10.1: answer the query itself.
        await botapi.answer_join_request_query(
            str(query_id), approve=approved, decline=not approved
        )
        return
    await client(HideChatJoinRequestRequest(peer=chat_id, user_id=user_id, approved=approved))


async def _notify_owner(client, owner: int, text: str) -> None:
    if not owner:
        return
    try:
        await client.send_message(owner, text)
    except Exception:
        log.debug("could not tell owner %s about a join request", owner)


def register(bot) -> None:
    """Attach the guardian handlers to the control bot."""

    @bot.on(events.Raw(UpdateBotChatInviteRequester))
    async def _on_join_request(update) -> None:
        try:
            await _handle(bot, update)
        except Exception:
            log.exception("join request handling failed")

    @bot.on(events.CallbackQuery(pattern=rb"^guard:(-?\d+):(\d+):(.+)$"))
    async def _on_captcha(event) -> None:
        chat_id = int(event.pattern_match.group(1))
        user_id = int(event.pattern_match.group(2))
        answer = event.pattern_match.group(3).decode()

        if event.sender_id != user_id:
            await event.answer("This one is not for you.", alert=True)
            return

        state = _pending.get((chat_id, user_id))
        if state is None:
            await event.answer("That request has already been dealt with.", alert=True)
            return

        correct, deadline = state
        if time.time() > deadline:
            _pending.pop((chat_id, user_id), None)
            await event.answer("That took too long. Ask to join again.", alert=True)
            return

        _pending.pop((chat_id, user_id), None)
        approved = answer == correct
        guard = await mongo.get_guard(chat_id) or {}
        try:
            await _resolve(bot, chat_id, user_id, approved=approved)
        except Exception as exc:
            log.warning("could not %s %s: %s", "approve" if approved else "decline", user_id, exc)
            await event.answer("Something went wrong. An admin will look at it.", alert=True)
            return

        await event.answer("Welcome in." if approved else "Wrong button.")
        await event.edit(
            "You are in. Enjoy the group."
            if approved
            else "That was the wrong button, so the request was declined. "
            "You can ask to join again."
        )
        await _notify_owner(
            bot,
            int(guard.get("owner_id", 0)),
            f"Guardian: {'approved' if approved else 'declined'} user {user_id} "
            f"in chat {chat_id} after the captcha.",
        )


async def _handle(bot, update) -> None:
    """One join request, start to finish."""
    chat_id = _chat_id_of(update)
    user_id = int(getattr(update, "user_id", 0))
    query_id = getattr(update, "query_id", None)  # Bot API 10.1, when present

    guard = await mongo.get_guard(chat_id)
    mode = (guard or {}).get("mode", "off")
    if not guard or mode == "off":
        return  # not ours to decide; leave it for a human

    owner = int(guard.get("owner_id", 0))
    banned = await mongo.is_banned(user_id)
    sender = None
    try:
        sender = await bot.get_entity(user_id)
    except Exception:
        log.debug("could not resolve requester %s", user_id)

    verdict, reason = screen(sender, banned=banned)
    if verdict == "decline":
        await _resolve(bot, chat_id, user_id, approved=False, query_id=query_id)
        await _notify_owner(
            bot, owner, f"Guardian: declined user {user_id} in chat {chat_id} ({reason})."
        )
        log.info("guardian declined %s in %s: %s", user_id, chat_id, reason)
        return

    if mode == "auto":
        await _resolve(bot, chat_id, user_id, approved=True, query_id=query_id)
        await _notify_owner(bot, owner, f"Guardian: approved user {user_id} in chat {chat_id}.")
        log.info("guardian approved %s in %s", user_id, chat_id)
        return

    # captcha
    correct, options = captcha_challenge()
    _pending[(chat_id, user_id)] = (correct, time.time() + CAPTCHA_TIMEOUT)
    rows = [
        [Button.inline(option, data=f"guard:{chat_id}:{user_id}:{option}") for option in options]
    ]
    title = getattr(await _chat_title(bot, chat_id), "title", "") or "the group"
    try:
        await bot.send_message(
            user_id,
            f"You asked to join {title}.\n\nTap {correct} to confirm you are a person. "
            "You have 10 minutes.",
            buttons=rows,
        )
    except Exception:
        # Cannot DM somebody who has never started the bot. Leave the
        # request pending rather than punishing them for a privacy setting.
        _pending.pop((chat_id, user_id), None)
        await _notify_owner(
            bot,
            owner,
            f"Guardian: user {user_id} asked to join chat {chat_id}, but I cannot "
            "message them, so the request is still waiting for you.",
        )
        return

    asyncio.get_running_loop().call_later(
        CAPTCHA_TIMEOUT + 1, lambda: _pending.pop((chat_id, user_id), None)
    )


def _chat_id_of(update) -> int:
    """The marked chat id (-100... for supergroups) Telethon can resolve."""
    peer = getattr(update, "peer", None)
    if peer is None:
        return 0
    try:
        return int(utils.get_peer_id(peer))
    except Exception:
        for attribute in ("channel_id", "chat_id", "user_id"):
            value = getattr(peer, attribute, None)
            if value:
                return int(value)
    return 0


async def _chat_title(bot, chat_id: int):
    try:
        return await bot.get_entity(chat_id)
    except Exception:
        return None
