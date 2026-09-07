"""MongoDB access layer.

Changes from the previous version, all of them bugs that showed up in use:

* The client is created **lazily**. It used to be built at import time from a
  possibly-empty ``MONGO_URI``, so a missing variable crashed on import with
  a stack trace instead of a readable message.
* Conversation history is filtered by ``is_group``. It previously was not,
  so a group thread and a DM with the same user were fed to the model as one
  conversation.
* ``today_replies`` resets at local midnight. It used to increment forever,
  so "today" was really "since the database was created".
* ``delete_session`` deletes the session that is actually in use. ``/logout``
  used to delete ``session_0`` regardless of which account was signed in.
* Indexes are created on startup.

Everything a person owns is scoped by ``owner_id`` - the Telegram id of
whoever is talking to the control bot. The bot is multi-user: two people can
sign in their own accounts, and neither may see the other's history,
settings, schedules or blocked list. Any function that touches per-person
data therefore takes ``owner`` as its first argument, and forgetting it is a
TypeError rather than a silent data leak.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from motor.motor_asyncio import AsyncIOMotorClient

from config import settings

log = logging.getLogger(__name__)

__all__ = [
    "add_message",
    "add_pending",
    "allow_group",
    "blacklist_user",
    "clear_all_history",
    "clear_history",
    "clear_pending",
    "connect",
    "disconnect",
    "get_conversation",
    "get_dnd",
    "get_pending",
    "get_prompt",
    "get_setting",
    "get_stat",
    "increment_stat",
    "is_blacklisted",
    "is_group_allowed",
    "is_locked",
    "list_allowed_groups",
    "load_all_sessions",
    "load_session",
    "migrate_to_multi_user",
    "register_user",
    "save_session",
    "set_setting",
    "set_user_setting",
]

_client: AsyncIOMotorClient | None = None
_db: Any = None
_cipher: Fernet | None = None


# ──────────────────────────────────────────────────────────────────────────
#  Lifecycle
# ──────────────────────────────────────────────────────────────────────────
async def connect() -> None:
    """Open the connection and ensure indexes. Safe to call twice."""
    global _client, _db, _cipher

    if _cipher is None and settings.encryption_key:
        _cipher = Fernet(settings.encryption_key.encode())

    if _client is not None:
        return

    _client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=8000)
    _db = _client[settings.mongo_db]
    await _client.admin.command("ping")
    await _ensure_indexes()
    log.info("MongoDB connected (database %r)", settings.mongo_db)


async def disconnect() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client, _db = None, None


def _collection(name: str):
    if _db is None:
        raise RuntimeError("database.connect() was never awaited")
    return _db[name]


async def _ensure_indexes() -> None:
    await _collection("history").create_index([("owner_id", 1), ("peer_id", 1), ("timestamp", -1)])
    await _collection("history").create_index("timestamp")
    await _collection("settings").create_index("key", unique=True)
    await _collection("user_settings").create_index([("owner_id", 1), ("key", 1)], unique=True)
    await _collection("sessions").create_index("owner_id", unique=True)
    await _collection("accounts").create_index("owner_id", unique=True)
    await _collection("users").create_index([("owner_id", 1), ("user_id", 1)], unique=True)
    await _collection("groups").create_index([("owner_id", 1), ("group_id", 1)], unique=True)
    await _collection("schedules").create_index([("owner_id", 1), ("target_id", 1), ("type", 1)])
    await _collection("stats").create_index([("owner_id", 1), ("key", 1)])
    await _collection("pending").create_index("updated_at", expireAfterSeconds=1800)


async def migrate_to_multi_user(default_owner: int) -> dict[str, int]:
    """Give pre-multi-user rows an owner, so nobody loses their data.

    Everything used to be global: one session, one persona, one history. The
    first configured owner inherits all of it. Safe to run on every boot -
    documents that already have an ``owner_id`` are left alone.
    """
    if not default_owner:
        return {}
    moved: dict[str, int] = {}
    plans = (
        ("sessions", {"user_id": "account_id"}),
        ("history", {"user_id": "peer_id"}),
        ("pending", {"user_id": "peer_id"}),
        ("users", {}),
        ("groups", {}),
        ("schedules", {"user_id": "target_id"}),
        ("stats", {}),
    )
    for name, renames in plans:
        collection = _collection(name)
        update: dict[str, Any] = {"$set": {"owner_id": int(default_owner)}}
        if renames:
            update["$rename"] = renames
        result = await collection.update_many({"owner_id": {"$exists": False}}, update)
        if result.modified_count:
            moved[name] = result.modified_count

    # The old global settings become the first owner's settings.
    legacy_keys = ("persona", "prompt", "dnd", "locked", "preferred_model", "login_state")
    migrated_settings = 0
    async for doc in _collection("settings").find({"key": {"$in": list(legacy_keys)}}):
        await _collection("user_settings").update_one(
            {"owner_id": int(default_owner), "key": doc["key"]},
            {"$setOnInsert": {"value": doc.get("value"), "updated_at": _now()}},
            upsert=True,
        )
        await _collection("settings").delete_one({"_id": doc["_id"]})
        migrated_settings += 1
    if migrated_settings:
        moved["settings"] = migrated_settings

    if moved:
        log.info("migrated legacy data to owner %s: %s", default_owner, moved)
    return moved


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _today() -> str:
    return dt.datetime.now(settings.tz).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────────────────
#  Sessions (encrypted at rest)
# ──────────────────────────────────────────────────────────────────────────
def _encrypt(text: str) -> str:
    return _cipher.encrypt(text.encode()).decode() if _cipher else text


def _decrypt(text: str) -> str:
    if not _cipher:
        return text
    try:
        return _cipher.decrypt(text.encode()).decode()
    except InvalidToken:
        # Written before ENCRYPTION_KEY was set, or the key was rotated.
        log.warning("A stored session could not be decrypted and was skipped")
        return ""


async def save_session(owner: int, session_string: str, account_id: int) -> None:
    await _collection("sessions").update_one(
        {"owner_id": int(owner)},
        {
            "$set": {
                "account_id": int(account_id),
                "session": _encrypt(session_string),
                "updated_at": _now(),
            }
        },
        upsert=True,
    )


async def load_session(owner: int) -> str | None:
    doc = await _collection("sessions").find_one({"owner_id": int(owner)})
    if not doc:
        return None
    return _decrypt(doc.get("session", "")) or None


async def load_all_sessions() -> list[dict[str, Any]]:
    usable, _ = await load_session_records()
    return usable


async def load_session_records() -> tuple[list[dict[str, Any]], list[int]]:
    """Return ``(usable_sessions, undecryptable_user_ids)``.

    A session that cannot be decrypted is almost always a rotated or
    corrupted ENCRYPTION_KEY. It will never work again, so the caller
    deletes it and asks the owner to sign in once more - otherwise the row
    sits there forever and every restart silently starts zero accounts.
    """
    usable: list[dict[str, Any]] = []
    broken: list[int] = []
    async for doc in _collection("sessions").find({}):
        owner_id = doc.get("owner_id", 0)
        session = _decrypt(doc.get("session", ""))
        if session:
            usable.append(
                {
                    "owner_id": owner_id,
                    "account_id": doc.get("account_id", 0),
                    "session": session,
                }
            )
        else:
            broken.append(owner_id)
    return usable, broken


async def delete_session(owner: int | None = None) -> int:
    """Delete one owner's session, or every session when ``owner`` is None."""
    query = {"owner_id": int(owner)} if owner is not None else {}
    result = await _collection("sessions").delete_many(query)
    return result.deleted_count


