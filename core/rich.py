"""Rich (formatted) Telegram messages.

Telethon's HTML parser covers ``b i u s code pre a blockquote``, but it has
no tag for two things Telegram supports and that make a message actually
look rich: **spoilers** and **expandable (collapsed) blockquotes**. Those
need raw ``MessageEntity`` objects.

Raw entities come with one sharp edge: **offsets and lengths are counted in
UTF-16 code units, not Python characters.** Any emoji outside the basic
plane (😄, 🛡, most flags) is two units, so a builder that uses ``len()``
puts every entity after the first emoji in the wrong place - the classic
"my bold text is shifted by three characters" bug. ``_Builder`` counts in
UTF-16 throughout, so emoji are safe anywhere in the text.

The public helper is :func:`send_rich`, which degrades gracefully: if a
client or chat rejects the entities for any reason, the same message is sent
as plain text rather than not at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityItalic,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    TypeMessageEntity,
)

log = logging.getLogger(__name__)

__all__ = ["RichMessage", "demo_message", "send_rich", "utf16_len"]


def utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units, which is what Telegram counts."""
    return len(text.encode("utf-16-le")) // 2


@dataclass
class RichMessage:
    """A fluent builder for a formatted Telegram message.

    Every method appends and returns ``self``, so a message reads like the
    thing it produces::

        RichMessage().bold("Hi").text(" - see ").link("docs", "https://…")
    """

    text: str = ""
    entities: list[TypeMessageEntity] = field(default_factory=list)

    # -- internals ---------------------------------------------------------
    @property
    def _offset(self) -> int:
        return utf16_len(self.text)

    def _add(self, body: str, factory=None) -> RichMessage:
        if not body:
            return self
        start = self._offset
        self.text += body
        if factory is not None:
            self.entities.append(factory(offset=start, length=utf16_len(body)))
        return self

    # -- plain -------------------------------------------------------------
    def text_(self, body: str) -> RichMessage:
        return self._add(body)

    #: ``text`` is also the field name, so expose the readable alias too.
    append = text_

    def line(self, body: str = "") -> RichMessage:
        return self._add(f"{body}\n")

    def newline(self, count: int = 1) -> RichMessage:
        return self._add("\n" * count)

    # -- inline styles -----------------------------------------------------
    def bold(self, body: str) -> RichMessage:
        return self._add(body, MessageEntityBold)

    def italic(self, body: str) -> RichMessage:
        return self._add(body, MessageEntityItalic)

    def underline(self, body: str) -> RichMessage:
        return self._add(body, MessageEntityUnderline)

    def strike(self, body: str) -> RichMessage:
        return self._add(body, MessageEntityStrike)

    def code(self, body: str) -> RichMessage:
        return self._add(body, MessageEntityCode)

    def spoiler(self, body: str) -> RichMessage:
        """Hidden text the reader taps to reveal."""
        return self._add(body, MessageEntitySpoiler)

    def link(self, label: str, url: str) -> RichMessage:
        start = self._offset
        self.text += label
        self.entities.append(MessageEntityTextUrl(offset=start, length=utf16_len(label), url=url))
        return self

    # -- blocks ------------------------------------------------------------
    def pre(self, body: str, language: str = "") -> RichMessage:
        body = body.rstrip("\n") + "\n"
        start = self._offset
        self.text += body
        self.entities.append(
            MessageEntityPre(offset=start, length=utf16_len(body), language=language)
        )
        return self

    def quote(self, body: str, *, expandable: bool = False) -> RichMessage:
        """A blockquote. ``expandable`` renders it collapsed with a 'show more'."""
        start = self._offset
        self.text += body
        self.entities.append(
            MessageEntityBlockquote(
                offset=start,
                length=utf16_len(body),
                collapsed=True if expandable else None,
            )
        )
        return self

    # -- output ------------------------------------------------------------
    def build(self) -> tuple[str, list[TypeMessageEntity]]:
        return self.text, self.entities

    def __len__(self) -> int:  # pragma: no cover - convenience only
        return utf16_len(self.text)


async def send_rich(client, chat, message: RichMessage, *, file=None, reply_to=None):
    """Send a :class:`RichMessage`, degrading to plain text if entities fail."""
    text, entities = message.build()
    try:
        return await client.send_message(
            chat,
            text,
            formatting_entities=entities,
            file=file,
            reply_to=reply_to,
            link_preview=False,
        )
    except Exception as exc:  # entity errors must never cost the whole message
        log.warning("rich send failed (%s) - falling back to plain text", type(exc).__name__)
        return await client.send_message(chat, text, file=file, reply_to=reply_to)


def demo_message(sender_name: str = "Serena") -> RichMessage:
    """A message that exercises every supported format, for live testing."""
    return (
        RichMessage()
        .bold("✨ Rich message demo")
        .newline(2)
        .text_("Everything below is one single message, formatted with Telegram entities.")
        .newline(2)
        .text_("• ")
        .bold("bold")
        .text_(", ")
        .italic("italic")
        .text_(", ")
        .underline("underline")
        .text_(", ")
        .strike("strikethrough")
        .newline()
        .text_("• inline ")
        .code("code_looks_like_this()")
        .newline()
        .text_("• a ")
        .link("real hyperlink", "https://core.telegram.org/api/entities")
        .text_(" with no ugly URL")
        .newline()
        .text_("• tap to reveal: ")
        .spoiler("this part is hidden until you tap it")
        .newline(2)
        .bold("Quote")
        .newline()
        .quote("A normal blockquote, for when you answer a specific line.")
        .newline(2)
        .bold("Expandable quote")
        .newline()
        .quote(
            "This one starts collapsed and expands when tapped.\n"
            "It is the right shape for long context that should not dominate "
            "the chat: a summary, a changelog, an itinerary, the full text of "
            "something you are only referring to in passing.\n"
            "Telethon's HTML parser cannot produce this - it needs a raw "
            "MessageEntityBlockquote with collapsed=True.",
            expandable=True,
        )
        .newline(2)
        .bold("Code block")
        .newline()
        .pre('print("syntax highlighted")', language="python")
        .newline()
        .italic(f"— sent by {sender_name}")
    )
