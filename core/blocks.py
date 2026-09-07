"""Bot API 10.1 Rich Messages: the block model, as plain data.

Bot API 10.1 (11 June 2026) replaced "text plus a list of entities" with a
**block model**: a message is a list of blocks (headings, paragraphs,
dividers, lists, tables, quotes, media, and so on), and inline styling
inside a block is a small tree of ``RichText`` nodes rather than a set of
offsets. Bot API 10.2 (14 July 2026) added list items and media references.

Everything here is a pure function returning JSON-serialisable dicts, which
means the whole feature is testable without a network. Sending lives in
:mod:`core.botapi`.

Two facts drive the design:

* ``RichText`` is polymorphic. Telegram accepts a bare string, an array of
  nodes, or a tagged object. A bare string is used wherever there is no
  styling, which keeps the payloads small and readable.
* Blocks nest. A list item, a quotation, a collage and a details block all
  contain blocks, so the builders take and return the same shape all the
  way down.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BLOCK_TYPES",
    "anchor",
    "animation",
    "audio",
    "blockquote",
    "bold",
    "bot_command",
    "buttons",
    "cell",
    "code",
    "collage",
    "custom_emoji",
    "datetime_",
    "details",
    "divider",
    "document",
    "email",
    "expandable_quote",
    "footer",
    "heading",
    "italic",
    "list_",
    "list_item",
    "map_",
    "marked",
    "math",
    "math_block",
    "mention",
    "message",
    "paragraph",
    "phone",
    "photo",
    "pre",
    "pullquote",
    "seq",
    "spoiler",
    "strike",
    "sub",
    "sup",
    "table",
    "text_mention",
    "thinking",
    "underline",
    "url",
    "validate",
    "video",
    "voice_note",
]

#: Every block type in the Bot API 10.1/10.2 rich message model.
BLOCK_TYPES: frozenset[str] = frozenset(
    {
        "paragraph",
        "heading",
        "pre",
        "footer",
        "divider",
        "mathematical_expression",
        "anchor",
        "list",
        "blockquote",
        "expandable_blockquote",
        "pullquote",
        "collage",
        "slideshow",
        "table",
        "details",
        "map",
        "buttons",
        "animation",
        "audio",
        "document",
        "photo",
        "video",
        "voice_note",
        "thinking",
    }
)

#: Inline node types, for validation and for the tests.
TEXT_TYPES: frozenset[str] = frozenset(
    {
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "spoiler",
        "date_time",
        "text_mention",
        "subscript",
        "superscript",
        "marked",
        "code",
        "custom_emoji",
        "mathematical_expression",
        "url",
        "email_address",
        "phone_number",
        "bank_card_number",
        "mention",
        "hashtag",
        "cashtag",
        "bot_command",
        "button",
        "anchor",
        "anchor_link",
        "reference",
        "reference_link",
    }
)

RichText = str | dict[str, Any] | list[Any]
Block = dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────
#  Inline text
# ──────────────────────────────────────────────────────────────────────────
def seq(*parts: RichText) -> list[RichText]:
    """A run of inline nodes, e.g. ``seq("hello ", bold("world"))``."""
    return [part for part in parts if part not in ("", None)]


def _wrap(kind: str, text: RichText) -> dict[str, Any]:
    return {"type": kind, "text": text}


def bold(text: RichText) -> dict[str, Any]:
    return _wrap("bold", text)


def italic(text: RichText) -> dict[str, Any]:
    return _wrap("italic", text)


def underline(text: RichText) -> dict[str, Any]:
    return _wrap("underline", text)


def strike(text: RichText) -> dict[str, Any]:
    return _wrap("strikethrough", text)


def spoiler(text: RichText) -> dict[str, Any]:
    return _wrap("spoiler", text)


def code(text: RichText) -> dict[str, Any]:
    return _wrap("code", text)


def marked(text: RichText) -> dict[str, Any]:
    """Highlighted text, as if run over with a marker pen."""
    return _wrap("marked", text)


def sub(text: RichText) -> dict[str, Any]:
    return _wrap("subscript", text)


def sup(text: RichText) -> dict[str, Any]:
    return _wrap("superscript", text)


def mention(text: RichText) -> dict[str, Any]:
    return _wrap("mention", text)


def bot_command(text: RichText) -> dict[str, Any]:
    """A /command, rendered by Telegram as a tappable command."""
    return _wrap("bot_command", text)


def email(text: RichText) -> dict[str, Any]:
    return _wrap("email_address", text)


def phone(text: RichText) -> dict[str, Any]:
    return _wrap("phone_number", text)


def url(text: RichText, href: str) -> dict[str, Any]:
    return {"type": "url", "text": text, "url": href}


def text_mention(text: RichText, user_id: int) -> dict[str, Any]:
    return {"type": "text_mention", "text": text, "user": {"id": user_id}}


def custom_emoji(custom_emoji_id: str, alternative_text: str) -> dict[str, Any]:
    return {
        "type": "custom_emoji",
        "custom_emoji_id": custom_emoji_id,
        "alternative_text": alternative_text,
    }


def datetime_(text: RichText, unix_time: int, date_time_format: str = "") -> dict[str, Any]:
    """A timestamp Telegram renders in the reader's own timezone."""
    node = {"type": "date_time", "text": text, "unix_time": int(unix_time)}
    if date_time_format:
        node["date_time_format"] = date_time_format
    return node


