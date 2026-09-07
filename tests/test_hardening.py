"""Tests for the fixes and features added in the hardening pass.

Each test names the production problem it prevents from coming back.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from core.rich import RichMessage, demo_message, utf16_len
from core.safety import RateLimiter, normalise, screen_stranger_text
from handlers import ai, scheduler

# ── timezone ────────────────────────────────────────────────────────────────
# python:3.12-slim ships no IANA database, so ZoneInfo("Asia/Kolkata") raised
# and every scheduled message fired on UTC - 5h30m early in India.


def test_tzdata_is_a_declared_dependency() -> None:
    """The slim image has no system zoneinfo; the wheel is what fixes it."""
    requirements = Path("requirements.txt").read_text().lower()
    assert re.search(r"^tzdata", requirements, re.M), (
        "tzdata must stay in requirements.txt or schedules silently run on UTC"
    )


def test_dockerfile_installs_tzdata() -> None:
    assert "tzdata" in Path("Dockerfile").read_text()


def test_unresolvable_timezone_falls_back_without_raising(monkeypatch) -> None:
    """The old fallback was ZoneInfo("UTC"), which also raises with no tzdata."""
    monkeypatch.setenv("TIMEZONE", "Not/AZone")
    settings = Settings()
    assert settings.tz == dt.UTC  # never raises, whatever the image contains
    assert settings.timezone_ok is False
    assert "fallback" in settings.timezone_effective


def test_valid_timezone_is_used_and_reported(monkeypatch) -> None:
    monkeypatch.setenv("TIMEZONE", "Asia/Kolkata")
    settings = Settings()
    assert settings.timezone_ok is True
    assert settings.timezone_effective == "Asia/Kolkata"
    # The whole point: the offset must actually be +05:30, not +00:00.
    offset = dt.datetime(2026, 1, 1, 12, 0, tzinfo=settings.tz).utcoffset()
    assert offset == dt.timedelta(hours=5, minutes=30)


def test_bad_timezone_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    """Silent degradation is what made this bug take months to notice."""
    for key, value in (
        ("API_ID", "1"),
        ("API_HASH", "x"),
        ("BOT_TOKEN", "x"),
        ("OWNER_IDS", "1"),
        ("MONGO_URI", "mongodb://x"),
        ("ENCRYPTION_KEY", "x"),
        ("GROQ_API_KEY", "x"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TIMEZONE", "Not/AZone")
    with caplog.at_level("WARNING"):
        Settings().validate()
    assert any("UTC" in record.message for record in caplog.records)


# ── model fallback chain ────────────────────────────────────────────────────


def test_providers_are_ordered_best_first() -> None:
    ordered = sorted(ai.PROVIDERS.values(), key=lambda p: p.rank)
    assert ordered[0].key == "groq_gpt_oss_120b"
    # Ranks must be unique, or "best first" is not well defined.
    all_ranks = [p.rank for p in ai.PROVIDERS.values()]
    assert len(all_ranks) == len(set(all_ranks))


def test_available_providers_returns_best_first(monkeypatch) -> None:
    monkeypatch.setattr(ai.settings, "groq_api_key", "key")
    monkeypatch.setattr(ai.settings, "sambanova_api_key", "")
    monkeypatch.setattr(ai.settings, "nvidia_api_key", "")
    order = [p.rank for p in ai.available_providers()]
    assert order == sorted(order)


def test_retired_groq_models_are_gone() -> None:
    """Groq shut these down in 2026; they returned 404 on every message."""
    models = {p.model for p in ai.PROVIDERS.values()}
    for dead in (
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ):
        assert dead not in models


def test_requested_groq_models_are_present() -> None:
    models = {p.model for p in ai.PROVIDERS.values()}
    assert {"openai/gpt-oss-120b", "groq/compound", "qwen/qwen3.8-27b"} <= models


def test_tts_model_is_not_in_the_chat_chain() -> None:
    """Orpheus is text-to-speech: a chat completion against it always fails."""
    assert ai.TTS_MODEL == "canopylabs/orpheus-v1-english"
    assert ai.TTS_MODEL not in {p.model for p in ai.PROVIDERS.values()}


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_decommissioned_model_is_detected() -> None:
    response = _FakeResponse(
        404,
        {"error": {"message": "The model `x` has been decommissioned", "code": "model_not_found"}},
    )
    assert ai._looks_retired(response) is True


def test_ordinary_bad_request_is_not_treated_as_retired() -> None:
    response = _FakeResponse(400, {"error": {"message": "max_tokens too large"}})
    assert ai._looks_retired(response) is False


# ── stranger guardian ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Double your bitcoin profit, guaranteed trading signals!",
        "Claim your free gift now: bit.ly/abc",
        "Please verify your account OTP to avoid suspension",
        "Send me your seed phrase to restore the wallet",
    ],
)
def test_scam_messages_from_strangers_are_screened(text: str) -> None:
    assert screen_stranger_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Hey, is this Rita? We met at the conference.",
        "Hi! Are you free for a call tomorrow at 4?",
        "Sorry to bother you - quick question about the invoice.",
    ],
)
def test_genuine_strangers_still_get_a_reply(text: str) -> None:
    assert screen_stranger_text(text) == ""


def test_stranger_reply_cap_stops_the_conversation() -> None:
    limiter = RateLimiter()
    now = 0.0
    allowed = 0
    for _ in range(6):
        decision = limiter.check(1, now=now, is_stranger=True, incoming_text="hello")
        if decision.allowed:
            allowed += 1
            limiter.record(1, now=now, text=f"reply {allowed}", is_stranger=True)
        now += 3600
    assert allowed == 3  # STRANGER_MAX_REPLIES default


def test_stranger_cap_tells_the_owner() -> None:
    limiter = RateLimiter()
    now = 0.0
    for i in range(4):
        decision = limiter.check(1, now=now, is_stranger=True, incoming_text="hi")
        if decision.allowed:
            limiter.record(1, now=now, text=f"r{i}", is_stranger=True)
        now += 3600
    assert "unknown sender" in decision.notify_owner


def test_trusting_a_chat_lifts_the_cap() -> None:
    limiter = RateLimiter()
    now = 0.0
    for i in range(3):
        limiter.check(1, now=now, is_stranger=True)
        limiter.record(1, now=now, text=f"r{i}", is_stranger=True)
        now += 3600
    assert not limiter.check(1, now=now, is_stranger=True).allowed
    limiter.trust(1)
    assert limiter.check(1, now=now, is_stranger=True).allowed


def test_strangers_wait_much_longer_than_friends() -> None:
    limiter = RateLimiter()
    friend = limiter.check(1, now=0.0)
    limiter_b = RateLimiter()
    stranger = limiter_b.check(2, now=0.0, is_stranger=True)
    assert stranger.extra_delay > friend.extra_delay + 10


# ── repetition ──────────────────────────────────────────────────────────────


def test_identical_message_is_not_sent_twice() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="Hey! I'll get back to you soon.")
    assert not limiter.allow_text(1, "Hey! I'll get back to you soon.", now=10.0).allowed


def test_near_identical_message_is_also_blocked() -> None:
    """Punctuation and case changes must not defeat the duplicate guard."""
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="Hey! I'll get back to you soon.")
    assert not limiter.allow_text(1, "hey, ill get back to you soon!!", now=10.0).allowed


def test_a_different_message_is_allowed() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="Hey! I'll get back to you soon.")
    assert limiter.allow_text(1, "Sure, see you at 8.", now=10.0).allowed


def test_duplicate_guard_is_per_chat() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="On my way")
    assert limiter.allow_text(2, "On my way", now=10.0).allowed


def test_duplicate_guard_expires() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="On my way")
    assert limiter.allow_text(1, "On my way", now=10_000.0).allowed


def test_repeated_incoming_message_stops_the_ping_pong() -> None:
    """Answering a bot that repeats itself is an unbounded loop."""
    limiter = RateLimiter()
    now = 0.0
    answered = 0
    for _ in range(6):
        limiter.note_incoming(1, "ping")
        if limiter.check(1, now=now, incoming_text="ping").allowed:
            answered += 1
            limiter.record(1, now=now, text=f"pong {answered}")
        now += 3600
    assert answered == 2  # ECHO_LOOP_THRESHOLD default of 3 stops the third


def test_varied_incoming_messages_reset_the_loop_counter() -> None:
    limiter = RateLimiter()
    limiter.note_incoming(1, "ping")
    limiter.note_incoming(1, "ping")
    assert limiter.note_incoming(1, "something else") == 1


def test_normalise_folds_trivial_differences() -> None:
    assert normalise("Hey!!  How are you?") == normalise("hey how are you")


# ── pacing ──────────────────────────────────────────────────────────────────


def test_account_wide_gap_applies_across_different_chats() -> None:
    """A burst spread over many chats is still a burst to Telegram."""
    limiter = RateLimiter()
    limiter.record(1, now=100.0, text="hi")
    decision = limiter.check(2, now=101.0)
    assert decision.allowed
    assert decision.extra_delay >= 6.0


def test_burst_damping_grows_with_volume() -> None:
    quiet = RateLimiter()
    quiet.record(1, now=0.0, text="a")
    busy = RateLimiter()
    for i in range(15):
        busy.record(i, now=0.0, text=f"m{i}")
    assert busy.check(99, now=1.0).extra_delay > quiet.check(99, now=1.0).extra_delay


def test_quiet_hours_multiply_the_delay() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="a")
    normal = limiter.check(2, now=1.0).extra_delay
    night = limiter.check(2, now=1.0, quiet_hours=True).extra_delay
    assert night > normal


def test_snapshot_reports_the_guardian_state() -> None:
    limiter = RateLimiter()
    limiter.record(1, now=0.0, text="hi", is_stranger=True)
    snapshot = limiter.snapshot()
    assert snapshot["strangers_answered"] == 1
    assert "stranger_max_replies" in snapshot["limits"]
    assert "global_min_gap_s" in snapshot["limits"]


# ── rich messages ───────────────────────────────────────────────────────────


def _slice_utf16(text: str, offset: int, length: int) -> str:
    raw = text.encode("utf-16-le")
    return raw[offset * 2 : (offset + length) * 2].decode("utf-16-le")


def test_entity_offsets_are_utf16_not_python_characters() -> None:
    """Emoji outside the BMP are 2 UTF-16 units; len() shifts every entity."""
    message = RichMessage().text_("🛡️😄🎉 ").bold("BOLD").text_(" tail")
    text, entities = message.build()
    assert _slice_utf16(text, entities[0].offset, entities[0].length) == "BOLD"
    # And prove the naive version really would have been wrong.
    assert text[entities[0].offset : entities[0].offset + entities[0].length] != "BOLD"


def test_every_demo_entity_lands_on_real_text() -> None:
    text, entities = demo_message().build()
    assert entities
    for entity in entities:
        assert _slice_utf16(text, entity.offset, entity.length).strip()


def test_demo_covers_spoiler_and_expandable_quote() -> None:
    """These two are exactly what Telethon's HTML parser cannot express."""
    _, entities = demo_message().build()
    names = [type(e).__name__ for e in entities]
    assert "MessageEntitySpoiler" in names
    collapsed = [
        e for e in entities if type(e).__name__ == "MessageEntityBlockquote" and e.collapsed
    ]
    assert collapsed


