"""The bot is public: everybody gets their own space.

These tests exist for one reason - a missing ``owner_id`` in a query is not a
crash, it is a data leak. Somebody else's conversation history, persona or
schedule would quietly appear in your account.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from core.safety import aggregate_snapshot, forget_limiter, limiter_for, reset_all
from database import mongo


@pytest.fixture(autouse=True)
def _clean():
    reset_all()
    yield
    reset_all()


# ── every per-user function is owner-scoped ─────────────────────────────────

#: Functions that read or write something belonging to one person.
_PER_USER = [
    "add_message",
    "add_pending",
    "add_schedule",
    "allow_group",
    "blacklist_user",
    "clear_all_history",
    "clear_history",
    "clear_pending",
    "disallow_group",
    "get_active_schedules",
    "get_conversation",
    "get_dnd",
    "get_login_state",
    "get_pending",
    "get_persona_key",
    "get_prompt",
    "get_stat",
    "get_today_count",
    "get_user_setting",
    "increment_stat",
    "increment_today",
    "is_blacklisted",
    "is_group_allowed",
    "is_locked",
    "list_allowed_groups",
    "list_blacklisted",
    "load_session",
    "remove_schedule",
    "save_session",
    "set_dnd",
    "set_login_state",
    "set_persona_key",
    "set_user_setting",
    "unblacklist_user",
]


@pytest.mark.parametrize("name", _PER_USER)
def test_every_per_user_function_takes_owner_first(name: str) -> None:
    """Forgetting the owner must be a TypeError, not a silent leak."""
    parameters = list(inspect.signature(getattr(mongo, name)).parameters)
    assert parameters[0] == "owner", f"mongo.{name} must take owner first, got {parameters}"


# ── queries actually filter on owner_id ─────────────────────────────────────


class _FakeCursor:
    def __init__(self, docs=()):
        self._docs = list(docs)

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, *a, **k):
        return self._docs

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc

        return gen()


class _FakeCollection:
    """Records every filter it is queried with."""

    def __init__(self, docs=()):
        self.filters: list[dict] = []
        self._docs = list(docs)

    def find(self, query=None, *a, **k):
        self.filters.append(query or {})
        return _FakeCursor(self._docs)

    async def find_one(self, query=None, *a, **k):
        self.filters.append(query or {})
        return self._docs[0] if self._docs else None

    async def update_one(self, query, *a, **k):
        self.filters.append(query)

    async def insert_one(self, doc):
        self.filters.append(doc)

    async def delete_one(self, query):
        self.filters.append(query)

    async def delete_many(self, query):
        self.filters.append(query)

        class _R:
            deleted_count = 0

        return _R()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "kwargs"),
    [
        ("get_conversation", {"owner": 1, "peer_id": 2}),
        ("add_message", {"owner": 1, "peer_id": 2, "role": "user", "content": "hi"}),
        ("clear_history", {"owner": 1, "peer_id": 2}),
        ("clear_all_history", {"owner": 1}),
        ("get_pending", {"owner": 1, "peer_id": 2}),
        ("add_pending", {"owner": 1, "peer_id": 2, "text": "hi"}),
        ("clear_pending", {"owner": 1, "peer_id": 2}),
        ("is_blacklisted", {"owner": 1, "user_id": 2}),
        ("blacklist_user", {"owner": 1, "user_id": 2}),
        ("list_blacklisted", {"owner": 1}),
        ("is_group_allowed", {"owner": 1, "group_id": -2}),
        ("allow_group", {"owner": 1, "group_id": -2}),
        ("list_allowed_groups", {"owner": 1}),
        ("get_active_schedules", {"owner": 1}),
        ("add_schedule", {"owner": 1, "target_id": 2, "stype": "morning", "time_str": "08:00"}),
        ("remove_schedule", {"owner": 1, "target_id": 2, "stype": "morning"}),
        ("get_user_setting", {"owner": 1, "key": "persona"}),
        ("set_user_setting", {"owner": 1, "key": "persona", "value": "casual"}),
        ("load_session", {"owner": 1}),
        ("get_stat", {"owner": 1, "key": "total_replies"}),
        ("increment_stat", {"owner": 1, "key": "total_replies"}),
    ],
)
async def test_query_is_scoped_to_the_owner(call: str, kwargs: dict) -> None:
    collection = _FakeCollection()
    with patch.object(mongo, "_collection", lambda _name: collection):
        await getattr(mongo, call)(**kwargs)
    assert collection.filters, f"mongo.{call} issued no query"
    assert all("owner_id" in f for f in collection.filters), (
        f"mongo.{call} queried without owner_id: {collection.filters}"
    )


@pytest.mark.asyncio
async def test_clear_all_history_only_clears_one_owner() -> None:
    """It used to delete every message in the database."""
    collection = _FakeCollection()
    with patch.object(mongo, "_collection", lambda _name: collection):
        await mongo.clear_all_history(7)
    assert collection.filters == [{"owner_id": 7}]


@pytest.mark.asyncio
async def test_deleting_an_account_removes_only_that_owner() -> None:
    collection = _FakeCollection()
    with patch.object(mongo, "_collection", lambda _name: collection):
        await mongo.forget_user(7)
    assert collection.filters
    assert all(f == {"owner_id": 7} for f in collection.filters)


# ── rate limiting is per account ────────────────────────────────────────────


def test_each_owner_gets_their_own_limiter() -> None:
    """A shared limiter would let one busy user throttle everybody else."""
    assert limiter_for(1) is not limiter_for(2)
    assert limiter_for(1) is limiter_for(1)


def test_one_owner_hitting_a_cap_does_not_silence_another() -> None:
    busy = limiter_for(1)
    for i in range(200):
        busy.record(i, now=0.0, text=f"m{i}")
    assert not busy.check(999, now=1.0).allowed
    assert limiter_for(2).check(999, now=1.0).allowed


def test_the_same_contact_id_is_tracked_separately_per_owner() -> None:
    """Two users may both be talking to contact 555."""
    limiter_for(1).record(555, now=0.0, text="See you at 8")
    assert not limiter_for(1).allow_text(555, "See you at 8", now=1.0).allowed
    assert limiter_for(2).allow_text(555, "See you at 8", now=1.0).allowed


def test_aggregate_snapshot_sums_every_account() -> None:
    limiter_for(1).record(1, now=0.0, text="a")
    limiter_for(2).record(1, now=0.0, text="b")
    snapshot = aggregate_snapshot()
    assert snapshot["accounts"] == 2
    assert snapshot["replies_last_hour"] == 2


def test_forgetting_an_owner_drops_their_counters() -> None:
    limiter_for(1).record(1, now=0.0, text="a")
    forget_limiter(1)
    assert aggregate_snapshot()["accounts"] == 0


# ── legacy data is migrated, not orphaned ───────────────────────────────────


@pytest.mark.asyncio
async def test_migration_claims_rows_that_have_no_owner() -> None:
    """The first admin inherits everything from the single-user era."""
    seen: list[tuple[dict, dict]] = []

    class _Migrating(_FakeCollection):
        async def update_many(self, query, update):
            seen.append((query, update))

            class _R:
                modified_count = 1

            return _R()

    collection = _Migrating()
    with patch.object(mongo, "_collection", lambda _name: collection):
        moved = await mongo.migrate_to_multi_user(42)

    assert seen, "nothing was migrated"
    for query, update in seen:
        assert query == {"owner_id": {"$exists": False}}
        assert update["$set"]["owner_id"] == 42
    assert moved


@pytest.mark.asyncio
async def test_migration_is_a_no_op_without_a_configured_admin() -> None:
    assert await mongo.migrate_to_multi_user(0) == {}


@pytest.mark.asyncio
async def test_migration_does_not_touch_rows_that_already_have_an_owner() -> None:
    """It must be safe to run on every boot."""
    calls: list[dict] = []

    class _Migrating(_FakeCollection):
        async def update_many(self, query, update):
            calls.append(query)

            class _R:
                modified_count = 0

            return _R()

    collection = _Migrating()
    with patch.object(mongo, "_collection", lambda _name: collection):
        await mongo.migrate_to_multi_user(42)
    assert all(q == {"owner_id": {"$exists": False}} for q in calls)


# ── access control ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_banned_user_is_refused() -> None:
    collection = _FakeCollection([{"owner_id": 5, "banned": True}])
    with patch.object(mongo, "_collection", lambda _name: collection):
        assert await mongo.is_banned(5) is True


@pytest.mark.asyncio
async def test_an_unknown_user_is_not_banned() -> None:
    collection = _FakeCollection([])
    with patch.object(mongo, "_collection", lambda _name: collection):
        assert await mongo.is_banned(5) is False


def test_admin_commands_are_not_open_to_everyone() -> None:
    """/users, /broadcast and /ban must stay behind OWNER_IDS."""
    from pathlib import Path

    source = Path("handlers/control.py").read_text()
    for command in ("/users", "/gstats", "/broadcast", "/ban", "/unban"):
        index = source.index(f'pattern=r"^{command}')
        following = source[index : index + 400]
        assert "@_admin_only" in following, f"{command} is not admin-gated"


def test_user_commands_are_open_to_everyone() -> None:
    from pathlib import Path

    source = Path("handlers/control.py").read_text()
    for command in ("/login", "/persona", "/status", "/deleteme"):
        index = source.index(f'pattern=r"^{command}')
        following = source[index : index + 400]
        assert "@_registered" in following, f"{command} should be open to all users"


def test_userbot_scopes_every_read_to_its_owner() -> None:
    """The handler must never read another owner's settings."""
    from handlers import userbot

    source = inspect.getsource(userbot._handle)
    for call in ("is_locked", "is_blacklisted", "get_dnd", "generate_reply"):
        assert f"{call}(owner" in source or f"{call}(\n            owner" in source, (
            f"{call} in the pipeline is not owner-scoped"
        )


def test_logging_out_only_removes_your_own_session() -> None:
    """It used to delete every stored session in the database."""
    from pathlib import Path

    source = Path("handlers/control.py").read_text()
    start = source.index("async def cmd_logout")
    logout = source[start : start + 900]
    assert "delete_session(owner)" in logout
    assert "delete_session()" not in logout


def test_new_users_are_registered_by_the_decorator() -> None:
    """Anyone who talks to the bot is recorded, so /users and /broadcast work."""
    from pathlib import Path

    source = Path("handlers/control.py").read_text()
    start = source.index("def _registered")
    assert "register_user" in source[start : start + 700]
