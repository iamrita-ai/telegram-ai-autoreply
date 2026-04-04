"""
scheduler.py — AI-generated greetings, unique every time.
Supports Groq, SambaNova, NVIDIA NIM.
"""
import asyncio
import datetime
import httpx
import random

from config import (
    SAMBANOVA_API_KEY, GROQ_API_KEY, NVIDIA_API_KEY,
    SAMBANOVA_MODEL, GROQ_MODEL_70B, NVIDIA_MODEL_70B,
)
from database.mongo import get_active_schedules, increment_stat, get_setting

SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
NVIDIA_URL    = "https://integrate.api.nvidia.com/v1/chat/completions"

_sent_history: dict = {}
_HISTORY_SIZE = 10

PERSONA_MAP = {
    "TechnicalSerena": "Serena",
    "technicalserena": "Serena",
    "Xioqui_Xin":      "Xioqui",
    "xioqui_xin":      "Xioqui",
}

def _persona_name(username) -> str:
    return PERSONA_MAP.get(username or "", "me")


async def _try_api(url: str, api_key: str, model: str, system: str, user_msg: str):
    if not api_key:
        return None
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        "max_tokens": 80, "temperature": 1.1,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Scheduler] API error {url}: {e}")
    return None


async def _generate_greeting(stype: str, username, uid: int) -> str:
    persona = _persona_name(username)
    past    = _sent_history.get(uid, [])
    avoid   = "\n".join(f"- {m}" for m in past[-6:]) if past else "None"
    time_map = {"morning": "good morning", "afternoon": "good afternoon", "night": "good night / goodbye for the night"}
    greeting_type = time_map.get(stype, stype)

    system = (
        f"You are {persona}, a real person texting a friend on Telegram. "
        f"Write a short, warm, natural {greeting_type} message. "
        f"Sound human — casual, friendly, maybe a tiny bit playful. "
        f"1-2 sentences max. 1 emoji max. "
        f"Never repeat these previous messages:\n{avoid}\n"
        f"Do NOT include quotes or explanation — just the message itself."
    )
    user_msg = f"Send a {greeting_type} message."

    # Get preferred model from DB, try it first
    preferred = await get_setting("preferred_model", "sambanova")
    order = [preferred] + [m for m in ["sambanova", "groq_70b", "nvidia_70b"] if m != preferred]

    model_map = {
        "sambanova":   (SAMBANOVA_URL, SAMBANOVA_API_KEY, SAMBANOVA_MODEL),
        "groq_70b":    (GROQ_URL,      GROQ_API_KEY,      GROQ_MODEL_70B),
        "groq_8b":     (GROQ_URL,      GROQ_API_KEY,      "llama-3.1-8b-instant"),
        "nvidia_70b":  (NVIDIA_URL,    NVIDIA_API_KEY,    NVIDIA_MODEL_70B),
        "nvidia_maverick": (NVIDIA_URL, NVIDIA_API_KEY,   "meta/llama-4-maverick-17b-128e-instruct"),
    }

    for mid in order:
        info = model_map.get(mid)
        if not info:
            continue
        url, key, model = info
        result = await _try_api(url, key, model, system, user_msg)
        if result:
            return result

    # Hard fallback
    fallbacks = {
        "morning":   ["Good morning! ☀️", "Morning! Hope your day's great 🌅", "Rise and shine! ✨"],
        "afternoon": ["Good afternoon! 😊", "Hey, hope your day's going well! ☀️"],
        "night":     ["Good night! 🌙", "Sleep well! 😴", "Night! Rest up ✨"],
    }
    options  = fallbacks.get(stype, ["Hey! 👋"])
    past_set = set(past)
    fresh    = [m for m in options if m not in past_set]
    return random.choice(fresh if fresh else options)


def _record_sent(uid: int, msg: str):
    _sent_history.setdefault(uid, []).append(msg)
    if len(_sent_history[uid]) > _HISTORY_SIZE:
        _sent_history[uid] = _sent_history[uid][-_HISTORY_SIZE:]


async def run_scheduler(client, me_username=None):
    print("[Scheduler] Started.")
    while True:
        try:
            now          = datetime.datetime.now()
            current_time = now.strftime("%H:%M")
            schedules    = await get_active_schedules()

            for s in schedules:
                if s.get("time") != current_time:
                    continue
                uid   = s["user_id"]
                stype = s["type"]

                msg = await _generate_greeting(stype, me_username, uid)
                _record_sent(uid, msg)

                try:
                    await client.send_message(uid, msg)
                    await increment_stat("total_replies")
                    print(f"[Scheduler] Sent {stype} to {uid}: {msg}")
                except Exception as e:
                    print(f"[Scheduler] Send error → {uid}: {e}")

        except Exception as e:
            print(f"[Scheduler] Loop error: {e}")

        await asyncio.sleep(60)
