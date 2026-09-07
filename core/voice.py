"""Optional voice-note replies.

Groq serves ``canopylabs/orpheus-v1-english``, an expressive text-to-speech
model. It is **not** a chat model, so it can never be part of the reply
fallback chain - it lives here instead, behind an explicit opt-in.

Three limits shape the design:

* Groq's speech endpoint rejects input over 200 characters, so only short
  replies are eligible.
* Telegram renders a real voice note only for OGG/Opus. If the endpoint
  returns WAV, the clip is sent as an audio file rather than not at all.
* Speech costs an extra API call on a small free tier, and an account that
  has never sent a voice note suddenly sending one to everybody is its own
  kind of tell. Hence: off by default, probabilistic, and never for a chat
  the guardian is already unsure about.

Every failure path falls back to sending the text, so a broken or
unauthorised TTS setup can never cost a reply.
"""

from __future__ import annotations

import io
import logging
import random

from config import settings
from database import mongo
from handlers import ai

log = logging.getLogger(__name__)

__all__ = ["send_as_voice", "settings_summary", "should_speak"]


async def _enabled(owner: int) -> bool:
    stored = await mongo.get_user_setting(owner, "voice_replies", None)
    return settings.voice_replies if stored is None else bool(stored)


async def _chance(owner: int) -> float:
    stored = await mongo.get_user_setting(owner, "voice_chance", None)
    value = settings.voice_reply_chance if stored is None else float(stored)
    return min(max(value, 0.0), 1.0)


async def _voice_name(owner: int) -> str:
    stored = await mongo.get_user_setting(owner, "voice_name", None)
    return str(stored or settings.voice_name)


async def should_speak(owner: int, text: str, *, is_stranger: bool = False) -> bool:
    """Should this particular reply be spoken rather than typed?"""
    if is_stranger:
        # Never send audio to somebody unknown: it is far more intrusive
        # than text, and it is the kind of thing that gets reported.
        return False
    if not await _enabled(owner):
        return False
    if not ai.tts_available():
        return False
    body = (text or "").strip()
    if not body or len(body) > settings.voice_max_chars:
        return False
    if "\n" in body or "```" in body:
        return False  # lists and code do not work as speech
    return random.random() < await _chance(owner)


async def send_as_voice(owner: int, client, chat_id, text: str, *, reply_to=None) -> bool:
    """Send ``text`` as speech. Returns ``False`` if the caller should send text."""
    rendered = await ai.synthesize(text, voice=await _voice_name(owner))
    if not rendered:
        return False
    audio, extension = rendered

    handle = io.BytesIO(audio)
    handle.name = f"voice.{extension}"
    try:
        await client.send_file(
            chat_id,
            handle,
            # Telegram only accepts OGG/Opus as a true voice note; a wav is
            # still worth sending, just as an ordinary audio attachment.
            voice_note=extension == "ogg",
            reply_to=reply_to,
        )
    except Exception as exc:
        log.warning("voice send failed (%s) - falling back to text", type(exc).__name__)
        return False
    return True


async def settings_summary(owner: int) -> dict[str, object]:
    return {
        "enabled": await _enabled(owner),
        "chance": await _chance(owner),
        "voice": await _voice_name(owner),
        "max_chars": settings.voice_max_chars,
        "available": ai.tts_available(),
    }
