"""A small HTTP Bot API client, for the features MTProto cannot reach.

The control bot runs on Telethon (MTProto). That is the right choice for
everything it normally does, but three Bot API features have no MTProto
equivalent in Telethon 1.44:

* **Rich Messages** (Bot API 10.1, 11 June 2026) - ``sendRichMessage``.
* **Disabled buttons and force_reply keyboards** (24 August 2026).
* **Join request queries** (10.1) - ``answerChatJoinRequestQuery``.

Both interfaces speak to the same bot with the same token, and mixing them
is safe here because this client only ever *sends*. It never calls
``getUpdates``, which is the one thing that would fight with the MTProto
connection for the update stream.

Every call degrades: if the server is older than 10.1, or the method is
unknown, or the payload is rejected, the caller is told and falls back to a
plain Telethon message. A formatting feature must never cost the user the
message itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)

__all__ = [
    "BotApiError",
    "MethodUnsupported",
    "answer_join_request_query",
    "call",
    "close",
    "configured",
    "disabled_button",
    "inline_keyboard",
    "rich_supported",
    "send_rich",
]

API_ROOT = "https://api.telegram.org"

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()

#: Methods the server has told us it does not have. Checked before every
#: call so a bot on an older Telegram server tries ``sendRichMessage`` once
#: and then stops asking.
_unsupported: set[str] = set()


class BotApiError(RuntimeError):
    """The API answered, and the answer was no."""

    def __init__(self, method: str, code: int, description: str) -> None:
        super().__init__(f"{method} failed [{code}]: {description}")
        self.method = method
        self.code = code
        self.description = description


class MethodUnsupported(BotApiError):
    """This Telegram server does not implement the method at all."""


def configured() -> bool:
    return bool(settings.bot_token)


async def _http() -> httpx.AsyncClient:
    global _client
    async with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _looks_unimplemented(description: str) -> bool:
    lowered = description.lower()
    return "method not found" in lowered or "unknown method" in lowered


async def call(method: str, payload: dict[str, Any] | None = None) -> Any:
    """Call one Bot API method. Raises :class:`BotApiError` on failure."""
    if not configured():
        raise BotApiError(method, 0, "no BOT_TOKEN configured")
    if method in _unsupported:
        raise MethodUnsupported(method, 404, "method not available on this server")

    client = await _http()
    url = f"{API_ROOT}/bot{settings.bot_token}/{method}"
    body = {key: value for key, value in (payload or {}).items() if value is not None}
    try:
        response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise BotApiError(method, 0, f"{type(exc).__name__}: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise BotApiError(method, response.status_code, "response was not JSON") from exc

    if data.get("ok"):
        return data.get("result")

    description = str(data.get("description", "no description"))
    code = int(data.get("error_code", response.status_code))
    if code == 404 or _looks_unimplemented(description):
        _unsupported.add(method)
        raise MethodUnsupported(method, code, description)
    raise BotApiError(method, code, description)


def rich_supported() -> bool:
    """Has ``sendRichMessage`` been ruled out on this server?"""
    return configured() and "sendRichMessage" not in _unsupported


# ──────────────────────────────────────────────────────────────────────────
#  Reply markup, including the 24 August 2026 additions
# ──────────────────────────────────────────────────────────────────────────
def disabled_button(text: str, *, style: str = "") -> dict[str, Any]:
    """A button that is visibly present but does nothing.

    Added on 24 August 2026 as ``DisabledButton``. It is the honest way to
    show a choice that is already taken: the current persona should look
    like a button, not like a gap in the row.
    """
    button: dict[str, Any] = {"text": text, "disabled": {}}
    if style:
        button["style"] = style
    return button


def inline_keyboard(rows: list[list[dict[str, Any]]], *, force_reply: bool = False) -> dict:
    """An ``InlineKeyboardMarkup``.

    ``force_reply`` (24 August 2026) opens the reply box as soon as the
    message arrives, which is exactly right for a prompt that is about to be
    typed over several messages.
    """
    markup: dict[str, Any] = {"inline_keyboard": rows}
    if force_reply:
        markup["force_reply"] = True
    return markup


def button(text: str, *, data: str = "", url: str = "", style: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"text": text}
    if data:
        item["callback_data"] = data
    if url:
        item["url"] = url
    if style:
        item["style"] = style
    return item


# ──────────────────────────────────────────────────────────────────────────
#  Rich messages
# ──────────────────────────────────────────────────────────────────────────
async def send_rich(
    chat_id: int,
    rich_message: dict[str, Any],
    *,
    reply_markup: dict[str, Any] | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """``sendRichMessage``: a block-structured message, Bot API 10.1."""
    payload: dict[str, Any] = {"chat_id": chat_id, "rich_message": rich_message}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    if disable_notification:
        payload["disable_notification"] = True
    return await call("sendRichMessage", payload)


# ──────────────────────────────────────────────────────────────────────────
#  Guardian bots
# ──────────────────────────────────────────────────────────────────────────
async def answer_join_request_query(
    query_id: str,
    *,
    approve: bool = False,
    decline: bool = False,
    queue: bool = False,
) -> Any:
    """``answerChatJoinRequestQuery``: approve, decline or hold a request.

    Bot API 10.1 lets a guardian bot answer the query directly instead of
    acting on the chat. Telethon has no MTProto equivalent for the query id,
    so this is only reachable when the update arrives over HTTP.
    """
    chosen = [
        name for name, on in (("approve", approve), ("decline", decline), ("queue", queue)) if on
    ]
    if len(chosen) != 1:
        raise ValueError("choose exactly one of approve, decline or queue")
    return await call("answerChatJoinRequestQuery", {"query_id": query_id, "result": chosen[0]})
