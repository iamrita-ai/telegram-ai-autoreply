"""Tests for configuration, personas, safety rails and reply logic.

Each test names the production problem it prevents from coming back.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from config import ConfigError, Settings
from core.humanize import detect_sentiment, is_quiet_hours, parse_quiet_hours
from core.personas import PERSONAS, build_system_prompt, get_persona
from core.safety import RateLimiter
from handlers.ai import _tidy, seems_incomplete

# ── configuration ───────────────────────────────────────────────────────────


def test_missing_configuration_is_reported_all_at_once(monkeypatch) -> None:
    """It used to crash on import with a stack trace about a bad Mongo URI."""
    for key in (
        "API_ID",
        "API_HASH",
        "BOT_TOKEN",
        "OWNER_IDS",
        "MONGO_URI",
        "ENCRYPTION_KEY",
        "GROQ_API_KEY",
        "SAMBANOVA_API_KEY",
        "NVIDIA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigError) as excinfo:
        Settings().validate()

    message = str(excinfo.value)
    for expected in ("API_ID", "BOT_TOKEN", "OWNER_IDS", "MONGO_URI", "ENCRYPTION_KEY"):
        assert expected in message


def test_owner_ids_parse_from_a_comma_list(monkeypatch) -> None:
    monkeypatch.setenv("OWNER_IDS", " 111 , 222;333 ")
    settings = Settings()
    assert settings.owner_ids == (111, 222, 333)
    assert settings.is_owner(222)
    assert not settings.is_owner(999)


def test_owner_ids_are_not_hardcoded() -> None:
    """Two user ids used to be baked into config.py."""
    source = (__import__("pathlib").Path(__file__).parent.parent / "config.py").read_text()
    assert "1598576202" not in source
    assert "6518065496" not in source


def test_an_unknown_timezone_falls_back_to_utc(monkeypatch) -> None:
    monkeypatch.setenv("TIMEZONE", "Mars/Olympus")
    assert str(Settings().tz) == "UTC"


def test_describe_never_leaks_secrets(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecret")
    monkeypatch.setenv("ENCRYPTION_KEY", "fernet-secret")
    described = str(Settings().describe())
    assert "supersecret" not in described
    assert "fernet-secret" not in described


# ── personas ────────────────────────────────────────────────────────────────


def test_three_personalities_are_available() -> None:
    assert set(PERSONAS) == {"professional", "casual", "romantic"}


@pytest.mark.parametrize("key", ["professional", "casual", "romantic"])
def test_every_persona_enforces_the_house_rules(key: str) -> None:
    prompt = build_system_prompt(key)
    assert "English only" in prompt
    assert "one emoji" in prompt
    # The account must never out itself as automated.
    assert "never say or imply that you are an ai" in prompt.lower()


def test_unknown_persona_falls_back_instead_of_raising() -> None:
    assert get_persona("nonsense").key == "casual"
    assert get_persona(None).key == "casual"


def test_custom_prompt_overrides_the_persona_but_keeps_the_rules() -> None:
    prompt = build_system_prompt("romantic", "You are a pirate.")
    assert "pirate" in prompt
    assert "romantic" not in prompt.lower().split("hard rules")[0]
    assert "English only" in prompt


def test_no_persona_is_written_in_hinglish() -> None:
    """The bot was half Hindi; the owner asked for English only."""
    banned = ("hun", "karo", "yaar", "baat", "kal", "nahi", "bhai")
    for persona in PERSONAS.values():
        words = set(persona.prompt.lower().replace(",", " ").split())
        assert not words & set(banned), f"{persona.key} still contains Hinglish"


# ── safety rails ────────────────────────────────────────────────────────────


def test_a_second_reply_to_the_same_chat_is_throttled() -> None:
    limiter = RateLimiter(per_chat_cooldown=5.0)
    limiter.record(1, now=100.0)
    assert not limiter.check(1, now=101.0).allowed
    assert limiter.check(1, now=106.0).allowed


def test_per_chat_hourly_cap_is_enforced() -> None:
    limiter = RateLimiter(per_chat_cooldown=0, per_chat_hourly_limit=3)
    for i in range(3):
        limiter.record(7, now=100.0 + i)
    decision = limiter.check(7, now=110.0)
    assert not decision.allowed
    assert "per-chat hourly" in decision.reason


def test_global_hourly_cap_protects_the_account() -> None:
    limiter = RateLimiter(per_chat_cooldown=0, global_hourly_limit=2)
    limiter.record(1, now=100.0)
    limiter.record(2, now=101.0)
    assert not limiter.check(3, now=102.0).allowed


def test_counters_expire_after_an_hour() -> None:
    limiter = RateLimiter(per_chat_cooldown=0, global_hourly_limit=1)
    limiter.record(1, now=100.0)
    assert not limiter.check(2, now=200.0).allowed
    assert limiter.check(2, now=100.0 + 3601).allowed


def test_a_brand_new_chat_gets_an_extra_pause() -> None:
    """An instant reply to a stranger is the clearest automation tell."""
    limiter = RateLimiter(new_chat_extra_delay=60.0)
    first = limiter.check(42, now=100.0)
    assert first.allowed and first.extra_delay > 30

    # A chat we have spoken to before is answered sooner - though never
    # instantly, because burst damping and the account-wide gap still apply.
    limiter.record(42, now=100.0)
    later = limiter.check(42, now=500.0)
    assert later.allowed and later.extra_delay < 30


def test_a_chat_being_replied_to_is_not_answered_twice() -> None:
    limiter = RateLimiter(per_chat_cooldown=0)
    limiter.hold(5)
    assert not limiter.check(5, now=100.0).allowed
    limiter.release(5)
    assert limiter.check(5, now=100.0).allowed


def test_snapshot_reports_usage_against_the_limits() -> None:
    limiter = RateLimiter(per_chat_cooldown=0)
    snapshot = limiter.snapshot()
    assert "replies_last_hour" in snapshot
    assert snapshot["limits"]["global_daily"] > 0


# ── quiet hours ─────────────────────────────────────────────────────────────


def test_quiet_hours_window_wrapping_midnight() -> None:
    """23:00-07:00 is the normal case and must not be treated as empty."""
    at = lambda h, m=0: dt.datetime(2026, 1, 1, h, m)  # noqa: E731
    assert is_quiet_hours("23:00-07:00", now=at(23, 30))
    assert is_quiet_hours("23:00-07:00", now=at(3))
    assert not is_quiet_hours("23:00-07:00", now=at(12))


def test_quiet_hours_within_one_day() -> None:
    at = lambda h: dt.datetime(2026, 1, 1, h)  # noqa: E731
    assert is_quiet_hours("09:00-17:00", now=at(12))
    assert not is_quiet_hours("09:00-17:00", now=at(20))


def test_malformed_quiet_hours_never_silence_the_bot() -> None:
    for value in ("", None, "garbage", "25:00-30:00", "9-5"):
        assert parse_quiet_hours(value) is None
        assert not is_quiet_hours(value)


# ── fragment detection ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["hey", "so", "i was thinking and", "hi there"])
def test_fragments_are_held(text: str) -> None:
    assert seems_incomplete(text)


@pytest.mark.parametrize(
    "text",
    ["what time is it?", "hey, how are you doing today", "stop.", "that is amazing!"],
)
def test_complete_thoughts_are_answered_immediately(text: str) -> None:
    assert not seems_incomplete(text)


async def test_a_held_fragment_is_still_answered_eventually() -> None:
    """A short message used to be buffered and then lost forever."""
    from handlers import ai

    with (
        patch.object(ai.mongo, "get_pending", AsyncMock(return_value=["hey"])),
        patch.object(ai.mongo, "clear_pending", AsyncMock()),
        patch.object(ai.mongo, "get_persona_key", AsyncMock(return_value="casual")),
        patch.object(ai.mongo, "get_prompt", AsyncMock(return_value=None)),
        patch.object(ai.mongo, "get_conversation", AsyncMock(return_value=[])),
        patch.object(ai.mongo, "add_message", AsyncMock()),
        patch.object(ai, "_complete", AsyncMock(return_value=("hey you", "groq_70b"))),
    ):
        result = await ai.flush_stale_fragment(1)

    assert result.text == "hey you"


async def test_a_fragment_followed_by_more_text_is_answered_once() -> None:
    from handlers import ai

    with (
        patch.object(ai.mongo, "get_pending", AsyncMock(return_value=["hey"])),
        patch.object(ai.mongo, "clear_pending", AsyncMock()) as cleared,
        patch.object(ai.mongo, "get_persona_key", AsyncMock(return_value="casual")),
        patch.object(ai.mongo, "get_prompt", AsyncMock(return_value=None)),
        patch.object(ai.mongo, "get_conversation", AsyncMock(return_value=[])),
        patch.object(ai.mongo, "add_message", AsyncMock()),
        patch.object(ai, "_complete", AsyncMock(return_value=("sure", "groq_70b"))) as call,
    ):
        result = await ai.generate_reply(1, "are you free tonight?")

    assert result.text == "sure"
    cleared.assert_awaited_once()
    # The held fragment must be prepended to the new message, not dropped.
    sent = call.await_args.args[0][-1]["content"]
    assert sent == "hey are you free tonight?"


# ── reply tidying ───────────────────────────────────────────────────────────


def test_model_preambles_are_stripped() -> None:
    assert _tidy("Assistant: sure thing") == "sure thing"
    assert _tidy('"quoted reply"') == "quoted reply"


def test_essays_are_cut_down_to_chat_length() -> None:
    long_reply = "\n".join(f"line {i}" for i in range(20))
    assert len(_tidy(long_reply).splitlines()) <= 4


# ── sentiment ───────────────────────────────────────────────────────────────


def test_sentiment_picks_a_reaction_bucket() -> None:
    assert detect_sentiment("this is great, thanks!") == "positive"
    assert detect_sentiment("that is awful and sad") == "negative"
    assert detect_sentiment("meeting at four") == "neutral"


def test_sentiment_matches_whole_words_only() -> None:
    """Substring matching made "badminton" negative and "goodbye" positive."""
    assert detect_sentiment("badminton practice") == "neutral"