def test_utf16_len_counts_surrogate_pairs() -> None:
    assert utf16_len("a") == 1
    assert utf16_len("😄") == 2


@pytest.mark.asyncio
async def test_send_rich_falls_back_to_plain_text_on_entity_error() -> None:
    from core.rich import send_rich

    client = AsyncMock()
    client.send_message.side_effect = [ValueError("bad entity"), "sent"]
    result = await send_rich(client, 1, RichMessage().bold("hi"))
    assert result == "sent"
    assert client.send_message.call_count == 2
    # The retry must not carry the entities that just failed.
    assert "formatting_entities" not in client.send_message.call_args.kwargs


# ── greetings are always generated ──────────────────────────────────────────


def test_no_hardcoded_greetings_remain() -> None:
    source = Path("handlers/scheduler.py").read_text()
    assert "_FALLBACKS" not in source
    for canned in ("Good morning", "Goodnight", "Sleep well", "Morning!"):
        assert canned not in source


@pytest.mark.asyncio
async def test_greeting_is_skipped_when_every_provider_fails(monkeypatch) -> None:
    """It must not fall back to a canned line - silence is the fallback."""
    monkeypatch.setattr(scheduler, "_RETRY_DELAY", 0)
    with (
        patch.object(scheduler.ai, "_complete", AsyncMock(return_value=(None, ""))),
        patch.object(scheduler.mongo, "get_persona_key", AsyncMock(return_value="casual")),
    ):
        assert await scheduler._compose("morning", 1) is None


