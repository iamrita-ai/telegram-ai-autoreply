"""House style for everything the bot writes.

Two rules, both learned from how people spot automation:

**No em dashes.** Almost nobody types "-" on a phone keyboard, but language
models produce them constantly, so a stream of them in a chat message is a
tell. Everything the bot sends, whether it came from a model or from the
control bot's own text, is normalised to plain punctuation.

**Emoji are rationed.** One emoji at the end of a sentence reads as a person.
An emoji in every sentence reads as marketing, and marketing is what gets
reported as spam. Model output is capped at one, and the control bot's own
panels are held to the same standard by a test.
"""

from __future__ import annotations

import re

__all__ = [
    "EM_DASHES",
    "clean",
    "count_emoji",
    "limit_emoji",
    "strip_em_dashes",
]

#: Em dash, en dash, horizontal bar, minus sign, and the two-hyphen form.
EM_DASHES = "\u2014\u2013\u2015\u2212"

_SPACED_DASH = re.compile(rf"\s*[{EM_DASHES}]+\s*")
_DOUBLE_HYPHEN = re.compile(r"(?<=\w)\s--\s(?=\w)")

# Emoji live in a handful of blocks. Box drawing (━), the middle dot (·) and
# ordinary punctuation are deliberately outside every range below, because
# the control bot's panels use them for layout.
_EMOJI_CORE = (
    "\U0001f000-\U0001faff"  # pictographs, faces, symbols, flags, hearts
    "\u2600-\u27bf"  # misc symbols and dingbats: ✅ ✨ ☀ ✂
    "\u2b00-\u2bff"  # ⭐ ⬛ ⬅
    "\u3030\u303d\u3297\u3299"
    "\u00a9\u00ae"
)
_MODIFIERS = "\ufe0f\ufe0e\u20e3\U0001f3fb-\U0001f3ff"
_REGIONAL = "\U0001f1e6-\U0001f1ff"

#: One emoji *cluster*: a base plus any joiners, skin tones and keycaps, so a
#: family sequence or a flag counts once rather than five times. Order
#: matters: flags and keycaps are matched before the generic case, which
#: would otherwise split them into their component code points.
_EMOJI_CLUSTER = re.compile(
    rf"(?:[{_REGIONAL}]{{2}}"
    rf"|[0-9#*]\ufe0f?\u20e3"
    rf"|[{_EMOJI_CORE}][{_MODIFIERS}]*(?:\u200d[{_EMOJI_CORE}][{_MODIFIERS}]*)*)"
)

_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")


def strip_em_dashes(text: str) -> str:
    """Replace dashes nobody types with punctuation everybody does.

    ``"it works - mostly"`` becomes ``"it works, mostly"``; a dash used as a
    range (``"10-20"``) becomes a plain hyphen.
    """
    if not text:
        return ""

    def replace(match: re.Match[str]) -> str:
        chunk = match.group(0)
        before = text[: match.start()]
        after = text[match.end() :]
        spaced = chunk != chunk.strip()
        if not spaced and before and after and before[-1].isdigit() and after[0].isdigit():
            return "-"  # a range: 10-20
        if not spaced:
            return "-"  # a compound: state-of-the-art
        return ", " if before.strip() and after.strip() else " "

    out = _SPACED_DASH.sub(replace, text)
    out = _DOUBLE_HYPHEN.sub(", ", out)
    # ", ." and similar left behind by a dash before punctuation.
    out = re.sub(r",\s*([,.!?;:])", r"\1", out)
    return out


def count_emoji(text: str) -> int:
    return len(_EMOJI_CLUSTER.findall(text or ""))


def limit_emoji(text: str, limit: int = 1) -> str:
    """Keep at most ``limit`` emoji, dropping the rest.

    The ones kept are the first, because that is usually the deliberate one:
    models tend to sprinkle decoration as they go.
    """
    if not text:
        return ""
    kept = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal kept
        if kept < limit:
            kept += 1
            return match.group(0)
        return ""

    out = _EMOJI_CLUSTER.sub(replace, text)
    out = _MULTI_SPACE.sub(" ", out)
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    return "\n".join(line.rstrip() for line in out.split("\n")).strip()


def clean(text: str, *, max_emoji: int = 1) -> str:
    """Apply the whole house style to one outgoing message."""
    return limit_emoji(strip_em_dashes(text or ""), max_emoji).strip()