def math(expression: str) -> dict[str, Any]:
    """Inline LaTeX."""
    return {"type": "mathematical_expression", "expression": expression}


def anchor_link(text: RichText, name: str) -> dict[str, Any]:
    """A link to an anchor block elsewhere in the same message."""
    return {"type": "anchor_link", "text": text, "name": name}


# ──────────────────────────────────────────────────────────────────────────
#  Blocks
# ──────────────────────────────────────────────────────────────────────────
def paragraph(text: RichText) -> Block:
    return {"type": "paragraph", "text": text}


def heading(text: RichText, size: int = 1) -> Block:
    """A section heading. ``size`` is the level, 1 being the largest."""
    return {"type": "heading", "text": text, "size": int(size)}


def divider() -> Block:
    return {"type": "divider"}


def pre(text: RichText, language: str = "") -> Block:
    block: Block = {"type": "pre", "text": text}
    if language:
        block["language"] = language
    return block


def footer(text: RichText) -> Block:
    return {"type": "footer", "text": text}


def anchor(name: str) -> Block:
    return {"type": "anchor", "name": name}


def math_block(expression: str) -> Block:
    return {"type": "mathematical_expression", "expression": expression}


def thinking(text: RichText) -> Block:
    """The collapsed "thinking" block used when streaming an AI reply."""
    return {"type": "thinking", "text": text}


def _as_blocks(value: list[Block] | RichText) -> list[Block]:
    """Accept either real blocks or text, and always return blocks.

    The check has to look at the ``type`` of each item, not merely at
    whether it is a dict: an inline run built with :func:`seq` is also a
    list of dicts, and wrapping it in a paragraph is exactly what it needs.
    """
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, dict) and item.get("type") in BLOCK_TYPES for item in value)
    ):
        return value  # type: ignore[return-value]
    if isinstance(value, list) and not value:
        return []
    return [paragraph(value)]  # type: ignore[arg-type]


def list_item(
    blocks: list[Block] | RichText,
    *,
    label: str = "",
    checkbox: bool | None = None,
    checked: bool = False,
    value: int = 0,
    numeral: str = "",
) -> dict[str, Any]:
    """One item of a list.

    ``blocks`` may be given as plain text for the common single-paragraph
    case. ``checkbox`` makes it a task-list item, ``value`` sets a custom
    number and ``numeral`` picks the numbering style.
    """
    item: dict[str, Any] = {"label": label, "blocks": _as_blocks(blocks)}
    if checkbox or checked:
        item["has_checkbox"] = True
        item["is_checked"] = bool(checked)
    if value:
        item["value"] = int(value)
    if numeral:
        item["type"] = numeral
    return item


def list_(items: list[dict[str, Any]]) -> Block:
    return {"type": "list", "items": items}


def blockquote(blocks: list[Block] | RichText, *, credit: RichText | None = None) -> Block:
    block: Block = {"type": "blockquote", "blocks": _as_blocks(blocks)}
    if credit is not None:
        block["credit"] = credit
    return block


def expandable_quote(text: RichText, *, credit: RichText | None = None) -> Block:
    """A quotation collapsed behind a "show more" control."""
    block: Block = {"type": "expandable_blockquote", "text": text}
    if credit is not None:
        block["credit"] = credit
    return block


def pullquote(text: RichText, *, credit: RichText | None = None) -> Block:
    block: Block = {"type": "pullquote", "text": text}
    if credit is not None:
        block["credit"] = credit
    return block


def details(summary: RichText, blocks: list[Block], *, is_open: bool = False) -> Block:
    """A collapsible section."""
    block: Block = {"type": "details", "summary": summary, "blocks": blocks}
    if is_open:
        block["is_open"] = True
    return block


