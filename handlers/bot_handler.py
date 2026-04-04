"""
bot_handler.py — Telegram Control Bot
Fixes:
  - OTP expire: reuse same TelegramClient across login steps (stored in-memory dict)
  - Phone: auto-add country code if missing (uses phonenumbers lib)
  - Sessions: per-user isolation — 2 accounts never conflict
  - Model: inline buttons, only shows keys that are set in ENV
"""
import re
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError

from config import API_ID, API_HASH, OWNER_IDS, get_available_models
from database.mongo import (
    save_session, load_session, delete_session, load_all_sessions,
    set_setting, get_setting, is_locked, get_prompt,
    get_stat, blacklist_user, unblacklist_user,
    whitelist_user, unwhitelist_user, clear_history, clear_all_history,
    set_dnd, get_dnd, add_schedule, remove_schedule,
    set_login_state, get_login_state,
    allow_group, disallow_group, list_allowed_groups,
)

# ── In-memory: keep TelegramClient alive across OTP steps ─────
# Key: sender_id  →  TelegramClient (not disconnected between steps)
_pending_clients: dict = {}

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

def _normalize_phone(raw: str) -> str:
    """
    Accept phone with or without country code.
    - +923001234567  → +923001234567  (already correct)
    - 923001234567   → +923001234567  (add +)
    - 9876543210     → +919876543210  (Indian number → international)
    - 09876543210    → +919876543210  (with leading 0)
    Falls back to adding '+' prefix if phonenumbers lib not available.
    """
    raw = raw.strip().replace(" ", "").replace("-", "")
    try:
        import phonenumbers
        # Try parsing as-is first
        try:
            p = phonenumbers.parse(raw if raw.startswith("+") else f"+{raw}")
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            pass
        # Try as Indian number (default region IN)
        try:
            p = phonenumbers.parse(raw, "IN")
            if phonenumbers.is_valid_number(p):
                return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            pass
    except ImportError:
        pass
    # Simple fallback: just ensure + prefix
    if not raw.startswith("+"):
        return f"+{raw}"
    return raw


def _build_model_buttons():
    available = get_available_models()
    if not available:
        return None
    return [[Button.inline(m["label"], data=f"model:{m['id']}")] for m in available]


