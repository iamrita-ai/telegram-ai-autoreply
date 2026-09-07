"""Render a RichMessage to standalone HTML that mimics the Telegram client.

Useful for reviewing a formatted message without deploying anything. It walks
the real ``MessageEntity`` objects the bot would send, so what you see is
driven by the same offsets Telegram will use - including the UTF-16 maths.

    python scripts/render_rich_preview.py docs/rich-preview.html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rich import demo_message

_TAGS = {
    "MessageEntityBold": ("<b>", "</b>"),
    "MessageEntityItalic": ("<i>", "</i>"),
    "MessageEntityUnderline": ("<u>", "</u>"),
    "MessageEntityStrike": ("<s>", "</s>"),
    "MessageEntityCode": ('<code class="inline">', "</code>"),
    "MessageEntitySpoiler": ('<span class="spoiler" title="tap to reveal">', "</span>"),
}


def render(text: str, entities) -> str:
    """Turn (text, entities) into HTML, honouring UTF-16 offsets."""
    raw = text.encode("utf-16-le")
    units = [raw[i * 2 : i * 2 + 2] for i in range(len(raw) // 2)]
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}

    for entity in entities:
        name = type(entity).__name__
        start, end = entity.offset, entity.offset + entity.length
        if name in _TAGS:
            open_tag, close_tag = _TAGS[name]
        elif name == "MessageEntityTextUrl":
            open_tag, close_tag = f'<a href="{entity.url}">', "</a>"
        elif name == "MessageEntityPre":
            open_tag, close_tag = '<pre class="block">', "</pre>"
        elif name == "MessageEntityBlockquote":
            css = "quote expandable" if getattr(entity, "collapsed", None) else "quote"
            open_tag = f'<div class="{css}">'
            close_tag = "</div>"
        else:  # pragma: no cover - unknown entity types render as plain text
            continue
        opens.setdefault(start, []).append(open_tag)
        closes.setdefault(end, []).insert(0, close_tag)

    out: list[str] = []
    buffer: list[bytes] = []

    def flush() -> None:
        if buffer:
            chunk = b"".join(buffer).decode("utf-16-le")
            out.append(chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            buffer.clear()

    for index in range(len(units) + 1):
        if index in closes:
            flush()
            out.extend(closes[index])
        if index in opens:
            flush()
            out.extend(opens[index])
        if index < len(units):
            buffer.append(units[index])
    flush()
    return "".join(out).replace("\n", "<br>")


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Rich message preview</title>
<style>
  body {{ margin:0; padding:32px; background:#0e1621; font-family:-apple-system,
         "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color:#fff; }}
  .phone {{ max-width:460px; margin:0 auto; }}
  .caption {{ color:#7d8b99; font-size:13px; text-align:center; margin-bottom:14px; }}
  .bubble {{ background:#182533; border-radius:14px 14px 14px 4px; padding:12px 14px;
             font-size:15px; line-height:1.45; box-shadow:0 1px 2px rgba(0,0,0,.4);
             word-wrap:break-word; }}
  a {{ color:#62bcf9; text-decoration:none; }}
  code.inline {{ font-family:ui-monospace, Menlo, Consolas, monospace; font-size:14px;
                 background:#232e3c; padding:1px 4px; border-radius:4px; }}
  pre.block {{ font-family:ui-monospace, Menlo, Consolas, monospace; font-size:13px;
               background:#232e3c; padding:10px 12px; border-radius:8px; margin:6px 0;
               overflow-x:auto; white-space:pre-wrap; }}
  .quote {{ border-left:3px solid #62bcf9; padding:2px 0 2px 10px; margin:6px 0;
            color:#dfe6ec; }}
  .quote.expandable {{ position:relative; }}
  .quote.expandable::after {{ content:"⌄ tap to expand"; display:block; margin-top:4px;
            font-size:12px; color:#62bcf9; }}
  .spoiler {{ background:#3a4757; color:transparent; border-radius:3px; cursor:pointer;
              transition:.15s; }}
  .spoiler:hover {{ background:transparent; color:inherit; }}
  .meta {{ text-align:right; font-size:11px; color:#6d7f8f; margin-top:6px; }}
</style>
<div class="phone">
  <div class="caption">how /rich renders in Telegram - hover the spoiler to reveal</div>
  <div class="bubble">{body}<div class="meta">09:41 ✓✓</div></div>
</div>
"""


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/rich-preview.html")
    text, entities = demo_message().build()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PAGE.format(body=render(text, entities)), encoding="utf-8")
    print(f"wrote {target} ({len(entities)} entities)")


if __name__ == "__main__":
    main()
