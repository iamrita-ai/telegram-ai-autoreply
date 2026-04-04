"""
bot_handler.py — Telegram Control Bot with inline model selector
"""
import re
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

from config import API_ID, API_HASH, OWNER_IDS, get_available_models
from database.mongo import (
    save_session, load_session, delete_session,
    set_setting, get_setting, is_locked, get_prompt,
    get_stat, blacklist_user, unblacklist_user,
    whitelist_user, unwhitelist_user, clear_history,
    set_dnd, get_dnd, add_schedule, remove_schedule,
    set_login_state, get_login_state,
)

_user_client_ref      = []
_start_user_client_fn = None

def set_user_client(client):
    if _user_client_ref: _user_client_ref[0] = client
    else: _user_client_ref.append(client)

def get_user_client():
    return _user_client_ref[0] if _user_client_ref else None

def _is_owner(event):
    return event.sender_id in OWNER_IDS

def _clean_otp(raw: str) -> str:
    return re.sub(r"[^0-9]", "", raw)

async def _reply(event, text: str, buttons=None):
    await event.respond(text, parse_mode="markdown", buttons=buttons)

def _build_model_buttons():
    available = get_available_models()
    if not available:
        return None
    return [[Button.inline(m["label"], data=f"model:{m['id']}")] for m in available]

