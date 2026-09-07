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
    "save_session",
    "set_setting",
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
    await _collection("history").create_index([("user_id", 1), ("timestamp", -1)])
    await _collection("history").create_index("timestamp")
    await _collection("settings").create_index("key", unique=True)
    await _collection("sessions").create_index("user_id", unique=True)
    await _collection("users").create_index("user_id", unique=True)
    await _collection("groups").create_index("group_id", unique=True)
    await _collection("pending").create_index("updated_at", expireAfterSeconds=1800)


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


async def save_session(session_string: str, user_id: int) -> None:
    await _collection("sessions").update_one(
        {"user_id": int(user_id)},
        {
            "$set": {
                "session": _encrypt(session_string),
                "updated_at": _now(),
            }
        },
        upsert=True,
    )


async def load_session(user_id: int | None = None) -> str | None:
    query = {"user_id": int(user_id)} if user_id is not None else {}
    doc = await _collection("sessions").find_one(query)
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
        user_id = doc.get("user_id", 0)
        session = _decrypt(doc.get("session", ""))
        if session:
            usable.append({"user_id": user_id, "session": session})
        else:
            broken.append(user_id)
    return usable, broken


async def delete_session(user_id: int | None = None) -> int:
    """Delete one account's session, or every session when ``user_id`` is None."""
    query = {"user_id": int(user_id)} if user_id is not None else {}
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


async def is_locked() -> bool:
    return bool(await get_setting("locked", False))


async def get_prompt() -> str | None:
    return await get_setting("prompt", None)


async def get_persona_key() -> str:
    return await get_setting("persona", settings.default_persona)


async def set_persona_key(key: str) -> None:
    await set_setting("persona", key)


async def set_dnd(time_range: str | None) -> None:
    await set_setting("dnd", time_range)


async def get_dnd() -> str | None:
    return await get_setting("dnd", None)


async def set_login_state(state: Any) -> None:
    await set_setting("login_state", state)


async def get_login_state() -> Any:
    return await get_setting("login_state", None)


# ──────────────────────────────────────────────────────────────────────────
#  Conversation history
# ──────────────────────────────────────────────────────────────────────────
async def get_conversation(
    user_id: int, limit: int | None = None, is_group: bool = False
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
                "user_id": int(user_id),
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


async def add_message(user_id: int, role: str, content: str, is_group: bool = False) -> None:
    await _collection("history").insert_one(
        {
            "user_id": int(user_id),
            "role": role,
            "content": content,
            "is_group": bool(is_group),
            "timestamp": _now(),
        }
    )


async def clear_history(user_id: int) -> int:
    result = await _collection("history").delete_many({"user_id": int(user_id)})
    return result.deleted_count


async def clear_all_history() -> int:
    result = await _collection("history").delete_many({})
    return result.deleted_count


async def auto_cleanup_history() -> dict[str, int]:
    """Drop history past its retention window. Runs hourly."""
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
async def add_pending(user_id: int, text: str) -> None:
    await _collection("pending").update_one(
        {"user_id": int(user_id)},
        {
            "$push": {"messages": {"text": text, "at": _now()}},
            "$set": {"updated_at": _now()},
        },
        upsert=True,
    )


async def get_pending(user_id: int, max_age_minutes: int = 10) -> list[str]:
    doc = await _collection("pending").find_one({"user_id": int(user_id)})
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


async def clear_pending(user_id: int) -> None:
    await _collection("pending").delete_one({"user_id": int(user_id)})


# ──────────────────────────────────────────────────────────────────────────
#  Access control
# ──────────────────────────────────────────────────────────────────────────
async def blacklist_user(user_id: int) -> None:
    await _collection("users").update_one(
        {"user_id": int(user_id)}, {"$set": {"blacklisted": True}}, upsert=True
    )


async def unblacklist_user(user_id: int) -> None:
    await _collection("users").update_one(
        {"user_id": int(user_id)}, {"$set": {"blacklisted": False}}, upsert=True
    )


async def is_blacklisted(user_id: int) -> bool:
    return bool(await _collection("users").find_one({"user_id": int(user_id), "blacklisted": True}))


async def list_blacklisted() -> list[int]:
    return [
        doc["user_id"]
        async for doc in _collection("users").find({"blacklisted": True}, {"user_id": 1})
    ]


async def allow_group(group_id: int) -> None:
    await _collection("groups").update_one(
        {"group_id": int(group_id)},
        {"$set": {"allowed": True, "added_at": _now()}},
        upsert=True,
    )


async def disallow_group(group_id: int) -> None:
    await _collection("groups").delete_one({"group_id": int(group_id)})


async def is_group_allowed(group_id: int) -> bool:
    return bool(await _collection("groups").find_one({"group_id": int(group_id), "allowed": True}))


async def list_allowed_groups() -> list[int]:
    return [
        doc["group_id"]
        async for doc in _collection("groups").find({"allowed": True}, {"group_id": 1})
    ]


# ──────────────────────────────────────────────────────────────────────────
#  Stats
# ──────────────────────────────────────────────────────────────────────────
async def increment_stat(key: str, amount: int = 1) -> None:
    await _collection("stats").update_one({"key": key}, {"$inc": {"value": amount}}, upsert=True)


async def increment_today() -> None:
    """Daily counter that actually rolls over at local midnight."""
    today = _today()
    await _collection("stats").update_one(
        {"key": "daily"},
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


async def get_stat(key: str) -> int:
    doc = await _collection("stats").find_one({"key": key})
    return int(doc.get("value", 0)) if doc else 0


async def get_today_count() -> int:
    doc = await _collection("stats").find_one({"key": "daily"})
    if not doc or doc.get("date") != _today():
        return 0
    return int(doc.get("value", 0))


# ──────────────────────────────────────────────────────────────────────────
#  Schedules
# ──────────────────────────────────────────────────────────────────────────
async def add_schedule(user_id: int, stype: str, time_str: str) -> None:
    await _collection("schedules").update_one(
        {"user_id": int(user_id), "type": stype},
        {"$set": {"time": time_str, "active": True}},
        upsert=True,
    )


async def remove_schedule(user_id: int, stype: str) -> None:
    await _collection("schedules").delete_one({"user_id": int(user_id), "type": stype})


async def get_active_schedules() -> list[dict[str, Any]]:
    return await _collection("schedules").find({"active": True}).to_list(None)
