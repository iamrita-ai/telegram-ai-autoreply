import os
import random
import datetime

# ============================================================
#   OWNER CONFIGURATION
# ============================================================
OWNER_IDS = [1598576202, 6518065496]

# ============================================================
#   TELEGRAM CREDENTIALS
# ============================================================
API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
PHONE_NUMBER = os.environ.get("PHONE_NUMBER", "")
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")

# ============================================================
#   SESSION ENCRYPTION
# ============================================================
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# ============================================================
#   MONGODB
# ============================================================
MONGO_URI = os.environ.get("MONGO_URI", "")

# ============================================================
#   AI API KEYS
# ============================================================
SAMBANOVA_API_KEY = os.environ.get("SAMBANOVA_API_KEY", "")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
NVIDIA_API_KEY    = os.environ.get("NVIDIA_API_KEY", "")

# ── Model strings ─────────────────────────────────────────────
GROQ_MODEL_70B        = "llama-3.3-70b-versatile"
GROQ_MODEL_8B         = "llama-3.1-8b-instant"
SAMBANOVA_MODEL       = "Meta-Llama-3.3-70B-Instruct"
NVIDIA_MODEL_70B      = "meta/llama-3.3-70b-instruct"
NVIDIA_MODEL_MAVERICK = "meta/llama-4-maverick-17b-128e-instruct"
GROQ_MODEL            = GROQ_MODEL_70B

def get_available_models() -> list:
    models = []
    if GROQ_API_KEY:
        models.append({"id": "groq_70b",        "label": "⚡ Groq — Llama 3.3 70B",           "provider": "groq",      "model": GROQ_MODEL_70B})
        models.append({"id": "groq_8b",         "label": "⚡ Groq — Llama 3.1 8B 🚀",         "provider": "groq",      "model": GROQ_MODEL_8B})
    if SAMBANOVA_API_KEY:
        models.append({"id": "sambanova",       "label": "🚀 SambaNova — Llama 3.3 70B",       "provider": "sambanova", "model": SAMBANOVA_MODEL})
    if NVIDIA_API_KEY:
        models.append({"id": "nvidia_70b",      "label": "🟢 NVIDIA NIM — Llama 3.3 70B",      "provider": "nvidia",    "model": NVIDIA_MODEL_70B})
        models.append({"id": "nvidia_maverick", "label": "🟢 NVIDIA NIM — Llama 4 Maverick 🔥", "provider": "nvidia",    "model": NVIDIA_MODEL_MAVERICK})
    return models

# ============================================================
#   LOG CHANNEL — disabled
# ============================================================
LOG_CHANNEL_ID = 0

# ============================================================
#   DELAY SETTINGS
# ============================================================
MIN_DELAY       = 1.2
MAX_DELAY       = 3.5
TYPING_SPEED    = 0.04
MAX_TYPING_TIME = 6.0

# ============================================================
#   REACTIONS
# ============================================================
POSITIVE_REACTIONS = ["❤️", "🔥", "👍", "😍", "🤩", "💯", "🎉", "✨", "🫡"]
NEGATIVE_REACTIONS = ["😢", "💔", "😮", "🫤"]
NEUTRAL_REACTIONS  = ["👍", "🤝", "👀", "😂", "🤔", "💀"]

# ============================================================
#   BUSY / FALLBACK MESSAGES
#   — Rotates daily so same excuse never repeats
#   — 30 unique sets: leaving / busy / sleeping (10 each)
# ============================================================

_LEAVING_MSGS = [
    "Chal nikalna hai, baad mein baat karte hain 🙏",
    "Kuch urgent aa gaya, thodi der mein wapas aata hun",
    "Abhi door hun, ping karo kal",
    "Nikal raha hun, catch you later ✌️",
    "Bhai kaam hai yaar, baad mein",
    "Jaana padega, hold on karo",
    "Running out, talk soon",
    "Gotta go, ping me later",
    "Thoda busy ho gaya hun, baad mein",
    "Abhi nahi yaar, thodi der mein 🙏",
]
_BUSY_MSGS = [
    "Abhi kaam mein hun, ek second",
    "Thoda tied up hun, baad mein reply karunga",
    "Busy hun yaar, wait karo",
    "Kuch kaam hai, thodi der mein",
    "Distracted hun abhi, baad mein",
    "Occupied right now, bear with me",
    "Stuck in something, brb",
    "On it with something else, hold on",
    "Thoda handle karna hai kuch, 2 min",
    "Mid something, hit me later",
]
_SLEEPING_MSGS = [
    "Neend aa rahi hai yaar 😴 kal milte hain",
    "Sone ja raha hun, good night",
    "Off ho raha hun, kal baat karte hain",
    "Tired hun, rest kar raha hun",
    "Calling it a night, talk tmrw 🌙",
    "Ja raha hun so, baad mein",
    "Eyes closing, gn yaar",
    "Dozing off, catch you tmrw",
    "Kal baat karte hain, raat ho gayi",
    "Sleep mode on 😴",
]

def _day_seed() -> int:
    """Same seed for the whole day → same message set each day, changes next day."""
    d = datetime.date.today()
    return d.year * 10000 + d.month * 100 + d.day

def get_busy_message() -> str:
    """Returns a random busy message. Pool changes every day."""
    rng   = random.Random(_day_seed())
    pool  = _LEAVING_MSGS + _BUSY_MSGS + _SLEEPING_MSGS
    # Pick 5 for today, then random from those 5
    today_pool = rng.sample(pool, 5)
    return random.choice(today_pool)

# Keep BUSY_MESSAGES list for backward compat (ai_handler uses random.choice)
BUSY_MESSAGES = _LEAVING_MSGS + _BUSY_MSGS + _SLEEPING_MSGS

# ============================================================
#   SCHEDULED MESSAGE TEMPLATES
# ============================================================
MORNING_MSGS = [
    "Good morning! ☀️ Aaj ka din ekdam mast ho tumhara!",
    "Subah bakhair! 🌅 Uth jao, duniya tumhara intezaar kar rahi hai!",
    "Rise and shine! ✨ Aaj bhi ek naya din, nayi opportunities!",
    "Good morning! ☕ Chai/coffee pi lo aur day start karo!",
    "Wakey wakey! 🌞 Aaj kuch naya aur kuch mast hoga zaroor!",
]
AFTERNOON_MSGS = [
    "Good afternoon! ☀️ Umeed hai din accha ja raha hai!",
    "Dopahar mubarak! 🌤️ Thoda break lo, kuch khao!",
    "Hey! Afternoon check-in — sab theek? 😊",
    "Good afternoon! Halfway through the day — keep it up! 💪",
]
NIGHT_MSGS = [
    "Good night! 🌙 Neend achhi aaye, sweet dreams!",
    "Raat bakhair! 😴 So jao ab, kal phir milenge!",
    "Shab bakhair! 🌟 Aaj jo bhi kiya, mast tha — rest karo!",
    "Good night! 🌙 Phone rakh do ab aur so jao 😄",
    "Sleep tight! ✨ Kal fir baat karte hain!",
]
