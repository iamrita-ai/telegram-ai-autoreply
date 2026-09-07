"""Tests for staying awake, contact pacing, length-scaled delay and bot safety.

Each test names the production behaviour it protects:

* the service was being suspended by the host after a few quiet minutes,
  mid-conversation ("shutting down / bye" three minutes after a reply);
* saved contacts were being rate limited as if they were strangers;
* every reply waited roughly the same time, whatever its length;
* answering another bot must be impossible.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

import main
from config import settings
from core.humanize import typing_plan
from core.safety import RateLimiter
from handlers import userbot

# ── keep-alive: the service must not be idled out ───────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://app.onrender.com", "https://app.onrender.com/healthz"),
        ("https://app.onrender.com/", "https://app.onrender.com/healthz"),
        ("app.onrender.com", "https://app.onrender.com/healthz"),
        ("https://app.onrender.com/healthz", "https://app.onrender.com/healthz"),
        ("https://app.onrender.com/health", "https://app.onrender.com/health"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_keepalive_url_is_normalised(raw: str, expected: str) -> None:
    assert main.keepalive_target(raw) == expected


async def test_keepalive_actually_sends_http_requests() -> None:
    """The real loop against a real server - not a mocked client session.

    Render only counts *inbound HTTP* traffic when deciding whether a service
    is idle. If this loop ever silently stops making requests, the account
    goes offline after 15 quiet minutes and nobody finds out until somebody
    complains that the bot ignored them.
    """
    hits: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        hits.append(request.path)
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/healthz", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = next(iter(runner.addresses))[1]

    task = asyncio.create_task(main._keepalive_loop(f"http://127.0.0.1:{port}/healthz", 0.05))
    try:
        for _ in range(100):
            if len(hits) >= 2:
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await runner.cleanup()

    assert len(hits) >= 2, "keep-alive made no requests; the host will suspend the service"
    assert hits[0] == "/healthz"
    assert main._keepalive_state["ok"] is True


async def test_keepalive_survives_a_failed_ping() -> None:
    """A ping to a dead port must not kill the task - it retries."""
    task = asyncio.create_task(main._keepalive_loop("http://127.0.0.1:1/healthz", 0.05))
    await asyncio.sleep(0.4)
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert still_running
    assert main._keepalive_state["ok"] is False


def test_keepalive_is_skipped_when_no_public_url_is_known(monkeypatch) -> None:
    monkeypatch.setattr(settings, "keepalive_url", "", raising=False)
    spawned: list[str] = []
    monkeypatch.setattr(main, "_spawn", lambda coro, name: spawned.append(name) or coro.close())
    main.start_keepalive()
    assert spawned == []


def test_keepalive_starts_when_render_supplies_the_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "keepalive", True, raising=False)
    monkeypatch.setattr(settings, "keepalive_url", "https://x.onrender.com", raising=False)
    spawned: list[str] = []
    monkeypatch.setattr(main, "_spawn", lambda coro, name: spawned.append(name) or coro.close())
    main.start_keepalive()
    assert spawned == ["keepalive"]
    assert main._keepalive_state["url"] == "https://x.onrender.com/healthz"


def test_render_yaml_does_not_disable_keepalive() -> None:
    from pathlib import Path

    text = Path("render.yaml").read_text().lower()
    assert "keepalive: false" not in text.replace('"', "")


# ── delay scales with reply length ──────────────────────────────────────────


def test_a_short_reply_is_typed_quickly() -> None:
    plan = typing_plan("ok", "hey", jitter=False)
    assert plan.total < 3.0, "a two-letter reply should not take seconds of 'typing'"


def test_a_long_reply_takes_much_longer_than_a_short_one() -> None:
    short = typing_plan("yeah", "hey", jitter=False)
    medium = typing_plan("Sure, I can do that around six if it works for you.", "hey", jitter=False)
    long = typing_plan("x " * 200, "hey", jitter=False)
    assert short.total < medium.total < long.total
    assert long.type > 4 * short.type


def test_typing_time_is_monotonic_in_reply_length() -> None:
    times = [typing_plan("x" * n, jitter=False).type for n in (1, 20, 60, 120, 240, 480)]
    assert times == sorted(times)
    assert times[0] == settings.min_typing_time  # floor: never instant
    assert times[-1] == settings.max_typing_time  # ceiling: never absurd


def test_reading_time_follows_the_incoming_message() -> None:
    quick = typing_plan("sure", "hi", jitter=False)
    slow = typing_plan("sure", "hi " * 400, jitter=False)
    assert slow.read > quick.read
    assert slow.read <= settings.max_reading_time * 1.1


def test_jitter_keeps_the_plan_in_a_sane_range() -> None:
    for _ in range(200):
        plan = typing_plan("a normal length reply, nothing unusual", "and a question?")
        assert 0 < plan.total < 25
        assert plan.type >= settings.min_typing_time * 0.99


# ── contacts are not rate limited ───────────────────────────────────────────


def test_a_contact_is_not_held_by_the_per_chat_cooldown() -> None:
    limiter = RateLimiter()
    limiter.record(5, now=1000.0, text="first")
    assert limiter.check(5, now=1000.5).allowed is False  # a stranger would wait
    assert limiter.check(5, now=1000.5, is_contact=True).allowed is True


def test_a_contact_can_pass_the_hourly_and_daily_caps() -> None:
    limiter = RateLimiter()
    limiter.per_chat_hourly_limit = 2
    limiter.global_hourly_limit = 2
    limiter.global_daily_limit = 2
    for i in range(3):
        limiter.record(7, now=1000.0 + i)
    assert limiter.check(7, now=2000.0).allowed is False
    assert limiter.check(7, now=2000.0, is_contact=True).allowed is True


def test_a_contact_still_cannot_be_answered_twice_at_once() -> None:
    """The in-flight guard is not a volume limit - it stops double sends."""
    limiter = RateLimiter()
    limiter.hold(9)
    assert limiter.check(9, is_contact=True).allowed is False


def test_a_contact_still_cannot_be_sent_the_same_text_twice() -> None:
    limiter = RateLimiter()
    limiter.record(9, now=1000.0, text="see you tomorrow")
    assert limiter.allow_text(9, "see you tomorrow!", now=1001.0).allowed is False


def test_a_contact_still_trips_the_echo_loop_guard() -> None:
    limiter = RateLimiter()
    for _ in range(4):
        limiter.note_incoming(9, "hello?")
    decision = limiter.check(9, is_contact=True, incoming_text="hello?")
    assert decision.allowed is False
    assert "repeated" in decision.reason


def test_a_contact_waits_far_less_before_replying() -> None:
    limiter = RateLimiter()
    for i in range(10):
        limiter.record(100 + i, now=1000.0 + i)
    stranger_side = limiter.check(500, now=1010.0).extra_delay
    contact_side = limiter.check(501, now=1010.0, is_contact=True).extra_delay
    assert contact_side < stranger_side
    assert contact_side < 7.0, "a friend should not wait half a minute for a reply"


def test_a_stranger_is_never_treated_as_a_contact() -> None:
    """is_stranger wins: an unknown sender does not get the fast path."""
    limiter = RateLimiter()
    limiter.record(5, now=1000.0)
    assert limiter.check(5, now=1000.5, is_contact=True, is_stranger=True).allowed is False


def test_contact_exemption_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "contact_unlimited", False, raising=False)
    limiter = RateLimiter()
    limiter.record(5, now=1000.0)
    assert limiter.check(5, now=1000.5, is_contact=True).allowed is False


# ── never, ever reply to a bot ──────────────────────────────────────────────


def _user(**kwargs) -> types.SimpleNamespace:
    base = {
        "bot": False,
        "deleted": False,
        "support": False,
        "scam": False,
        "fake": False,
        "contact": False,
        "mutual_contact": False,
        "username": "someone",
    }
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def _event(**kwargs) -> types.SimpleNamespace:
    base = {"via_bot_id": None, "post": False, "sender_id": 4242}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("sender", "event", "expect_reason"),
    [
        (_user(bot=True), _event(), "sender is a bot"),
        (_user(username="Helper_Bot"), _event(), "username looks like a bot"),
        (_user(deleted=True), _event(), "deleted account"),
        (_user(support=True), _event(), "Telegram support account"),
        (_user(scam=True), _event(), "flagged by Telegram"),
        (_user(fake=True), _event(), "flagged by Telegram"),
        (_user(), _event(via_bot_id=777), "inline bot"),
        (_user(), _event(post=True), "channel post"),
        (_user(), _event(sender_id=777000), "service account"),
        (_user(), _event(sender_id=1087968824), "service account"),
        (None, _event(), "no resolvable sender"),
        ("not a user object", _event(), "not a user account"),
    ],
)
def test_these_senders_are_never_answered(sender, event, expect_reason: str) -> None:
    with patch("handlers.userbot.User", types.SimpleNamespace):
        reason = userbot.ignore_reason(event, sender)
    assert expect_reason in reason


def test_a_real_person_is_answered() -> None:
    with patch("handlers.userbot.User", types.SimpleNamespace):
        assert userbot.ignore_reason(_event(), _user(username="priya")) == ""


def test_a_contact_whose_handle_ends_in_bot_is_still_answered() -> None:
    """Rare, but a person called @talkbot is a person."""
    sender = _user(username="talkbot", contact=True)
    with patch("handlers.userbot.User", types.SimpleNamespace):
        assert userbot.ignore_reason(_event(), sender) == ""


def test_is_contact_reads_both_telethon_flags() -> None:
    assert userbot.is_contact(_user(contact=True)) is True
    assert userbot.is_contact(_user(mutual_contact=True)) is True
    assert userbot.is_contact(_user()) is False
    assert userbot.is_contact(None) is False


class _FakeEvent:
    """Enough of a Telethon event for the real _handle to run."""

    def __init__(self, sender, text: str = "hello there") -> None:
        self._sender = sender
        self.text = text
        self.out = False
        self.is_private = True
        self.chat_id = 555
        self.sender_id = 4242
        self.id = 1
        self.mentioned = False
        self.via_bot_id = None
        self.post = False
        self.replies: list[str] = []

    async def get_sender(self):
        return self._sender

    async def reply(self, text: str) -> None:
        self.replies.append(text)


async def test_the_real_handler_stays_silent_for_a_bot() -> None:
    """End-to-end through _handle, with only the database and AI stubbed."""
    event = _FakeEvent(_user(bot=True, username="some_bot"))
    generate = AsyncMock()
    with (
        patch("handlers.userbot.User", types.SimpleNamespace),
        patch("handlers.userbot.mongo.is_locked", AsyncMock(return_value=False)),
        patch("handlers.userbot.mongo.is_blacklisted", AsyncMock(return_value=False)),
        patch("handlers.userbot.mongo.get_dnd", AsyncMock(return_value="")),
        patch("handlers.userbot.ai.generate_reply", generate),
    ):
        await userbot._handle(object(), event, owner=1, me_id=99, display_name="me")

    assert event.replies == []
    generate.assert_not_awaited(), "the AI was called for a bot - it must be filtered first"


async def test_the_real_handler_answers_a_person() -> None:
    """The mirror of the test above: the same path must still work."""
    event = _FakeEvent(_user(username="priya", contact=True))
    reply = types.SimpleNamespace(text="hey! all good?", buffered=False, provider="groq")
    with (
        patch("handlers.userbot.User", types.SimpleNamespace),
        patch("handlers.userbot.mongo.is_locked", AsyncMock(return_value=False)),
        patch("handlers.userbot.mongo.is_blacklisted", AsyncMock(return_value=False)),
        patch("handlers.userbot.mongo.get_dnd", AsyncMock(return_value="")),
        patch("handlers.userbot.mongo.get_conversation", AsyncMock(return_value=[{"a": 1}])),
        patch("handlers.userbot.mongo.increment_stat", AsyncMock()),
        patch("handlers.userbot.mongo.increment_today", AsyncMock()),
        patch("handlers.userbot.ai.generate_reply", AsyncMock(return_value=reply)),
        patch("handlers.userbot.send_reaction", AsyncMock()),
        patch("handlers.userbot.simulate_typing", AsyncMock()),
        patch("handlers.userbot.voice.should_speak", AsyncMock(return_value=False)),
    ):
        await userbot._handle(object(), event, owner=1, me_id=99, display_name="me")

    assert event.replies == ["hey! all good?"]


def test_the_scheduler_refuses_to_greet_a_bot() -> None:
    import inspect

    from handlers import scheduler

    source = inspect.getsource(scheduler._tick)
    assert 'getattr(entity, "bot", False)' in source
    assert "continue" in source.split('getattr(entity, "bot", False)')[1][:200]
