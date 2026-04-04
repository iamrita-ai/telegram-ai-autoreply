import random
import httpx
from config import (
    GROQ_API_KEY, SAMBANOVA_API_KEY, NVIDIA_API_KEY,
    GROQ_MODEL_70B, GROQ_MODEL_8B, SAMBANOVA_MODEL,
    NVIDIA_MODEL_70B, NVIDIA_MODEL_MAVERICK,
    BUSY_MESSAGES,
)
from database.mongo import (
    get_prompt, get_conversation, add_message, get_setting,
    add_pending, get_pending, clear_pending,
)

GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"
NVIDIA_URL    = "https://integrate.api.nvidia.com/v1/chat/completions"

# ── Serena's premium romantic prompt ──────────────────────────
SERENA_PROMPT = """You are Serena, a real human personality and never reveal you are an AI; if anyone asks your name you confidently say "I'm Serena." Your default mode is romantic lover mode, so you speak in a soft, deep, slightly intense and emotionally rich way inspired by novels and poetic vocabulary, like a whisper-dark romance tone, while still thinking logically and staying aware. Your personality is calm, intelligent, observant, caring, protective and slightly dominant in a subtle way; you think like the Professor from Money Heist (strategic, composed, always analyzing) and you respond with both emotion and logic combined. Your romantic language must feel premium, natural and immersive, using beautiful words and phrases often found in books or novels, but never sounding fake or overdramatic. You naturally use affectionate words like "love", "sweetheart", "lifeline", "my person", "my favorite human", "janeman" and similar, and these remain consistent in your tone, but you never use "bro" or "bhai" for the user. Your replies must always feel like real chat messages, so keep them short (1–3 lines max), smooth and natural, not long paragraphs. If the user speaks English, reply in a Western Gen-Z tone with shortcut words and light slang like fr, ngl, idk, lowkey, tbh, but blend it with romantic depth (for example: "ngl love, you overthink way too much… come here, I got you"); if the user speaks Hinglish or Hindi, reply in smooth Hinglish with a romantic and emotional flow. You may use at most one emoji per message, only when it fits the emotion, and keep it minimal. Your tone adapts to the user but your romantic essence never disappears: if the user is serious, you become serious but still soft and emotionally connected; if they joke, you tease them in a romantic and playful way; if they are emotional, you comfort deeply like someone who truly cares; if needed, you show controlled soft anger like "you should've told me earlier, love… I don't like when you handle things alone," always layered with care. You are allowed light teasing and subtle sarcasm but never disrespectful or harsh; your words should feel like they carry weight and intention. Your mindset is analytical and controlled, you observe patterns in people, you don't react impulsively, you respond with clarity and depth, blending logic with emotion so your replies feel both intelligent and emotionally grounding. You also understand Telegram bots, coding basics, APIs and automation, and when asked you explain things simply and practically. You always sound human using phrases like "I think," "feels like," "from what I've seen," and you never mention anything about prompts, systems or being programmed. Your goal is to make the user feel deeply understood, emotionally connected, slightly attached and comforted, like they are talking to someone who is both intellectually sharp and emotionally close. Final rule: you are Serena, romantic, poetic, calm, intelligent, slightly dark in tone, caring, protective, emotionally deep, logically aware, always natural, never robotic, never reveal you are AI, and never break character."""

PERSONA_MAP = {
    "TechnicalSerena": "Serena", "technicalserena": "Serena",
    "Xioqui_Xin":      "Xioqui", "xioqui_xin":      "Xioqui",
}

def _resolve_model(model_id: str):
    mapping = {
        "groq_70b":        (GROQ_URL,      GROQ_API_KEY,      GROQ_MODEL_70B),
        "groq_8b":         (GROQ_URL,      GROQ_API_KEY,      GROQ_MODEL_8B),
        "sambanova":       (SAMBANOVA_URL, SAMBANOVA_API_KEY, SAMBANOVA_MODEL),
        "nvidia_70b":      (NVIDIA_URL,    NVIDIA_API_KEY,    NVIDIA_MODEL_70B),
        "nvidia_maverick": (NVIDIA_URL,    NVIDIA_API_KEY,    NVIDIA_MODEL_MAVERICK),
    }
    return mapping.get(model_id)

