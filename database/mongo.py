import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from cryptography.fernet import Fernet
from config import MONGO_URI, ENCRYPTION_KEY

# ── Fernet cipher ──────────────────────────────────────────────
_cipher = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None

def _encrypt(text: str) -> str:
    if not _cipher: return text
    return _cipher.encrypt(text.encode()).decode()

def _decrypt(text: str) -> str:
    if not _cipher: return text
    return _cipher.decrypt(text.encode()).decode()

# ── Connect ────────────────────────────────────────────────────
_client = AsyncIOMotorClient(MONGO_URI)
db = _client.userbot

settings_col   = db.settings
history_col    = db.history
users_col      = db.users
schedules_col  = db.schedules
stats_col      = db.stats
session_col    = db.session
groups_col     = db.allowed_groups     # ← new: allowed group whitelist
pending_col    = db.pending_messages   # ← new: buffered incomplete messages

# ── Session ────────────────────────────────────────────────────
async def save_session(session_string: str, user_id: int = 0):
    encrypted = _encrypt(session_string)
    await session_col.update_one(
        {"key": f"session_{user_id}"},
        {"$set": {"value": encrypted, "user_id": user_id, "updated_at": datetime.datetime.utcnow()}},
        upsert=True,
    )
    await session_col.update_one(
        {"key": "session"},
        {"$set": {"value": encrypted, "user_id": user_id, "updated_at": datetime.datetime.utcnow()}},
        upsert=True,
    )

async def load_session(user_id: int = 0):
    doc = await session_col.find_one({"key": f"session_{user_id}"})
    if not doc:
        doc = await session_col.find_one({"key": "session"})
    if not doc:
        return None
    return _decrypt(doc["value"])

async def load_all_sessions() -> list:
    docs = await session_col.find({"key": {"$regex": "^session_"}}).to_list(None)
    result = []
    for doc in docs:
        try:
            result.append({"user_id": doc.get("user_id", 0), "session": _decrypt(doc["value"])})
        except Exception:
            pass
    return result

async def delete_session(user_id: int = 0):
    await session_col.delete_one({"key": f"session_{user_id}"})
    count = await session_col.count_documents({"key": {"$regex": "^session_"}})
    if count == 0:
        await session_col.delete_one({"key": "session"})

async def delete_all_sessions():
    await session_col.delete_many({})

# ── Settings ───────────────────────────────────────────────────
async def get_setting(key: str, default=None):
    doc = await settings_col.find_one({"key": key})
    return doc["value"] if doc else default

async def set_setting(key: str, value):
    await settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

async def is_locked() -> bool:
    return await get_setting("locked", False)

async def get_prompt():
    return await get_setting("prompt", None)

# ── Conversation History (14-day rolling, group = 24hr) ────────
async def get_conversation(user_id: int, limit: int = 20, is_group: bool = False):
    """
    Returns last `limit` messages for this user.
    Only fetches messages within the retention window.
    """
    if is_group:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    else:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=14)

    docs = (
        await history_col.find({"user_id": user_id, "timestamp": {"$gte": cutoff}})
        .sort("timestamp", -1).limit(limit).to_list(limit)
    )
    return list(reversed(docs))

async def add_message(user_id: int, role: str, content: str, is_group: bool = False):
    await history_col.insert_one({
        "user_id":   user_id,
        "role":      role,
        "content":   content,
        "is_group":  is_group,
        "timestamp": datetime.datetime.utcnow(),
    })

async def clear_history(user_id: int):
    await history_col.delete_many({"user_id": user_id})

async def clear_all_history():
    """Clear ALL conversation history for every user."""
    await history_col.delete_many({})

async def auto_cleanup_history():
    """
    Rolling cleanup:
    - DM history older than 14 days → deleted day by day as time passes
    - Group history older than 24 hours → deleted
    - Users with NO activity in 14 days → full history wiped
    """
    now = datetime.datetime.utcnow()

    # Delete group messages older than 24 hours
    await history_col.delete_many({
        "is_group": True,
        "timestamp": {"$lt": now - datetime.timedelta(hours=24)},
    })

    # Delete DM messages older than 14 days
    await history_col.delete_many({
        "is_group": {"$ne": True},
        "timestamp": {"$lt": now - datetime.timedelta(days=14)},
    })

    # Wipe full history of users inactive for 14+ days
    cutoff_14 = now - datetime.timedelta(days=14)
    active_users = await history_col.distinct("user_id", {"timestamp": {"$gte": cutoff_14}})
    all_users    = await history_col.distinct("user_id")
    inactive     = [u for u in all_users if u not in active_users]
    if inactive:
        await history_col.delete_many({"user_id": {"$in": inactive}})

