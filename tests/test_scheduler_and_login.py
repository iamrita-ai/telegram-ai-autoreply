"""Tests for the scheduler and the control-bot login flow."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from core.safety import limiter
from handlers import scheduler
from handlers.control import _normalise_phone

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _clean_scheduler_state():
    scheduler._sent_today.clear()
    scheduler._recent.clear()
    limiter.reset()
    yield
    scheduler._sent_today.clear()
    scheduler._recent.clear()
    limiter.reset()


def _unique_greetings():
    """The composer never returns the same text twice, so nor should the mock.

    The scheduler now refuses to send a greeting that duplicates a recent one,
    which is the point of the duplicate guard - a fixed string every morning
    is what makes an account look automated.
    """
    lines = iter(
        [
            "Morning, take 1!",
            "Hope the day is kind to you.",
            "Up early? Coffee is mandatory.",
            "Sun is out, so am I.",
            "Another one - let us make it count.",
        ]
        * 10
    )
    return AsyncMock(side_effect=lambda *a, **k: next(lines))


def _schedule(user_id: int = 42, kind: str = "morning", when: str = "08:00"):
    return [{"user_id": user_id, "type": kind, "time": when, "active": True}]


def _patched(schedules, client, locked=False):
    return (
        patch.object(scheduler.mongo, "get_active_schedules", AsyncMock(return_value=schedules)),
        patch.object(scheduler.mongo, "is_locked", AsyncMock(return_value=locked)),
        patch.object(scheduler.mongo, "increment_stat", AsyncMock()),
        patch.object(scheduler.mongo, "increment_today", AsyncMock()),
        patch.object(scheduler, "_compose", _unique_greetings()),
    )


async def test_a_due_message_is_sent() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    now = dt.datetime(2026, 1, 1, 8, 0, tzinfo=IST)

    patches = _patched(_schedule(), client)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        sent = await scheduler._tick(client, now=now)

    assert sent == 1
    client.send_message.assert_awaited_once_with(42, "Morning, take 1!")


async def test_the_same_schedule_never_fires_twice_in_one_day() -> None:
    """The loop polls faster than a minute, so it used to double-send."""
    client = MagicMock()
    client.send_message = AsyncMock()
    now = dt.datetime(2026, 1, 1, 8, 0, tzinfo=IST)

    patches = _patched(_schedule(), client)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        first = await scheduler._tick(client, now=now)
        second = await scheduler._tick(client, now=now + dt.timedelta(seconds=30))

    assert (first, second) == (1, 0)
    assert client.send_message.await_count == 1


async def test_the_same_schedule_fires_again_the_next_day() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    patches = _patched(_schedule(), client)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        await scheduler._tick(client, now=dt.datetime(2026, 1, 1, 8, 0, tzinfo=IST))
        await scheduler._tick(client, now=dt.datetime(2026, 1, 2, 8, 0, tzinfo=IST))

    assert client.send_message.await_count == 2


async def test_schedules_use_local_time_not_server_utc() -> None:
    """Render runs on UTC: 08:00 IST fired at 13:30 local for the owner."""
    client = MagicMock()
    client.send_message = AsyncMock()
    patches = _patched(_schedule(when="08:00"), client)

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        # 08:00 IST is 02:30 UTC. Matching must happen in IST.
        utc_equivalent = dt.datetime(2026, 1, 1, 2, 30, tzinfo=dt.UTC)
        sent = await scheduler._tick(client, now=utc_equivalent.astimezone(IST))

    assert sent == 1


async def test_nothing_is_sent_while_paused() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    patches = _patched(_schedule(), client, locked=True)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        sent = await scheduler._tick(client, now=dt.datetime(2026, 1, 1, 8, 0, tzinfo=IST))

    assert sent == 0
    client.send_message.assert_not_awaited()


async def test_a_blocked_recipient_is_not_retried_every_30_seconds() -> None:
    """Hammering someone who blocked the account is how accounts get limited."""
    client = MagicMock()
    client.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
    patches = _patched(_schedule(), client)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        now = dt.datetime(2026, 1, 1, 8, 0, tzinfo=IST)
        await scheduler._tick(client, now=now)
        await scheduler._tick(client, now=now + dt.timedelta(seconds=30))

    assert client.send_message.await_count == 1


async def test_greeting_avoids_repeating_recent_messages() -> None:
    scheduler._remember(42, "Morning!")
    scheduler._remember(42, "Good morning ☀️")
    assert scheduler._recent[42] == ["Morning!", "Good morning ☀️"]

    with (
        patch.object(scheduler.ai, "_complete", AsyncMock(return_value=(None, ""))),
        patch.object(scheduler.mongo, "get_persona_key", AsyncMock(return_value="casual")),
    ):
        text = await scheduler._compose("morning", 42)

    assert text not in ("Morning!", "Good morning ☀️")


def test_recent_greetings_do_not_grow_without_bound() -> None:
    for i in range(50):
        scheduler._remember(1, f"message {i}")
    assert len(scheduler._recent[1]) == 10


# ── login helpers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("9876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("+91-98765-43210", "+919876543210"),
    ],
)
def test_phone_numbers_are_normalised_to_e164(raw: str, expected: str) -> None:
    assert _normalise_phone(raw) == expected


def test_an_international_number_is_left_alone() -> None:
    assert _normalise_phone("+14155552671") == "+14155552671"
