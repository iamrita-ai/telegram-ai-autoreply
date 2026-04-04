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

# ── Typing Simulation (ChatGPT-style) ─────────────────────────
async def simulate_typing(client, chat_id, reply_text: str, source_text: str = ""):
    min_d = await get_setting("min_delay_override", MIN_DELAY)

    word_count    = len(source_text.split()) if source_text else 0
    reading_delay = min(word_count * 0.09, 3.5) + random.uniform(0.5, 1.2)
    await asyncio.sleep(reading_delay)

    char_count  = len(reply_text)
    typing_time = min(char_count * TYPING_SPEED, MAX_TYPING_TIME)
    typing_time = max(1.0, typing_time + random.uniform(-0.3, 0.5))

    if typing_time > 3.5:
        chunk1 = typing_time * random.uniform(0.45, 0.60)
        pause  = random.uniform(0.4, 0.9)
        chunk2 = typing_time - chunk1
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(chunk1)
        await asyncio.sleep(pause)
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(chunk2)
    else:
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(typing_time)

    await asyncio.sleep(random.uniform(0.1, 0.3))


# ── Reactions (Big animated — works on all Telethon versions) ──
from config import POSITIVE_REACTIONS, NEGATIVE_REACTIONS, NEUTRAL_REACTIONS

async def send_reaction(client, event, sentiment: str = "neutral"):
    """
    Big animated reaction using raw TL layer directly.
    Tries 3 methods in order until one works.
    """
    try:
        pool = (
            POSITIVE_REACTIONS if sentiment == "positive"
            else NEGATIVE_REACTIONS if sentiment == "negative"
            else NEUTRAL_REACTIONS
        )
        emoji = random.choice(pool)

        # Method 1: Most modern Telethon (reactions= list)
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            await client(SendReactionRequest(
                peer      = event.chat_id,
                msg_id    = event.id,
                big       = True,
                reactions = [ReactionEmoji(emoticon=emoji)],
            ))
            return
        except TypeError:
            pass

        # Method 2: Older Telethon (reaction= singular)
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            await client(SendReactionRequest(
                peer     = event.chat_id,
                msg_id   = event.id,
                big      = True,
                reaction = [ReactionEmoji(emoticon=emoji)],
            ))
            return
        except TypeError:
            pass

        # Method 3: Very old Telethon — emoji string directly
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            await client(SendReactionRequest(
                peer     = event.chat_id,
                msg_id   = event.id,
                reaction = emoji,
            ))
            return
        except Exception:
            pass

    except Exception:
        pass  # Silently skip — old groups block reactions


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