@pytest.mark.asyncio
async def test_greeting_is_regenerated_when_the_model_repeats_itself(monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "_RETRY_DELAY", 0)
    scheduler._recent[7] = ["Good morning!"]
    replies = [("Good morning!", "p"), ("Morning — coffee first, then chaos.", "p")]
    with (
        patch.object(scheduler.ai, "_complete", AsyncMock(side_effect=replies)),
        patch.object(scheduler.mongo, "get_persona_key", AsyncMock(return_value="casual")),
    ):
        result = await scheduler._compose("morning", 7)
    assert result == "Morning — coffee first, then chaos."
    scheduler._recent.clear()


@pytest.mark.asyncio
async def test_composer_tells_the_model_what_it_already_sent(monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "_RETRY_DELAY", 0)
    scheduler._recent[9] = ["Rise and shine!"]
    complete = AsyncMock(return_value=("Fresh line", "p"))
    with (
        patch.object(scheduler.ai, "_complete", complete),
        patch.object(scheduler.mongo, "get_persona_key", AsyncMock(return_value="casual")),
    ):
        await scheduler._compose("morning", 9)
    system_prompt = complete.call_args.args[0][0]["content"]
    assert "Rise and shine!" in system_prompt
    scheduler._recent.clear()


# ── voice replies ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_is_off_by_default(monkeypatch) -> None:
    from core import voice

    with patch.object(voice.mongo, "get_setting", AsyncMock(return_value=None)):
        monkeypatch.setattr(voice.settings, "voice_replies", False)
        assert await voice.should_speak("Sure, see you at 8.") is False


