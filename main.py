import sys, os

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, "/app", "/opt/render/project/src"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User
from telethon.tl.functions.account import UpdateStatusRequest

from config import API_ID, API_HASH, BOT_TOKEN, OWNER_IDS
from database.mongo import (
    load_session, load_all_sessions,
    is_locked, is_blacklisted, increment_stat,
    is_group_allowed, auto_cleanup_history,
)
from handlers.ai_handler  import get_ai_reply
from handlers.bot_handler import register_bot_handlers, set_user_client
from handlers.logger      import log_message, log_edited, log_startup
from handlers.scheduler   import run_scheduler
from utils.helpers        import simulate_typing, send_reaction, detect_sentiment, is_dnd_active

_active_clients: list[TelegramClient] = []
_me_usernames:   list[str]            = []
_me_ids:         list[int]            = []


def _attach_userbot_handlers(client: TelegramClient, me_username: str, me_id: int):

    @client.on(events.NewMessage(incoming=True))
    async def handle_incoming(event):
        try:
            if await is_locked(): return

            sender = await event.get_sender()
            if isinstance(sender, User) and sender.bot: return
            if event.sender_id == me_id: return

            if event.photo or event.video or event.gif or event.sticker or event.voice or event.audio: return
            if not event.text or not event.text.strip(): return
            if await is_blacklisted(event.sender_id): return

            is_private   = event.is_private
            is_mentioned = event.mentioned

            # ── Group: only reply if group is allowed AND bot is mentioned
            if not is_private:
                if not is_mentioned: return
                if not await is_group_allowed(event.chat_id): return

            if await is_dnd_active():
                if is_private:
                    await simulate_typing(client, event.chat_id, "Sone ja raha hun")
                    await event.reply("😴 Sone ja raha hun, kal baat karte hain!")
                return

            # ── Get AI reply (handles incomplete message buffering)
            reply, is_busy = await get_ai_reply(
                event.sender_id,
                event.text,
                me_username=me_username,
                is_group=not is_private,
            )

            # reply=None means message was buffered (incomplete) — wait for more
            if reply is None:
                return

            sentiment = detect_sentiment(event.text)
            await send_reaction(client, event, sentiment)
            await simulate_typing(client, event.chat_id, reply, source_text=event.text)
            await event.reply(reply)
            await log_message(client, event, reply, is_group=not is_private)
            await increment_stat("total_replies")
            await increment_stat("today_replies")

        except Exception as e:
            print(f"[Handler:{me_username}] {e}")

    @client.on(events.MessageEdited(incoming=True))
    async def handle_edited(event):
        try:
            sender = await event.get_sender()
            if isinstance(sender, User) and sender.bot: return
            if event.sender_id == me_id: return
            if event.text:
                await log_edited(client, event)
        except Exception as e:
            print(f"[EditHandler:{me_username}] {e}")


async def _cleanup_loop():
    """Runs every hour — cleans old history automatically."""
    while True:
        try:
            await auto_cleanup_history()
        except Exception as e:
            print(f"[Cleanup] {e}")
        await asyncio.sleep(3600)   # every 1 hour


async def _launch_client(client: TelegramClient, me_obj=None):
    if not client.is_connected():
        await client.connect()

    if not await client.is_user_authorized():
        print("[UserClient] Session invalid.")
        return False

    me       = me_obj or await client.get_me()
    username = me.username or str(me.id)
    me_id    = me.id
    print(f"[UserClient] Active: {me.first_name} @{username} ({me_id})")

    # Always appear offline
    try:
        await client(UpdateStatusRequest(offline=True))
    except Exception:
        pass

    _attach_userbot_handlers(client, username, me_id)
    set_user_client(client)
    await log_startup(client, me)
    asyncio.create_task(run_scheduler(client, username))
    asyncio.create_task(client.run_until_disconnected())

    _active_clients.append(client)
    _me_usernames.append(username)
    _me_ids.append(me_id)
    return True


async def start_user_client(existing_client: TelegramClient = None, me=None):
    if existing_client is not None:
        await _launch_client(existing_client, me_obj=me)
        return

    sessions = await load_all_sessions()
    if not sessions:
        single = await load_session()
        if single:
            sessions = [{"user_id": 0, "session": single}]

    if not sessions:
        print("[UserClient] No sessions found. Use /login in control bot.")
        return

    for s in sessions:
        sess_str = s.get("session")
        if not sess_str:
            continue
        client = TelegramClient(StringSession(sess_str), API_ID, API_HASH)
        ok = await _launch_client(client)
        if not ok:
            print(f"[UserClient] Session for user_id={s.get('user_id')} invalid, skipping.")

    print(f"[UserClient] {len(_active_clients)} account(s) running.")


async def main():
    print("=" * 45)
    print("  Userbot + Control Bot Starting...")
    print("=" * 45)

    bot = TelegramClient("bot_session", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    register_bot_handlers(bot, start_user_client)
    me = await bot.get_me()
    print(f"[ControlBot] @{me.username} running")

    await start_user_client()

    # Start background cleanup loop
    asyncio.create_task(_cleanup_loop())

    print("[System] All services ready.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args): pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[Web] Keep-alive on :{port}")

    asyncio.run(main())