def cell(
    text: RichText | None = None,
    *,
    header: bool = False,
    align: str = "left",
    valign: str = "middle",
    colspan: int = 0,
    rowspan: int = 0,
) -> dict[str, Any]:
    item: dict[str, Any] = {"align": align, "valign": valign}
    if text is not None:
        item["text"] = text
    if header:
        item["is_header"] = True
    if colspan:
        item["colspan"] = int(colspan)
    if rowspan:
        item["rowspan"] = int(rowspan)
    return item


def table(
    rows: list[list[dict[str, Any]]],
    *,
    bordered: bool = False,
    striped: bool = False,
    compact: bool = False,
    caption: RichText | None = None,
) -> Block:
    block: Block = {"type": "table", "cells": rows}
    if bordered:
        block["is_bordered"] = True
    if striped:
        block["is_striped"] = True
    if compact:
        block["is_compact"] = True
    if caption is not None:
        block["caption"] = caption
    return block


def map_(
    latitude: float,
    longitude: float,
    *,
    zoom: int = 14,
    width: int = 600,
    height: int = 400,
    caption: RichText | None = None,
) -> Block:
    block: Block = {
        "type": "map",
        "location": {"latitude": float(latitude), "longitude": float(longitude)},
        "zoom": int(zoom),
        "width": int(width),
        "height": int(height),
    }
    if caption is not None:
        block["caption"] = {"text": caption}
    return block


def _media(kind: str, field: str, media: str | dict[str, Any], caption, credit) -> Block:
    value = {"type": kind, "media": media} if isinstance(media, str) else media
    block: Block = {"type": kind, field: value}
    if caption is not None:
        cap: dict[str, Any] = {"text": caption}
        if credit is not None:
            cap["credit"] = credit
        block["caption"] = cap
    return block


def photo(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("photo", "photo", media, caption, credit)


def video(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("video", "video", media, caption, credit)


def animation(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("animation", "animation", media, caption, credit)


def audio(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("audio", "audio", media, caption, credit)


def document(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("document", "document", media, caption, credit)


def voice_note(media: str | dict[str, Any], *, caption=None, credit=None) -> Block:
    return _media("voice_note", "voice_note", media, caption, credit)


def collage(blocks: list[Block], *, caption: RichText | None = None) -> Block:
    block: Block = {"type": "collage", "blocks": blocks}
    if caption is not None:
        block["caption"] = {"text": caption}
    return block


def slideshow(blocks: list[Block], *, caption: RichText | None = None) -> Block:
    block: Block = {"type": "slideshow", "blocks": blocks}
    if caption is not None:
        block["caption"] = {"text": caption}
    return block


def buttons(rows: list[dict[str, Any]], *, align: str = "") -> Block:
    """Buttons inside the message body, as opposed to a reply keyboard."""
    block: Block = {"type": "buttons", "buttons": rows}
    if align:
        block["align"] = align
    return block


# ──────────────────────────────────────────────────────────────────────────
#  The message
# ──────────────────────────────────────────────────────────────────────────
def message(
    blocks: list[Block] | None = None,
    *,
    markdown: str = "",
    html: str = "",
    media: list[dict[str, Any]] | None = None,
    is_rtl: bool = False,
    skip_entity_detection: bool = False,
) -> dict[str, Any]:
    """An ``InputRichMessage``: blocks, or markdown, or HTML.

    Telegram accepts exactly one of the three. Markdown is the quick path
    for text that is already written; blocks are for structure.
    """
    if not blocks and not markdown and not html:
        raise ValueError("a rich message needs blocks, markdown or html")
    payload: dict[str, Any] = {}
    if blocks:
        payload["blocks"] = [validate(block) for block in blocks]
    if markdown:
        payload["markdown"] = markdown
    if html:
        payload["html"] = html
    if media:
        payload["media"] = media
    if is_rtl:
        payload["is_rtl"] = True
    if skip_entity_detection:
        payload["skip_entity_detection"] = True
    return payload


def validate(block: Block) -> Block:
    """Reject a block Telegram would reject, with a message that says why.

    Worth the few microseconds: the API returns "Bad Request: invalid rich
    message" for anything malformed, which tells you nothing about which of
    twenty blocks was wrong.
    """
    if not isinstance(block, dict):
        raise TypeError(f"a block must be a dict, got {type(block).__name__}")
    kind = block.get("type")
    if kind not in BLOCK_TYPES:
        raise ValueError(f"unknown rich block type {kind!r}")
    for key in ("blocks", "items"):
        for child in block.get(key, []) or []:
            for nested in child.get("blocks", []) if key == "items" else [child]:
                validate(nested)
    return block