# ── Pending / Incomplete Message Buffer ────────────────────────
# Stores short/incomplete messages until enough context builds up
async def add_pending(user_id: int, text: str):
    await pending_col.update_one(
        {"user_id": user_id},
        {"$push": {"messages": {"text": text, "at": datetime.datetime.utcnow()}},
         "$set":  {"updated_at": datetime.datetime.utcnow()}},
        upsert=True,
    )

async def get_pending(user_id: int) -> list:
    doc = await pending_col.find_one({"user_id": user_id})
    if not doc:
        return []
    # Only keep pending messages from last 10 minutes
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
    return [m["text"] for m in doc.get("messages", []) if m["at"] >= cutoff]

async def clear_pending(user_id: int):
    await pending_col.delete_one({"user_id": user_id})

# ── Blacklist / Whitelist ──────────────────────────────────────
async def blacklist_user(user_id: int):
    await users_col.update_one({"user_id": user_id}, {"$set": {"blacklisted": True}}, upsert=True)

async def unblacklist_user(user_id: int):
    await users_col.update_one({"user_id": user_id}, {"$set": {"blacklisted": False}})

async def is_blacklisted(user_id: int) -> bool:
    return bool(await users_col.find_one({"user_id": user_id, "blacklisted": True}))

async def whitelist_user(user_id: int):
    await users_col.update_one({"user_id": user_id}, {"$set": {"whitelisted": True}}, upsert=True)

async def unwhitelist_user(user_id: int):
    await users_col.update_one({"user_id": user_id}, {"$set": {"whitelisted": False}})

async def is_whitelisted(user_id: int) -> bool:
    return bool(await users_col.find_one({"user_id": user_id, "whitelisted": True}))

# ── Allowed Groups ─────────────────────────────────────────────
async def allow_group(group_id: int):
    await groups_col.update_one(
        {"group_id": group_id},
        {"$set": {"allowed": True, "added_at": datetime.datetime.utcnow()}},
        upsert=True,
    )

async def disallow_group(group_id: int):
    await groups_col.delete_one({"group_id": group_id})

async def is_group_allowed(group_id: int) -> bool:
    return bool(await groups_col.find_one({"group_id": group_id, "allowed": True}))

async def list_allowed_groups() -> list:
    docs = await groups_col.find({"allowed": True}).to_list(None)
    return [d["group_id"] for d in docs]

# ── Stats ──────────────────────────────────────────────────────
async def increment_stat(key: str):
    await stats_col.update_one({"key": key}, {"$inc": {"value": 1}}, upsert=True)

async def get_stat(key: str) -> int:
    doc = await stats_col.find_one({"key": key})
    return doc["value"] if doc else 0

# ── Schedules ──────────────────────────────────────────────────
async def add_schedule(user_id: int, stype: str, time_str: str):
    await schedules_col.update_one(
        {"user_id": user_id, "type": stype},
        {"$set": {"time": time_str, "active": True}},
        upsert=True,
    )

async def remove_schedule(user_id: int, stype: str):
    await schedules_col.update_one({"user_id": user_id, "type": stype}, {"$set": {"active": False}})

async def get_active_schedules():
    return await schedules_col.find({"active": True}).to_list(None)

# ── DND ────────────────────────────────────────────────────────
async def set_dnd(time_range):
    await set_setting("dnd", time_range)

async def get_dnd():
    return await get_setting("dnd", None)

# ── Login State ────────────────────────────────────────────────
async def set_login_state(state):
    await settings_col.update_one({"key": "login_state"}, {"$set": {"value": state}}, upsert=True)

async def get_login_state():
    doc = await settings_col.find_one({"key": "login_state"})
    return doc.get("value") if doc else None
