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

settings_col  = db.settings
history_col   = db.history
users_col     = db.users
schedules_col = db.schedules
stats_col     = db.stats
session_col   = db.session

# ── Session ────────────────────────────────────────────────────
async def save_session(session_string: str):
    encrypted = _encrypt(session_string)
    await session_col.update_one({"key": "session"}, {"$set": {"value": encrypted}}, upsert=True)

async def load_session():
    doc = await session_col.find_one({"key": "session"})
    if not doc: return None
    return _decrypt(doc["value"])

async def delete_session():
    await session_col.delete_one({"key": "session"})

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

# ── Conversation History ───────────────────────────────────────
async def get_conversation(user_id: int, limit: int = 10):
    docs = (
        await history_col.find({"user_id": user_id})
        .sort("timestamp", -1).limit(limit).to_list(limit)
    )
    return list(reversed(docs))

async def add_message(user_id: int, role: str, content: str):
    await history_col.insert_one({
        "user_id": user_id, "role": role, "content": content,
        "timestamp": datetime.datetime.utcnow(),
    })

async def clear_history(user_id: int):
    await history_col.delete_many({"user_id": user_id})

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