def register_bot_handlers(bot: TelegramClient, start_user_client_fn):
    global _start_user_client_fn
    _start_user_client_fn = start_user_client_fn

    # ── /start ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def cmd_start(event):
        if not _is_owner(event): return
        sessions = await load_all_sessions()
        if sessions:
            accs = len(sessions)
            status = f"🟢 {accs} account(s) logged in"
        else:
            session = await load_session()
            status  = "🟢 Logged in" if session else "🔴 Not logged in — send /login"
        await _reply(event,
            f"🤖 **Userbot Control Panel**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n\n"
            f"Send /help for all commands.")

    # ── /login ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/login$"))
    async def cmd_login(event):
        if not _is_owner(event): return
        # Clean up any pending client for this user
        old = _pending_clients.pop(event.sender_id, None)
        if old:
            try: await old.disconnect()
            except Exception: pass
        await set_login_state({"step": "awaiting_phone"})
        await _reply(event,
            "📱 **Login — Step 1/3**\n\n"
            "Send your phone number:\n"
            "✅ With country code: `+923001234567`\n"
            "✅ Without country code: `03001234567`\n"
            "✅ International: `923001234567`")

    # ── Catch-all for login multi-step ─────────────────────────
    @bot.on(events.NewMessage())
    async def handle_login_input(event):
        if not _is_owner(event): return
        if not event.text: return
        if event.text.startswith("/"): return

        state = await get_login_state()
        if not state: return
        step = state.get("step")

        # ── Step 1: Phone ───────────────────────────────────────
        if step == "awaiting_phone":
            raw_phone = event.text.strip()
            phone     = _normalize_phone(raw_phone)

            await _reply(event, f"📞 Using number: `{phone}`\n⏳ Sending OTP...")

            # Create a NEW TelegramClient and keep it ALIVE (do NOT disconnect)
            tmp = TelegramClient(StringSession(), API_ID, API_HASH)
            try:
                await tmp.connect()
                result = await tmp.send_code_request(phone)
                # Store the live client so sign_in reuses same connection & session
                _pending_clients[event.sender_id] = tmp
                await set_login_state({
                    "step":            "awaiting_otp",
                    "phone":           phone,
                    "phone_code_hash": result.phone_code_hash,
                })
                await _reply(event,
                    f"✅ **OTP sent to** `{phone}`\n\n"
                    f"📩 **Step 2/3** — Send the OTP code\n"
                    f"_(Spaces OK — e.g. `5 7 2 0 0 2`)_\n\n"
                    f"⚠️ Enter OTP within **2 minutes** to avoid expiry")
            except FloodWaitError as e:
                await tmp.disconnect()
                await set_login_state(None)
                await _reply(event, f"⏳ **Flood wait!** Try again after `{e.seconds}` seconds.")
            except Exception as e:
                await tmp.disconnect()
                await set_login_state(None)
                await _reply(event, f"❌ Error sending OTP: `{e}`\n\nSend /login to retry.")

        # ── Step 2: OTP ─────────────────────────────────────────
        elif step == "awaiting_otp":
            otp   = _clean_otp(event.text.strip())
            phone = state.get("phone")

            if len(otp) < 5:
                await _reply(event, "❗ OTP too short. Try again (e.g. `572002`):")
                return

            # Reuse the SAME client that sent the code (avoids "code expired")
            tmp = _pending_clients.get(event.sender_id)
            if not tmp or not tmp.is_connected():
                # Client lost (restart/timeout) — tell user to re-login
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                await _reply(event,
                    "⚠️ **Session expired** (bot restarted or took too long).\n\n"
                    "Send /login to start fresh.")
                return

            try:
                await tmp.sign_in(phone, otp, phone_code_hash=state["phone_code_hash"])
                me   = await tmp.get_me()
                sess = tmp.session.save()
                # Save session per user_id — no conflict between accounts
                await save_session(sess, user_id=me.id)
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                # Keep client connected — hand off to userbot
                await _reply(event,
                    f"✅ **Logged in as {me.first_name}!**\n"
                    f"🆔 ID: `{me.id}`\n"
                    f"📱 @{me.username or 'no username'}\n\n"
                    f"🔐 Session encrypted & saved!")
                await _boot_user_client(event, existing_client=tmp, me=me)

            except SessionPasswordNeededError:
                # Save partial session for 2FA step
                sess_so_far = tmp.session.save()
                # Keep client alive for 2FA
                await set_login_state({
                    "step":           "awaiting_2fa",
                    "phone":          phone,
                    "session_so_far": sess_so_far,
                })
                await _reply(event,
                    "🔐 **2FA Enabled — Step 3/3**\n\n"
                    "Send your **Telegram cloud password**:")

            except PhoneCodeInvalidError:
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                try: await tmp.disconnect()
                except Exception: pass
                await _reply(event, "❌ **Wrong OTP!** Send /login to start over.")

            except Exception as e:
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                try: await tmp.disconnect()
                except Exception: pass
                await _reply(event, f"❌ Error: `{e}`\n\nSend /login to retry.")

        # ── Step 3: 2FA ─────────────────────────────────────────
        elif step == "awaiting_2fa":
            password = event.text.strip()
            # Reuse same pending client
            tmp = _pending_clients.get(event.sender_id)
            if not tmp or not tmp.is_connected():
                # Rebuild from saved session
                sess_so_far = state.get("session_so_far", "")
                tmp = TelegramClient(StringSession(sess_so_far), API_ID, API_HASH)
                await tmp.connect()
                _pending_clients[event.sender_id] = tmp

            try:
                await tmp.sign_in(password=password)
                me   = await tmp.get_me()
                sess = tmp.session.save()
                await save_session(sess, user_id=me.id)
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                await _reply(event,
                    f"✅ **2FA verified! Logged in as {me.first_name}**\n"
                    f"🔐 Session encrypted & saved!")
                await _boot_user_client(event, existing_client=tmp, me=me)
            except Exception as e:
                await set_login_state(None)
                _pending_clients.pop(event.sender_id, None)
                try: await tmp.disconnect()
                except Exception: pass
                await _reply(event, f"❌ Wrong password: `{e}`\n\nSend /login again.")

    async def _boot_user_client(event, existing_client=None, me=None):
        try:
            await _reply(event, "⏳ Starting userbot...")
            await _start_user_client_fn(existing_client=existing_client, me=me)
            await _reply(event, "🚀 **Userbot is now running!**")
        except Exception as e:
            await _reply(event, f"⚠️ Start error: `{e}`")

    # ── /logout ────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/logout$"))
    async def cmd_logout(event):
        if not _is_owner(event): return
        uc = get_user_client()
        if uc:
            try: await uc.log_out()
            except Exception: pass
        await delete_session()
        await _reply(event, "👋 **Logged out.** Session deleted.")

    # ── /lock / /unlock ────────────────────────────────────────
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

    # ── /status ────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/status$"))
    async def cmd_status(event):
        if not _is_owner(event): return
        locked    = await is_locked()
        prompt    = await get_prompt()
        model     = await get_setting("preferred_model", "sambanova")
        dnd       = await get_dnd()
        total     = await get_stat("total_replies")
        today     = await get_stat("today_replies")
        sessions  = await load_all_sessions()
        n_accs    = len(sessions) if sessions else (1 if await load_session() else 0)
        available = get_available_models()
        cur_label = next((m["label"] for m in available if m["id"] == model), model)
        await _reply(event,
            f"⚡ **Status**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔑 **Accounts:** {n_accs} logged in\n"
            f"🔒 **Lock:** {'Locked 🔴' if locked else 'Active 🟢'}\n"
            f"🤖 **Model:** {cur_label}\n"
            f"💬 **Total Replies:** {total}\n"
            f"📅 **Today:** {today}\n"
            f"😴 **DND:** {dnd or 'Off'}\n"
            f"📝 **Prompt:** {'Custom ✅' if prompt else 'Default'}\n"
            f"━━━━━━━━━━━━━━━━━")

    # ── /stats ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/stats$"))
    async def cmd_stats(event):
        if not _is_owner(event): return
        total = await get_stat("total_replies")
        today = await get_stat("today_replies")
        await _reply(event, f"📊 **Stats**\n💬 Total: `{total}`\n📅 Today: `{today}`")

    # ── /prompt ────────────────────────────────────────────────
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

    # ── Blacklist / Whitelist ──────────────────────────────────
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

    # ── /delay ─────────────────────────────────────────────────
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
        current   = await get_setting("preferred_model", "sambanova")
        available = get_available_models()
        cur_label = next((m["label"] for m in available if m["id"] == current), current)
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
        await event.answer("✅ Switched!", alert=False)
        await event.edit(
            f"✅ **Model Updated!**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 Now using: **{selected['label']}**\n\n"
            f"_Use /model to switch again._",
            parse_mode="markdown")

    # ── /dnd ───────────────────────────────────────────────────
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

    # ── /schedule ──────────────────────────────────────────────
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

    # ── /allowgroup ────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/allowgroup (-?\d+)$"))
    async def cmd_allowgroup(event):
        if not _is_owner(event): return
        gid = int(event.pattern_match.group(1))
        await allow_group(gid)
        await _reply(event, f"✅ **Group Allowed!**\n🆔 `{gid}` added to whitelist.\nBot ab is group mein reply karega jab mention ho.")

    @bot.on(events.NewMessage(pattern=r"^/disallowgroup (-?\d+)$"))
    async def cmd_disallowgroup(event):
        if not _is_owner(event): return
        gid = int(event.pattern_match.group(1))
        await disallow_group(gid)
        await _reply(event, f"❌ **Group Removed!**\n🆔 `{gid}` whitelist se hata diya.")

    @bot.on(events.NewMessage(pattern=r"^/listgroups$"))
    async def cmd_listgroups(event):
        if not _is_owner(event): return
        groups = await list_allowed_groups()
        if not groups:
            await _reply(event, "📋 **Allowed Groups:** _Koi nahi_ — pehle /allowgroup use karo.")
            return
        lines = "\n".join(f"• `{g}`" for g in groups)
        await _reply(event, f"📋 **Allowed Groups ({len(groups)}):**\n{lines}")

    # ── /clearallhistory ────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/clearallhistory$"))
    async def cmd_clearallhistory(event):
        if not _is_owner(event): return
        await clear_all_history()
        await _reply(event, "🗑️ **Sabki history clear!** Sab users ki conversation wipe ho gayi.")

    # ── /help ──────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/help$"))
    async def cmd_help(event):
        if not _is_owner(event): return
        await _reply(event,
            "🤖 **Userbot — Full Command Guide**\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "**🔑 LOGIN / LOGOUT**\n"
            "`/login` — Account login karo\n"
            "  Step 1: Phone number bhejo\n"
            "  Example: `+919876543210` ya `9876543210`\n"
            "  Step 2: OTP bhejo jo Telegram ne bheja\n"
            "  Example: `5 7 2 0 0 2` ya `572002`\n"
            "  Step 3: 2FA password (agar laga hua ho)\n"
            "`/logout` — Session delete karo\n\n"
            "**🔒 LOCK / UNLOCK**\n"
            "`/lock` — Auto-reply band karo (bot sona chahta hai 😴)\n"
            "`/unlock` — Auto-reply chalu karo\n\n"
            "**📊 STATUS / STATS**\n"
            "`/status` — Sab settings ek jagah dekho\n"
            "`/stats` — Kitne replies hue aaj aur total\n\n"
            "**🤖 AI MODEL**\n"
            "`/model` — AI model change karo (buttons se)\n"
            "  Available: Groq, SambaNova, NVIDIA\n\n"
            "**📝 CUSTOM PROMPT**\n"
            "`/prompt <text>` — AI ka style/persona badlo\n"
            "  Example: `/prompt Tum Serena ho, Hinglish mein baat karo`\n"
            "`/clearprompt` — Default prompt wapas lao\n"
            "`/getprompt` — Current prompt dekho\n\n"
            "**🚫 BLACKLIST / WHITELIST**\n"
            "`/blacklist <user_id>` — Is user ko reply mat karo\n"
            "  Example: `/blacklist 123456789`\n"
            "`/unblacklist <user_id>` — Blacklist se hata do\n"
            "`/whitelist <user_id>` — VIP user (hamesha reply)\n"
            "`/unwhitelist <user_id>` — Whitelist se hata do\n"
            "`/clearhistory <user_id>` — Us user ki chat history clear karo\n\n"
            "**⏱️ DELAY**\n"
            "`/delay <seconds>` — Reply karne se pehle kitna rukna hai\n"
            "  Example: `/delay 2.5` → 2.5 second baad reply karega\n\n"
            "**😴 DND (Do Not Disturb)**\n"
            "`/dnd HH:MM-HH:MM` — Is time ke beech reply nahi karega\n"
            "  Example: `/dnd 23:00-07:00` → Raat 11 se subah 7 tak off\n"
            "`/dndoff` — DND hatao, wapas active\n\n"
            "**📅 SCHEDULED MESSAGES**\n"
            "`/schedule <user_id> morning|afternoon|night HH:MM`\n"
            "  Example: `/schedule 123456789 morning 08:00`\n"
            "  → Roz subah 8 baje us user ko good morning bhejega\n"
            "`/unschedule <user_id> morning|afternoon|night`\n"
            "  Example: `/unschedule 123456789 morning`\n\n"
            "**👥 GROUP CONTROL**\n"
            "`/allowgroup <group_id>` — Is group mein reply allow karo\n"
            "  Example: `/allowgroup -1001234567890`\n"
            "  Group ID pata karne ke liye: Group mein `/start` bhejo @userinfobot ko\n"
            "`/disallowgroup <group_id>` — Group hatao\n"
            "`/listgroups` — Saare allowed groups dekho\n\n"
            "**🗑️ HISTORY**\n"
            "`/clearhistory <user_id>` — Ek user ki history clear karo\n"
            "`/clearallhistory` — Sabki history ek saath wipe karo\n"
            "  Auto-cleanup: DM history 14 din baad, Group history 24 ghante baad delete\n\n"
            "**💡 TIPS**\n"
            "• User ID: @userinfobot pe message forward karo\n"
            "• India number: `+91` ya seedha `9876543210` likhna kaafi hai\n"
            "• Groups mein bot sirf tab reply karta hai jab @mention ho AND group allowed ho\n"
            "• Agar user 1-2 words bheje to bot wait karta hai pura message aane tak\n"
            "━━━━━━━━━━━━━━━━━")