@pytest.mark.asyncio
async def test_voice_never_goes_to_a_stranger(monkeypatch) -> None:
    """Audio to someone unknown is intrusive and gets reported."""
    from core import voice

    with patch.object(voice.mongo, "get_setting", AsyncMock(return_value=True)):
        monkeypatch.setattr(voice.settings, "voice_replies", True)
        assert await voice.should_speak("Hi there", is_stranger=True) is False


@pytest.mark.asyncio
async def test_long_replies_are_never_spoken(monkeypatch) -> None:
    """Groq's speech endpoint rejects input over 200 characters."""
    from core import voice

    async def enabled(key, default=None):
        return {"voice_replies": True, "voice_chance": 1.0}.get(key, default)

    with (
        patch.object(voice.mongo, "get_setting", AsyncMock(side_effect=enabled)),
        patch.object(voice.ai, "tts_available", lambda: True),
    ):
        assert await voice.should_speak("x" * 201) is False
        assert await voice.should_speak("Short and sweet.") is True


@pytest.mark.asyncio
async def test_multiline_replies_are_not_spoken(monkeypatch) -> None:
    from core import voice

    async def enabled(key, default=None):
        return {"voice_replies": True, "voice_chance": 1.0}.get(key, default)

    with (
        patch.object(voice.mongo, "get_setting", AsyncMock(side_effect=enabled)),
        patch.object(voice.ai, "tts_available", lambda: True),
    ):
        assert await voice.should_speak("line one\nline two") is False


@pytest.mark.asyncio
async def test_failed_synthesis_falls_back_to_text() -> None:
    """A broken TTS setup must never cost a reply."""
    from core import voice

    with (
        patch.object(voice.ai, "synthesize", AsyncMock(return_value=None)),
        patch.object(voice.mongo, "get_setting", AsyncMock(return_value=None)),
    ):
        assert await voice.send_as_voice(AsyncMock(), 1, "hello") is False


@pytest.mark.asyncio
async def test_ogg_is_sent_as_a_real_voice_note() -> None:
    from core import voice

    client = AsyncMock()
    with (
        patch.object(voice.ai, "synthesize", AsyncMock(return_value=(b"OggS...", "ogg"))),
        patch.object(voice.mongo, "get_setting", AsyncMock(return_value=None)),
    ):
        assert await voice.send_as_voice(client, 1, "hello") is True
    assert client.send_file.call_args.kwargs["voice_note"] is True


@pytest.mark.asyncio
async def test_wav_is_sent_as_an_audio_file_not_a_voice_note() -> None:
    """Telegram only renders OGG/Opus as a voice note."""
    from core import voice

    client = AsyncMock()
    with (
        patch.object(voice.ai, "synthesize", AsyncMock(return_value=(b"RIFF...", "wav"))),
        patch.object(voice.mongo, "get_setting", AsyncMock(return_value=None)),
    ):
        assert await voice.send_as_voice(client, 1, "hello") is True
    assert client.send_file.call_args.kwargs["voice_note"] is False


@pytest.mark.asyncio
async def test_synthesis_is_skipped_over_the_character_limit(monkeypatch) -> None:
    monkeypatch.setattr(ai.settings, "groq_api_key", "key")
    assert await ai.synthesize("x" * 500) is None


def test_configured_voice_is_a_real_orpheus_voice() -> None:
    from config import Settings

    assert Settings().voice_name in ai.TTS_VOICES
