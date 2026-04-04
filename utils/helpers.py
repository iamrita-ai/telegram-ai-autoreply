import asyncio
import random
import datetime
from config import MIN_DELAY, MAX_DELAY, TYPING_SPEED, MAX_TYPING_TIME
from database.mongo import get_setting

# ── Sentiment Detection ────────────────────────────────────────
_POSITIVE = ["good","great","awesome","love","happy","thanks","thank","nice","wow",
             "amazing","accha","badiya","shukriya","pyar","mast","zabardast","superb"]
_NEGATIVE = ["bad","hate","sad","angry","worst","bura","nahi","problem","boring",
             "terrible","awful","horrible","bekar","faltu"]

def detect_sentiment(text: str) -> str:
    t   = text.lower()
    pos = sum(1 for w in _POSITIVE if w in t)
    neg = sum(1 for w in _NEGATIVE if w in t)
    if pos > neg:  return "positive"
    if neg > pos:  return "negative"
    return "neutral"

# ── Typing Simulation ──────────────────────────────────────────
async def simulate_typing(client, chat_id, reply_text: str, source_text: str = ""):
    min_d      = await get_setting("min_delay_override", MIN_DELAY)
    base_delay = random.uniform(float(min_d), MAX_DELAY)
    word_count = len(source_text.split()) if source_text else 0
    reading_bonus = min(word_count * 0.08, 4.0)
    await asyncio.sleep(base_delay + reading_bonus)

    typing_time = min(len(reply_text) * TYPING_SPEED, MAX_TYPING_TIME)
    typing_time = max(0.8, typing_time + random.uniform(-0.2, 0.4))
    async with client.action(chat_id, "typing"):
        await asyncio.sleep(typing_time)

# ── Reactions ─────────────────────────────────────────────────
from config import POSITIVE_REACTIONS, NEGATIVE_REACTIONS, NEUTRAL_REACTIONS

async def send_reaction(client, message, sentiment: str = "neutral"):
    try:
        pool = (
            POSITIVE_REACTIONS if sentiment == "positive"
            else NEGATIVE_REACTIONS if sentiment == "negative"
            else NEUTRAL_REACTIONS
        )
        await client.send_reaction(message.chat_id, message.id, random.choice(pool), big=True)
    except Exception:
        pass

# ── DND Check ─────────────────────────────────────────────────
async def is_dnd_active() -> bool:
    from database.mongo import get_dnd
    dnd = await get_dnd()
    if not dnd:
        return False
    try:
        start_str, end_str = dnd.split("-")
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        now     = datetime.datetime.now()
        current = now.hour * 60 + now.minute
        start   = sh * 60 + sm
        end     = eh * 60 + em
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end
    except Exception:
        return False