# ──────────────────────────────────────────────────────────────────────────
#  Settings
# ──────────────────────────────────────────────────────────────────────────
async def get_setting(key: str, default: Any = None) -> Any:
    doc = await _collection("settings").find_one({"key": key})
    return doc["value"] if doc else default


async def set_setting(key: str, value: Any) -> None:
    await _collection("settings").update_one(
        {"key": key}, {"$set": {"value": value, "updated_at": _now()}}, upsert=True
    )


# -- per-owner settings ----------------------------------------------------
async def get_user_setting(owner: int, key: str, default: Any = None) -> Any:
    doc = await _collection("user_settings").find_one({"owner_id": int(owner), "key": key})
    return doc["value"] if doc else default


async def set_user_setting(owner: int, key: str, value: Any) -> None:
    await _collection("user_settings").update_one(
        {"owner_id": int(owner), "key": key},
        {"$set": {"value": value, "updated_at": _now()}},
        upsert=True,
    )


async def is_locked(owner: int) -> bool:
    return bool(await get_user_setting(owner, "locked", False))


async def get_prompt(owner: int) -> str | None:
    return await get_user_setting(owner, "prompt", None)


async def get_persona_key(owner: int) -> str:
    return await get_user_setting(owner, "persona", settings.default_persona)


async def set_persona_key(owner: int, key: str) -> None:
    await set_user_setting(owner, "persona", key)


async def set_dnd(owner: int, time_range: str | None) -> None:
    await set_user_setting(owner, "dnd", time_range)


async def get_dnd(owner: int) -> str | None:
    return await get_user_setting(owner, "dnd", None)


async def set_login_state(owner: int, state: Any) -> None:
    await set_user_setting(owner, "login_state", state)