def register_bot_handlers(bot: TelegramClient, start_user_client_fn):
    global _start_user_client_fn
    _start_user_client_fn = start_user_client_fn

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def cmd_start(event):
        if not _is_owner(event): return
        session = await load_session()
        status  = "🟢 Logged in" if session else "🔴 Not logged in — send /login"
        await _reply(event,
            f"🤖 **Userbot Control Panel**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Account: {status}\n\n"
            f"Send /help for all commands.")

    @bot.on(events.NewMessage(pattern=r"^/login$"))
    async def cmd_login(event):
        if not _is_owner(event): return
        if await load_session():
            await _reply(event, "✅ Already logged in! Use /logout first to switch.")
            return
        await set_login_state({"step": "awaiting_phone"})
        await _reply(event, "📱 Send your **phone number** with country code:\nExample: `+923001234567`")

    @bot.on(events.NewMessage())
    async def handle_login_input(event):
        if not _is_owner(event): return
        if not event.text: return
        if event.text.startswith("/"): return
        state = await get_login_state()
        if not state: return
        step = state.get("step")

        if step == "awaiting_phone":
            phone = event.text.strip()
            if not phone.startswith("+"):
                await _reply(event, "❗ Include country code. Example: `+923001234567`")
                return
            try:
                tmp = TelegramClient(StringSession(), API_ID, API_HASH)
                await tmp.connect()
                result = await tmp.send_code_request(phone)
                await set_login_state({"step": "awaiting_otp", "phone": phone, "phone_code_hash": result.phone_code_hash})
                await tmp.disconnect()
                await _reply(event, f"✅ OTP sent to `{phone}`\n\nSend the OTP here.\n_(Spaces allowed — e.g. `5 7 2 0 0 2`)_")
            except Exception as e:
                await set_login_state(None)
                await _reply(event, f"❌ Error sending OTP: `{e}`\nTry /login again.")

        elif step == "awaiting_otp":
            otp   = _clean_otp(event.text.strip())
            phone = state.get("phone")
            if len(otp) < 5:
                await _reply(event, "❗ Invalid OTP. Try again.")
                return
            tmp = TelegramClient(StringSession(), API_ID, API_HASH)
            try:
                await tmp.connect()
                await tmp.sign_in(phone, otp, phone_code_hash=state["phone_code_hash"])
                sess = tmp.session.save()
                await save_session(sess)
                await set_login_state(None)
                await tmp.disconnect()
                await _reply(event, "✅ **Logged in!** Session encrypted & saved 🔐")
                await _boot_user_client(event)
            except SessionPasswordNeededError:
                sess_so_far = tmp.session.save()
                await tmp.disconnect()
                await set_login_state({"step": "awaiting_2fa", "phone": phone, "session_so_far": sess_so_far})
                await _reply(event, "🔐 **2FA enabled.** Send your Telegram password:")
            except PhoneCodeInvalidError:
                await set_login_state(None)
                await _reply(event, "❌ Wrong OTP! Send /login to start over.")
            except Exception as e:
                await set_login_state(None)
                await _reply(event, f"❌ Error: `{e}`\nSend /login to retry.")

        elif step == "awaiting_2fa":
            password = event.text.strip()
            try:
                tmp = TelegramClient(StringSession(state.get("session_so_far", "")), API_ID, API_HASH)
                await tmp.connect()
                await tmp.sign_in(password=password)
                sess = tmp.session.save()
                await save_session(sess)
                await set_login_state(None)
                await tmp.disconnect()
                await _reply(event, "✅ **2FA verified! Logged in.** Session saved 🔐")
                await _boot_user_client(event)
            except Exception as e:
                await set_login_state(None)
                await _reply(event, f"❌ Wrong password: `{e}`\nSend /login again.")

    async def _boot_user_client(event):
        try:
            await _reply(event, "⏳ Starting userbot...")
            await _start_user_client_fn()
            await _reply(event, "🚀 **Userbot is now running!**")
        except Exception as e:
            await _reply(event, f"⚠️ Start error: `{e}`")

    @bot.on(events.NewMessage(pattern=r"^/logout$"))
    async def cmd_logout(event):
        if not _is_owner(event): return
        uc = get_user_client()
        if uc:
            try: await uc.log_out()
            except Exception: pass
        await delete_session()
        await _reply(event, "👋 **Logged out.** Session deleted.")

    @bot.on(events.NewMessage(pattern=r"^/lock$"))
    async def cmd_lock(event):
        if not _is_owner(event): return
        await set_setting("locked", True)
        await _reply(event, "🔒 **Locked!** No auto-replies.")

    @bot.on(events.NewMessage(pattern=r"^/unlock$"))
    async def cmd_unlock(event):
        if not _is_owner(event): return
        await set_setting("locked", False)
        await _reply(event, "🔓 **Unlocked!** Auto-replies active.")

    @bot.on(events.NewMessage(pattern=r"^/status$"))
    async def cmd_status(event):
        if not _is_owner(event): return
        locked  = await is_locked()
        prompt  = await get_prompt()
        model   = await get_setting("preferred_model", "sambanova")
        dnd     = await get_dnd()
        total   = await get_stat("total_replies")
        today   = await get_stat("today_replies")
        session = await load_session()
        available   = get_available_models()
        model_label = next((m["label"] for m in available if m["id"] == model), model)
        await _reply(event,
            f"⚡ **Status**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔑 **Logged in:** {'Yes ✅' if session else 'No ❌'}\n"
            f"🔒 **Lock:** {'Locked 🔴' if locked else 'Active 🟢'}\n"
            f"🤖 **Model:** {model_label}\n"
            f"💬 **Total Replies:** {total}\n"
            f"📅 **Today:** {today}\n"
            f"😴 **DND:** {dnd or 'Off'}\n"
            f"📝 **Prompt:** {'Custom ✅' if prompt else 'Default'}\n"
            f"━━━━━━━━━━━━━━━━━")

    @bot.on(events.NewMessage(pattern=r"^/stats$"))
    async def cmd_stats(event):
        if not _is_owner(event): return
        total = await get_stat("total_replies")
        today = await get_stat("today_replies")
        await _reply(event, f"📊 **Stats**\n💬 Total: `{total}`\n📅 Today: `{today}`")

    @bot.on(events.NewMessage(pattern=r"^/prompt (.+)"))
    async def cmd_prompt(event):
        if not _is_owner(event): return
        p = event.pattern_match.group(1)
        await set_setting("prompt", p)
        await _reply(event, f"✅ Prompt updated!\n\n`{p}`")

    @bot.on(events.NewMessage(pattern=r"^/clearprompt$"))
    async def cmd_clearprompt(event):
        if not _is_owner(event): return
        await set_setting("prompt", None)
        await _reply(event, "✅ Prompt reset to default.")

    @bot.on(events.NewMessage(pattern=r"^/getprompt$"))
    async def cmd_getprompt(event):
        if not _is_owner(event): return
        p = await get_prompt()
        await _reply(event, f"📝 **Current Prompt:**\n\n{p or '_Default_'}")

    @bot.on(events.NewMessage(pattern=r"^/blacklist (\d+)$"))
    async def cmd_blacklist(event):
        if not _is_owner(event): return
        uid = int(event.pattern_match.group(1))
        await blacklist_user(uid)
        await _reply(event, f"🚫 `{uid}` blacklisted!")

    @bot.on(events.NewMessage(pattern=r"^/unblacklist (\d+)$"))
    async def cmd_unblacklist(event):
        if not _is_owner(event): return
        await unblacklist_user(int(event.pattern_match.group(1)))
        await _reply(event, "✅ Removed from blacklist!")

    @bot.on(events.NewMessage(pattern=r"^/whitelist (\d+)$"))
    async def cmd_whitelist(event):
        if not _is_owner(event): return
        await whitelist_user(int(event.pattern_match.group(1)))
        await _reply(event, "⭐ Whitelisted!")

    @bot.on(events.NewMessage(pattern=r"^/unwhitelist (\d+)$"))
    async def cmd_unwhitelist(event):
        if not _is_owner(event): return
        await unwhitelist_user(int(event.pattern_match.group(1)))
        await _reply(event, "❌ Removed from whitelist!")

    @bot.on(events.NewMessage(pattern=r"^/clearhistory (\d+)$"))
    async def cmd_clearhistory(event):
        if not _is_owner(event): return
        await clear_history(int(event.pattern_match.group(1)))
        await _reply(event, "🗑️ History cleared!")

    @bot.on(events.NewMessage(pattern=r"^/delay (\d+\.?\d*)$"))
    async def cmd_delay(event):
        if not _is_owner(event): return
        d = float(event.pattern_match.group(1))
        await set_setting("min_delay_override", d)
        await _reply(event, f"⏱️ Min delay: `{d}s`")

    # ── /model — Inline buttons ────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/model$"))
    async def cmd_model(event):
        if not _is_owner(event): return
        buttons = _build_model_buttons()
        if not buttons:
            await _reply(event,
                "❌ **No AI API keys found!**\n\n"
                "Set at least one in Render ENV:\n"
                "`GROQ_API_KEY` / `SAMBANOVA_API_KEY` / `NVIDIA_API_KEY`")
            return
        current     = await get_setting("preferred_model", "sambanova")
        available   = get_available_models()
        cur_label   = next((m["label"] for m in available if m["id"] == current), current)
        await _reply(event,
            f"🤖 **Select AI Model**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Current: **{cur_label}**\n\n"
            f"Tap a button to switch:\n"
            f"_(Only keys set in ENV are shown)_",
            buttons=buttons)

    @bot.on(events.CallbackQuery(pattern=rb"^model:(.+)$"))
    async def callback_model(event):
        if not _is_owner(event): return
        model_id  = event.data.decode().split("model:")[1]
        available = get_available_models()
        selected  = next((m for m in available if m["id"] == model_id), None)
        if not selected:
            await event.answer("❌ Model unavailable. Check API key.", alert=True)
            return
        await set_setting("preferred_model", model_id)
        await event.answer(f"✅ Switched!", alert=False)
        await event.edit(
            f"✅ **Model Updated!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 Now using: **{selected['label']}**\n\n"
            f"_Use /model to switch again._",
            parse_mode="markdown")

    @bot.on(events.NewMessage(pattern=r"^/dnd (.+)$"))
    async def cmd_dnd(event):
        if not _is_owner(event): return
        await set_dnd(event.pattern_match.group(1))
        await _reply(event, f"😴 DND ON: `{event.pattern_match.group(1)}`")

    @bot.on(events.NewMessage(pattern=r"^/dndoff$"))
    async def cmd_dndoff(event):
        if not _is_owner(event): return
        await set_dnd(None)
        await _reply(event, "✅ DND OFF")

    @bot.on(events.NewMessage(pattern=r"^/schedule (\d+) (morning|afternoon|night) (\d{1,2}:\d{2})$"))
    async def cmd_schedule(event):
        if not _is_owner(event): return
        uid   = int(event.pattern_match.group(1))
        stype = event.pattern_match.group(2)
        t     = event.pattern_match.group(3)
        await add_schedule(uid, stype, t)
        await _reply(event, f"📅 Scheduled **{stype}** for `{uid}` at `{t}`")

    @bot.on(events.NewMessage(pattern=r"^/unschedule (\d+) (morning|afternoon|night)$"))
    async def cmd_unschedule(event):
        if not _is_owner(event): return
        await remove_schedule(int(event.pattern_match.group(1)), event.pattern_match.group(2))
        await _reply(event, "❌ Schedule removed!")

    @bot.on(events.NewMessage(pattern=r"^/help$"))
    async def cmd_help(event):
        if not _is_owner(event): return
        await _reply(event,
            "🤖 **Userbot Commands**\n"
            "━━━━━━━━━━━━━━━━━\n"
            "🔑 `/login` — Login (phone → OTP → 2FA)\n"
            "🚪 `/logout` — Logout & delete session\n"
            "🔒 `/lock` · 🔓 `/unlock`\n"
            "📊 `/status` · 📈 `/stats`\n"
            "📝 `/prompt <text>` — Set AI persona\n"
            "🗑️ `/clearprompt` · 📋 `/getprompt`\n"
            "🚫 `/blacklist <id>` · ✅ `/unblacklist <id>`\n"
            "⭐ `/whitelist <id>` · ❌ `/unwhitelist <id>`\n"
            "🗑️ `/clearhistory <id>`\n"
            "⏱️ `/delay <sec>`\n"
            "🤖 `/model` — Switch AI model _(inline buttons)_\n"
            "😴 `/dnd HH:MM-HH:MM` · `/dndoff`\n"
            "📅 `/schedule <id> morning|afternoon|night HH:MM`\n"
            "❌ `/unschedule <id> morning|afternoon|night`\n"
            "━━━━━━━━━━━━━━━━━")
