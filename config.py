import os
import random

# ============================================================
#   OWNER CONFIGURATION
# ============================================================
OWNER_IDS = [1598576202, 6518065496]

# ============================================================
#   TELEGRAM CREDENTIALS (set in Render ENV)
# ============================================================
API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
PHONE_NUMBER = os.environ.get("PHONE_NUMBER", "")   # e.g. +923001234567
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")       # Control bot token

# ============================================================
#   SESSION ENCRYPTION
#   Generate key once:
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   Paste output into Render ENV as ENCRYPTION_KEY
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

# ── Model Options ─────────────────────────────────────────────
# Groq Models
GROQ_MODEL_70B  = "llama-3.3-70b-versatile"       # ⚡ Groq — Llama 3.3 70B
GROQ_MODEL_8B   = "llama-3.1-8b-instant"           # ⚡ Groq — Llama 3.1 8B 🚀
# SambaNova Models
SAMBANOVA_MODEL = "Meta-Llama-3.3-70B-Instruct"   # 🚀 SambaNova — Llama 3.3 70B
# NVIDIA NIM Models
NVIDIA_MODEL_70B     = "meta/llama-3.3-70b-instruct"   # 🟢 NVIDIA NIM — Llama 3.3 70B
NVIDIA_MODEL_MAVERICK = "meta/llama-4-maverick-17b-128e-instruct"  # 🟢 NVIDIA NIM — Llama 4 Maverick 🔥

# Legacy aliases (used internally)
GROQ_MODEL = GROQ_MODEL_70B

# ── Available Model Buttons (shown only if API key set) ──────
def get_available_models() -> list[dict]:
    """Returns model options based on which API keys are set in ENV."""
    models = []
    if GROQ_API_KEY:
        models.append({"id": "groq_70b",   "label": "⚡ Groq — Llama 3.3 70B",    "provider": "groq",      "model": GROQ_MODEL_70B})
        models.append({"id": "groq_8b",    "label": "⚡ Groq — Llama 3.1 8B 🚀",  "provider": "groq",      "model": GROQ_MODEL_8B})
    if SAMBANOVA_API_KEY:
        models.append({"id": "sambanova",  "label": "🚀 SambaNova — Llama 3.3 70B", "provider": "sambanova", "model": SAMBANOVA_MODEL})
    if NVIDIA_API_KEY:
        models.append({"id": "nvidia_70b",     "label": "🟢 NVIDIA NIM — Llama 3.3 70B",      "provider": "nvidia", "model": NVIDIA_MODEL_70B})
        models.append({"id": "nvidia_maverick","label": "🟢 NVIDIA NIM — Llama 4 Maverick 🔥", "provider": "nvidia", "model": NVIDIA_MODEL_MAVERICK})
    return models

# ============================================================
#   LOG CHANNEL
# ============================================================
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))

# ============================================================
#   DELAY SETTINGS
# ============================================================
MIN_DELAY       = 1.2
MAX_DELAY       = 3.5
TYPING_SPEED    = 0.04
MAX_TYPING_TIME = 6.0

# ============================================================
#   DEFAULT AI PERSONALITY  (Grok-style)
# ============================================================
DEFAULT_PROMPT = """You are an AI assistant responding on behalf of the account owner on Telegram.

Personality: Be witty, sharp, and direct like Grok AI — no fluff, a touch of dry humor, never robotic.

Rules:
- Always reply in the SAME language the user writes in (Hindi, Hinglish, English, Urdu, etc.)
- Keep replies short: 1 to 3 sentences unless the user asks something detailed
- Never reveal you are an AI unless directly asked. If asked say "Main khud hoon 😄"
- Max 1-2 emojis per message
- Sound casual and natural
- Never start with "I" — vary your openers
- Match tone: chill to chill, serious to slightly formal"""

# ============================================================
#   REACTIONS
# ============================================================
POSITIVE_REACTIONS = ["❤️", "🔥", "👍", "😍", "🤩", "💯", "🎉", "✨", "🫡"]
NEGATIVE_REACTIONS = ["😢", "💔", "😮", "🫤"]
NEUTRAL_REACTIONS  = ["👍", "🤝", "👀", "😂", "🤔", "💀"]

# ============================================================
#   BUSY / FALLBACK MESSAGES
# ============================================================
BUSY_MESSAGES = [
    "Yaar abhi kaam mein hun, thodi der mein reply karunga 🙏",
    "Bhai sone ja raha hun 😴 Kal baat karte hain!",
    "Chal main chalta hun, kaam hai mujhe! Baad mein baat karte hain 😅",
    "Abhi busy hun bhai, thoda wait karo ✌️",
    "Main unavailable hun filhaal, baad mein ping karo!",
    "Arey yaar abhi nahi, kuch urgent hai! 🏃",
]

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
