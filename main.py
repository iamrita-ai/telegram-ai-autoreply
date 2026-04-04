import sys
import os

# Fix module path for Render & Docker
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

from config import API_ID, API_HASH, BOT_TOKEN, OWNER_IDS
from database.mongo import (
    load_session, is_locked, is_blacklisted, increment_stat,
)
from handlers.ai_handler  import get_ai_reply
from handlers.bot_handler import register_bot_handlers, set_user_client
from handlers.logger      import log_message, log_edited, log_startup
from handlers.scheduler   import run_scheduler
from utils.helpers        import simulate_typing, send_reaction, detect_sentiment, is_dnd_active

user_client = None
_me_username = None


def _attach_userbot_handlers(client: TelegramClient):

    @client.on(events.NewMessage(incoming=True))
    async def handle_incoming(event):
        try:
            if await is_locked():
                return
            sender = await event.get_sender()
            if isinstance(sender, User) and sender.bot:
                return
            if event.sender_id in OWNER_IDS:
                return
            if event.photo or event.video or event.gif or event.sticker or event.voice or event.audio:
                return
            if not event.text or not event.text.strip():
                return
            if await is_blacklisted(event.sender_id):
                return

            is_private   = event.is_private
            is_mentioned = event.mentioned
            if not is_private and not is_mentioned:
                return

            if await is_dnd_active():
                if is_private:
                    await simulate_typing(client, event.chat_id, "Sone ja raha hun")
                    await event.reply("😴 Sone ja raha hun, kal baat karte hain!")
                return

            reply, _ = await get_ai_reply(
                event.sender_id, event.text, me_username=_me_username
            )
            sentiment = detect_sentiment(event.text)
            await send_reaction(client, event, sentiment)
            await simulate_typing(client, event.chat_id, reply, source_text=event.text)
            await event.reply(reply)
            await log_message(client, event, reply, is_group=not is_private)
            await increment_stat("total_replies")
            await increment_stat("today_replies")

        except Exception as e:
            print(f"[Handler] {e}")

    @client.on(events.MessageEdited(incoming=True))
    async def handle_edited(event):
        try:
            sender = await event.get_sender()
            if isinstance(sender, User) and sender.bot:
                return
            if event.sender_id in OWNER_IDS:
                return
            if event.text:
                await log_edited(client, event)
        except Exception as e:
            print(f"[EditHandler] {e}")


async def start_user_client():
    global user_client, _me_username
    session_str = await load_session()
    if not session_str:
        print("[UserClient] No session. Use /login in control bot.")
        return

    if user_client and user_client.is_connected():
        await user_client.disconnect()

    user_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await user_client.connect()

    if not await user_client.is_user_authorized():
        print("[UserClient] Session invalid. Use /login again.")
        return

    me = await user_client.get_me()
    _me_username = me.username
    print(f"[UserClient] Logged in as: {me.first_name} @{me.username} ({me.id})")

    _attach_userbot_handlers(user_client)
    set_user_client(user_client)
    await log_startup(user_client, me)
    asyncio.create_task(run_scheduler(user_client, me.username))
    asyncio.create_task(user_client.run_until_disconnected())
    print("[UserClient] Running!")


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

    print("[System] All services ready.")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[Web] Keep-alive on :{port}")

    asyncio.run(main())