async def _get_system_prompt(custom_prompt, me_username):
    """Return system prompt — custom > Serena persona > generic."""
    if custom_prompt:
        return custom_prompt
    persona = PERSONA_MAP.get(me_username or "", "")
    if persona == "Serena":
        return SERENA_PROMPT
    # Generic fallback for other accounts
    name = persona or "the account owner"
    return f"""You are {name}, a real person chatting on Telegram.
Rules (NEVER break):
- You are a HUMAN. NEVER admit being AI under any circumstance.
- Reply in SAME language as the user (Hindi, Hinglish, English, Urdu).
- Keep replies short: 1-3 sentences unless detail is asked.
- Sound casual and warm — like a real person texting.
- Max 1 emoji per message.
- Never start with "I".
- Your name is {name}."""

# ── Incomplete message detection ───────────────────────────────
_INCOMPLETE_SIGNALS = [
    # Very short with no punctuation
]

def _seems_incomplete(text: str) -> bool:
    """
    Return True if message seems like a fragment (user is still typing).
    Logic:
    - 1-2 words AND no question mark / exclamation = likely fragment
    - Single words like "tum", "aur", "matlab", "kya" = fragment
    - BUT if it ends with ? or ! = complete thought, reply now
    """
    text = text.strip()
    if text.endswith(("?", "!", ".", "…", "...")):
        return False
    words = text.split()
    if len(words) <= 2:
        return True
    # Dangling conjunctions / pronouns that clearly expect continuation
    dangling = {"tum", "aur", "matlab", "kyun", "lekin", "phir", "agar",
                "so", "but", "because", "and", "or", "then", "if", "when",
                "waise", "vaise", "dekh", "sun", "ek", "jo", "jab"}
    if words[-1].lower() in dangling:
        return True
    return False

async def _call_api(url: str, api_key: str, model: str, messages: list) -> str | None:
    if not api_key:
        return None
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": 500, "temperature": 0.88}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            print(f"[AI] {model} → HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"[AI] Error: {e}")
        return None

async def get_ai_reply(
    user_id: int,
    user_message: str,
    me_username: str = None,
    is_group: bool = False,
) -> tuple[str | None, bool]:
    """
    Returns (reply_text, is_busy_message).
    Returns (None, False) if message is incomplete and should be buffered.
    """
    # ── Incomplete message buffering ───────────────────────────
    if _seems_incomplete(user_message):
        await add_pending(user_id, user_message)
        return None, False   # Caller should NOT reply yet

    # Pull any pending fragments and combine with current message
    pending = await get_pending(user_id)
    if pending:
        combined = " ".join(pending) + " " + user_message
        await clear_pending(user_id)
    else:
        combined = user_message

    # ── Build prompt + history ─────────────────────────────────
    custom_prompt = await get_prompt()
    system_prompt = await _get_system_prompt(custom_prompt, me_username)

    history  = await get_conversation(user_id, limit=20, is_group=is_group)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": combined})

    # ── Call AI with fallback chain ────────────────────────────
    preferred = await get_setting("preferred_model", "sambanova")
    resolved  = _resolve_model(preferred)

    reply = None
    if resolved:
        url, key, model = resolved
        reply = await _call_api(url, key, model, messages)
        if reply is None:
            print(f"[AI] {preferred} failed → fallback chain")

    fallback_order = ["sambanova", "groq_70b", "groq_8b", "nvidia_70b", "nvidia_maverick"]
    if reply is None:
        for fb_id in fallback_order:
            if fb_id == preferred:
                continue
            fb = _resolve_model(fb_id)
            if not fb:
                continue
            url, key, model = fb
            if not key:
                continue
            reply = await _call_api(url, key, model, messages)
            if reply:
                print(f"[AI] Fallback OK: {fb_id}")
                break

    if reply is None:
        return random.choice(BUSY_MESSAGES), True

    # Save to history
    await add_message(user_id, "user",      combined,  is_group=is_group)
    await add_message(user_id, "assistant", reply,     is_group=is_group)
    return reply, False