async def get_login_state(owner: int) -> Any:
    return await get_user_setting(owner, "login_state", None)


# -- who is using the bot --------------------------------------------------
async def register_user(owner: int, **profile: Any) -> None:
    """Record that somebody has started using the bot."""
    await _collection("accounts").update_one(
        {"owner_id": int(owner)},
        {"$set": {**profile, "last_seen": _now()}, "$setOnInsert": {"first_seen": _now()}},
        upsert=True,
    )


async def list_users() -> list[dict[str, Any]]:
    return await _collection("accounts").find({}).sort("first_seen", 1).to_list(None)


async def count_users() -> int:
    return await _collection("accounts").count_documents({})


async def is_banned(owner: int) -> bool:
    doc = await _collection("accounts").find_one({"owner_id": int(owner)})
    return bool(doc and doc.get("banned"))


async def set_banned(owner: int, banned: bool) -> None:
    await _collection("accounts").update_one(
        {"owner_id": int(owner)}, {"$set": {"banned": bool(banned)}}, upsert=True
    )


async def forget_user(owner: int) -> dict[str, int]:
    """Delete everything belonging to one person (used by /deleteme)."""
    removed: dict[str, int] = {}
    for name in (
        "sessions",
        "history",
        "pending",
        "users",
        "groups",
        "schedules",
        "user_settings",
        "stats",
        "accounts",
    ):
        result = await _collection(name).delete_many({"owner_id": int(owner)})
        if result.deleted_count:
            removed[name] = result.deleted_count
    return removed


