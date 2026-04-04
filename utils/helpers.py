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

# ── Typing Simulation (ChatGPT-style animated cursor) ─────────
async def simulate_typing(client, chat_id, reply_text: str, source_text: str = ""):
    """
    Realistic typing animation:
    1. Reading delay  — simulates reading the user's message
    2. Thinking pause — short pause before starting to type
    3. Typing action  — shows 'typing...' while 'composing' reply
       - For longer replies: pauses and restarts typing action (like real user)
    """
    min_d = await get_setting("min_delay_override", MIN_DELAY)

    # Phase 1: reading delay (based on incoming message length)
    word_count    = len(source_text.split()) if source_text else 0
    reading_delay = min(word_count * 0.09, 3.5) + random.uniform(0.5, 1.2)
    await asyncio.sleep(reading_delay)

    # Phase 2: calculate realistic typing duration
    char_count   = len(reply_text)
    typing_time  = min(char_count * TYPING_SPEED, MAX_TYPING_TIME)
    typing_time  = max(1.0, typing_time + random.uniform(-0.3, 0.5))

    # Phase 3: chunked typing animation (mimics human pauses mid-sentence)
    # Split into 1–3 chunks for realism on longer replies
    if typing_time > 3.5:
        # Two bursts of typing with a tiny pause (like thinking)
        chunk1 = typing_time * random.uniform(0.45, 0.60)
        pause  = random.uniform(0.4, 0.9)
        chunk2 = typing_time - chunk1

        async with client.action(chat_id, "typing"):
            await asyncio.sleep(chunk1)
        await asyncio.sleep(pause)                        # brief "thinking" gap
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(chunk2)
    else:
        # Short reply — single continuous typing burst
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)

    # Small final delay before message appears (natural send latency)
    await asyncio.sleep(random.uniform(0.1, 0.3))


# ── Reactions (Big animated reaction) ─────────────────────────
from config import POSITIVE_REACTIONS, NEGATIVE_REACTIONS, NEUTRAL_REACTIONS
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

async def send_reaction(client, event, sentiment: str = "neutral"):
    """
    Send a big animated reaction to the user's message.
    Uses Telethon's raw API (SendReactionRequest) for proper big=True support.
    """
    try:
        pool = (
            POSITIVE_REACTIONS if sentiment == "positive"
            else NEGATIVE_REACTIONS if sentiment == "negative"
            else NEUTRAL_REACTIONS
        )
        emoji = random.choice(pool)

        await client(SendReactionRequest(
            peer      = event.chat_id,
            msg_id    = event.id,
            big       = True,           # ← animated big reaction ✅
            reactions = [ReactionEmoji(emoticon=emoji)],
        ))
    except Exception as e:
        # Reactions may fail in old groups/channels — silently ignore
        print(f"[Reaction] Failed: {e}")


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
