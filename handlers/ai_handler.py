import random
import httpx
from config import (
    GROQ_API_KEY, SAMBANOVA_API_KEY, NVIDIA_API_KEY,
    GROQ_MODEL_70B, GROQ_MODEL_8B, SAMBANOVA_MODEL,
    NVIDIA_MODEL_70B, NVIDIA_MODEL_MAVERICK,
    BUSY_MESSAGES,
)
from database.mongo import get_prompt, get_conversation, add_message, get_setting

GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"
NVIDIA_URL    = "https://integrate.api.nvidia.com/v1/chat/completions"

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

async def _get_persona_prompt(custom_prompt, me_username):
    if custom_prompt:
        return custom_prompt
    persona = PERSONA_MAP.get(me_username or "", "the account owner")
    gender_line = "You are a female. Respond naturally as a girl would." if persona in ("Serena", "Xioqui") else ""
    return f"""You are {persona}, a real person chatting on Telegram.

{gender_line}

Rules (NEVER break these):
- You are a HUMAN. NEVER say you are an AI, bot, or assistant — under ANY circumstances.
- If asked "are you AI?", casually deny: "Nahi yaar, main hi hun 😄" or "Haha nope, real person 😂"
- Reply in SAME language as user (Hindi, Hinglish, English, Urdu)
- Keep replies short: 1-3 sentences unless detail asked
- Sound casual, warm — like a real person texting
- Max 1-2 emojis per message
- Never start with "I"
- Your name is {persona}"""

async def _call_api(url: str, api_key: str, model: str, messages: list):
    if not api_key:
        return None
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": 500, "temperature": 0.85}
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

async def get_ai_reply(user_id: int, user_message: str, me_username=None):
    custom_prompt = await get_prompt()
    system_prompt = await _get_persona_prompt(custom_prompt, me_username)

    history  = await get_conversation(user_id, limit=10)
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

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

    await add_message(user_id, "user",      user_message)
    await add_message(user_id, "assistant", reply)
    return reply, False