# ──────────────────────────────────────────────────────────────────────────
#  Conversation history
# ──────────────────────────────────────────────────────────────────────────
async def get_conversation(
    owner: int, peer_id: int, limit: int | None = None, is_group: bool = False
) -> list[dict[str, str]]:
    """Recent turns for one user, newest last.

    Group and DM history are kept apart: mixing them made the model answer a
    private question with group context.
    """
    limit = limit or settings.history_limit
    if is_group:
        cutoff = _now() - dt.timedelta(hours=settings.group_retention_hours)
    else:
        cutoff = _now() - dt.timedelta(days=settings.dm_retention_days)

    cursor = (
        _collection("history")
        .find(
            {
                "owner_id": int(owner),
                "peer_id": int(peer_id),
                "is_group": bool(is_group),
                "timestamp": {"$gte": cutoff},
            },
            {"role": 1, "content": 1, "_id": 0},
        )
        .sort("timestamp", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(limit)
    return list(reversed(docs))


async def add_message(
    owner: int, peer_id: int, role: str, content: str, is_group: bool = False
) -> None:
    await _collection("history").insert_one(
        {
            "owner_id": int(owner),
            "peer_id": int(peer_id),
            "role": role,
            "content": content,
            "is_group": bool(is_group),
            "timestamp": _now(),
        }
    )


async def clear_history(owner: int, peer_id: int) -> int:
    result = await _collection("history").delete_many(
        {"owner_id": int(owner), "peer_id": int(peer_id)}
    )
    return result.deleted_count


async def clear_all_history(owner: int) -> int:
    """Clear one owner's history - never everybody's."""
    result = await _collection("history").delete_many({"owner_id": int(owner)})
    return result.deleted_count


async def auto_cleanup_history() -> dict[str, int]:
    """Drop history past its retention window, for every owner. Runs hourly."""
    now = _now()
    groups = await _collection("history").delete_many(
        {
            "is_group": True,
            "timestamp": {"$lt": now - dt.timedelta(hours=settings.group_retention_hours)},
        }
    )
    dms = await _collection("history").delete_many(
        {
            "is_group": {"$ne": True},
            "timestamp": {"$lt": now - dt.timedelta(days=settings.dm_retention_days)},
        }
    )
    return {"groups": groups.deleted_count, "dms": dms.deleted_count}


# ──────────────────────────────────────────────────────────────────────────
#  Pending fragments (user still typing)
# ──────────────────────────────────────────────────────────────────────────
async def add_pending(owner: int, peer_id: int, text: str) -> None:
    await _collection("pending").update_one(
        {"owner_id": int(owner), "peer_id": int(peer_id)},
        {
            "$push": {"messages": {"text": text, "at": _now()}},
            "$set": {"updated_at": _now()},
        },
        upsert=True,
    )


async def get_pending(owner: int, peer_id: int, max_age_minutes: int = 10) -> list[str]:
    doc = await _collection("pending").find_one({"owner_id": int(owner), "peer_id": int(peer_id)})
    if not doc:
        return []
    cutoff = _now() - dt.timedelta(minutes=max_age_minutes)
    fresh = []
    for message in doc.get("messages", []):
        at = message.get("at")
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.UTC)
        if at >= cutoff:
            fresh.append(message["text"])
    return fresh


async def clear_pending(owner: int, peer_id: int) -> None:
    await _collection("pending").delete_one({"owner_id": int(owner), "peer_id": int(peer_id)})


# ──────────────────────────────────────────────────────────────────────────
#  Access control
# ──────────────────────────────────────────────────────────────────────────
async def blacklist_user(owner: int, user_id: int) -> None:
    await _collection("users").update_one(
        {"owner_id": int(owner), "user_id": int(user_id)},
        {"$set": {"blacklisted": True}},
        upsert=True,
    )


async def unblacklist_user(owner: int, user_id: int) -> None:
    await _collection("users").update_one(
        {"owner_id": int(owner), "user_id": int(user_id)},
        {"$set": {"blacklisted": False}},
        upsert=True,
    )


async def is_blacklisted(owner: int, user_id: int) -> bool:
    return bool(
        await _collection("users").find_one(
            {"owner_id": int(owner), "user_id": int(user_id), "blacklisted": True}
        )
    )


async def list_blacklisted(owner: int) -> list[int]:
    return [
        doc["user_id"]
        async for doc in _collection("users").find(
            {"owner_id": int(owner), "blacklisted": True}, {"user_id": 1}
        )
    ]


async def allow_group(owner: int, group_id: int) -> None:
    await _collection("groups").update_one(
        {"owner_id": int(owner), "group_id": int(group_id)},
        {"$set": {"allowed": True, "added_at": _now()}},
        upsert=True,
    )


async def disallow_group(owner: int, group_id: int) -> None:
    await _collection("groups").delete_one({"owner_id": int(owner), "group_id": int(group_id)})


async def is_group_allowed(owner: int, group_id: int) -> bool:
    return bool(
        await _collection("groups").find_one(
            {"owner_id": int(owner), "group_id": int(group_id), "allowed": True}
        )
    )


async def list_allowed_groups(owner: int) -> list[int]:
    return [
        doc["group_id"]
        async for doc in _collection("groups").find(
            {"owner_id": int(owner), "allowed": True}, {"group_id": 1}
        )
    ]


# ──────────────────────────────────────────────────────────────────────────
#  Stats
# ──────────────────────────────────────────────────────────────────────────
async def increment_stat(owner: int, key: str, amount: int = 1) -> None:
    await _collection("stats").update_one(
        {"owner_id": int(owner), "key": key}, {"$inc": {"value": amount}}, upsert=True
    )


async def increment_today(owner: int) -> None:
    """Daily counter that actually rolls over at local midnight."""
    today = _today()
    await _collection("stats").update_one(
        {"owner_id": int(owner), "key": "daily"},
        [
            {
                "$set": {
                    "date": today,
                    "value": {
                        "$cond": [
                            {"$eq": ["$date", today]},
                            {"$add": [{"$ifNull": ["$value", 0]}, 1]},
                            1,
                        ]
                    },
                }
            }
        ],
        upsert=True,
    )


async def get_stat(owner: int, key: str) -> int:
    doc = await _collection("stats").find_one({"owner_id": int(owner), "key": key})
    return int(doc.get("value", 0)) if doc else 0


async def get_today_count(owner: int) -> int:
    doc = await _collection("stats").find_one({"owner_id": int(owner), "key": "daily"})
    if not doc or doc.get("date") != _today():
        return 0
    return int(doc.get("value", 0))


async def total_replies_all_users() -> int:
    """Across everybody - for the admin dashboard only."""
    pipeline = [
        {"$match": {"key": "total_replies"}},
        {"$group": {"_id": None, "total": {"$sum": "$value"}}},
    ]
    rows = await _collection("stats").aggregate(pipeline).to_list(1)
    return int(rows[0]["total"]) if rows else 0


# ──────────────────────────────────────────────────────────────────────────
#  Schedules
# ──────────────────────────────────────────────────────────────────────────
async def add_schedule(owner: int, target_id: int, stype: str, time_str: str) -> None:
    await _collection("schedules").update_one(
        {"owner_id": int(owner), "target_id": int(target_id), "type": stype},
        {"$set": {"time": time_str, "active": True}},
        upsert=True,
    )


async def remove_schedule(owner: int, target_id: int, stype: str) -> None:
    await _collection("schedules").delete_one(
        {"owner_id": int(owner), "target_id": int(target_id), "type": stype}
    )


async def get_active_schedules(owner: int) -> list[dict[str, Any]]:
    """Only this owner's schedules - never everybody's."""
    return (
        await _collection("schedules").find({"owner_id": int(owner), "active": True}).to_list(None)
    )
