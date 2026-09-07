"""The control bot: the owner's remote for the userbot.

Security notes, both of them real problems in the previous version:

* The login step deletes the owner's message containing the OTP and the 2FA
  password. They were previously left sitting in the chat history forever.
* ``/logout`` now signs out and deletes the session **that is actually in
  use**. It used to delete ``session_0`` no matter which account was live,
  so the account kept running after the owner thought it had stopped.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from pathlib import Path

from telethon import Button, TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from config import settings
from core.personas import persona_choices
from core.rich import RichMessage, demo_message, send_rich
from core.safety import limiter
from database import mongo
from handlers import ai

log = logging.getLogger(__name__)

__all__ = ["register", "set_user_client"]

#: Welcome banner shown by /start.
START_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "start.jpg"

#: Live clients mid-login, keyed by owner id. Kept connected between steps so
#: the OTP is signed in on the same connection that requested it - otherwise
#: Telegram reports the code as expired.
_pending: dict[int, TelegramClient] = {}

_user_clients: dict[int, TelegramClient] = {}
_start_user_client = None


def set_user_client(client: TelegramClient, user_id: int) -> None:
    _user_clients[user_id] = client


def get_user_clients() -> dict[int, TelegramClient]:
    return _user_clients


def _owner_only(handler):
    async def wrapper(event):
        if not settings.is_owner(event.sender_id):
            return
        return await handler(event)

    return wrapper


async def _say(event, text: str, buttons=None) -> None:
    await event.respond(text, parse_mode="markdown", buttons=buttons, link_preview=False)


def _normalise_phone(raw: str) -> str:
    """Accept ``+919876543210``, ``919876543210``, ``09876543210``, ``9876543210``."""
    raw = re.sub(r"[\s\-()]", "", raw.strip())
    try:
        import phonenumbers

        for candidate, region in ((raw if raw.startswith("+") else f"+{raw}", None), (raw, "IN")):
            try:
                parsed = phonenumbers.parse(candidate, region)
            except phonenumbers.NumberParseException:
                continue
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except ImportError:  # pragma: no cover - phonenumbers is a dependency
        pass
    return raw if raw.startswith("+") else f"+{raw}"


async def _delete_secret(event) -> None:
    """Remove a message containing an OTP or password from the chat."""
    try:
        await event.delete()
    except Exception:
        log.debug("could not delete the sensitive message")


def register(bot: TelegramClient, start_user_client) -> None:
    global _start_user_client
    _start_user_client = start_user_client

    # ── status ────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/start$"))
    @_owner_only
    async def cmd_start(event):
        sessions = await mongo.load_all_sessions()
        signed_in = bool(sessions)
        state = (
            f"🟢 {len(sessions)} account(s) signed in"
            if signed_in
            else "🔴 No account signed in — send /login"
        )
        message = (
            RichMessage()
            .bold("Serena · Auto-Reply Control Panel")
            .newline(2)
            .text_(state)
            .newline(2)
            .quote(
                "I answer your Telegram messages in your own voice while you "
                "are away, and I pace myself so the account stays safe.",
            )
            .newline(2)
            .text_("Next: ")
            .code("/login" if not signed_in else "/status")
            .text_("   ·   full list: ")
            .code("/help")
        )
        banner = START_IMAGE if START_IMAGE.exists() else None
        await send_rich(bot, event.chat_id, message, file=banner)

    @bot.on(events.NewMessage(pattern=r"^/status$"))
    @_owner_only
    async def cmd_status(event):
        locked = await mongo.is_locked()
        persona_key = await mongo.get_persona_key()
        custom = await mongo.get_prompt()
        model = await mongo.get_setting("preferred_model", "auto")
        dnd = await mongo.get_dnd()
        total = await mongo.get_stat("total_replies")
        today = await mongo.get_today_count()
        sessions = await mongo.load_all_sessions()
        stats = limiter.snapshot()

        label = ai.PROVIDERS[model].label if model in ai.PROVIDERS else "Automatic"
        await _say(
            event,
            "⚡ **Status**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Accounts: `{len(sessions)}`\n"
            f"🔒 Replies: {'🔴 paused' if locked else '🟢 active'}\n"
            f"🎭 Persona: `{persona_key}`{' (custom prompt)' if custom else ''}\n"
            f"🤖 Model: {label}\n"
            f"😴 Quiet hours: `{dnd or 'off'}`\n"
            f"🕒 Timezone: `{settings.timezone_effective}` "
            f"(now {dt.datetime.now(settings.tz).strftime('%H:%M')})\n"
            f"💬 Replies today: `{today}` · total `{total}`\n"
            f"📉 Last hour: `{stats['replies_last_hour']}`/"
            f"`{stats['limits']['global_hourly']}`\n"
            "━━━━━━━━━━━━━━━━━━━━",
        )

    @bot.on(events.NewMessage(pattern=r"^/limits$"))
    @_owner_only
    async def cmd_limits(event):
        s = limiter.snapshot()
        limits = s["limits"]
        await _say(
            event,
            "🛡 **Safety limits**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Replies last hour: `{s['replies_last_hour']}` / `{limits['global_hourly']}`\n"
            f"Replies today: `{s['replies_last_day']}` / `{limits['global_daily']}`\n"
            f"Chats active this hour: `{s['active_chats_this_hour']}`\n\n"
            f"Per chat: max `{limits['per_chat_hourly']}`/hour, "
            f"`{limits['per_chat_cooldown_s']}`s between replies\n"
            f"Account-wide gap: `{limits['global_min_gap_s']}`s minimum\n"
            f"Current burst penalty: `+{s['current_burst_penalty_s']}`s\n\n"
            "🛡 **Stranger guardian**\n"
            f"Unknown senders answered: `{s['strangers_answered']}`\n"
            f"Max replies per stranger: `{limits['stranger_max_replies']}`\n"
            f"Screening: `{'on' if settings.stranger_screening else 'off'}`\n\n"
            + (
                "**Recently blocked**\n"
                + "\n".join(f"· {r}" for r in s["recently_blocked"])
                + "\n\n"
                if s["recently_blocked"]
                else ""
            )
            + "_These protect the account from being flagged for automation. "
            "Change them with the env vars in `.env.example`._",
        )

    @bot.on(events.NewMessage(pattern=r"^/diag$"))
    @_owner_only
    async def cmd_diag(event):
        message = await event.respond("🩺 Testing AI providers…")
        results = await ai.smoke_test()
        lines = [f"`{key:<16}` {value}" for key, value in sorted(results.items())]
        health = ai.provider_health()
        paused = [k for k, v in health.items() if v.startswith("cooling")]
        await message.edit(
            "🩺 **Diagnostics**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**Providers**\n" + "\n".join(lines) + "\n\n"
            f"**Paused:** {', '.join(paused) if paused else 'none'}\n"
            f"**Accounts:** {len(_user_clients)} running",
            parse_mode="markdown",
        )

    # ── persona ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/persona$"))
    @_owner_only
    async def cmd_persona(event):
        current = await mongo.get_persona_key()
        buttons = [
            [
                Button.inline(
                    f"{p.label}{' ✅' if p.key == current else ''}", data=f"persona:{p.key}"
                )
            ]
            for p in persona_choices()
        ]
        body = "\n".join(f"{p.label} — {p.description}" for p in persona_choices())
        await _say(
            event,
            "🎭 **Reply personality**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{body}\n\n"
            f"Current: **{current}**\n"
            "_All personalities reply in English._",
            buttons=buttons,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^persona:(.+)$"))
    async def cb_persona(event):
        if not settings.is_owner(event.sender_id):
            return
        key = event.data.decode().split(":", 1)[1]
        await mongo.set_persona_key(key)
        await event.answer("Personality updated")
        await event.edit(
            f"✅ **Personality set to `{key}`.**\n\n"
            "_A custom /prompt, if set, still overrides this._",
            parse_mode="markdown",
        )

    # ── model ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/model$"))
    @_owner_only
    async def cmd_model(event):
        providers = ai.available_providers()
        if not providers:
            await _say(
                event,
                "❌ **No AI provider configured.**\n\n"
                "Set `GROQ_API_KEY`, `SAMBANOVA_API_KEY` or `NVIDIA_API_KEY`.",
            )
            return
        current = await mongo.get_setting("preferred_model", "auto")
        buttons = [
            [Button.inline(f"{p.label}{' ✅' if p.key == current else ''}", data=f"model:{p.key}")]
            for p in providers
        ]
        buttons.append([Button.inline("♻️ Automatic (fastest available)", data="model:auto")])
        await _say(
            event,
            "🤖 **AI model**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "The rest are used automatically if the chosen one fails.",
            buttons=buttons,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^model:(.+)$"))
    async def cb_model(event):
        if not settings.is_owner(event.sender_id):
            return
        key = event.data.decode().split(":", 1)[1]
        await mongo.set_setting("preferred_model", "" if key == "auto" else key)
        label = ai.PROVIDERS[key].label if key in ai.PROVIDERS else "Automatic"
        await event.answer("Model updated")
        await event.edit(f"✅ **Model:** {label}", parse_mode="markdown")

    # ── login ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/login$"))
    @_owner_only
    async def cmd_login(event):
        old = _pending.pop(event.sender_id, None)
        if old:
            with_suppressed(old.disconnect())
        await mongo.set_login_state({"step": "phone"})
        await _say(
            event,
            "📱 **Sign in — step 1 of 3**\n\n"
            "Send the phone number of the account that should auto-reply:\n"
            "`+919876543210` · `9876543210` · `09876543210`\n\n"
            "_Send /cancel to stop._",
        )

    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    @_owner_only
    async def cmd_cancel(event):
        client = _pending.pop(event.sender_id, None)
        if client:
            with_suppressed(client.disconnect())
        await mongo.set_login_state(None)
        await _say(event, "✅ Cancelled.")

    @bot.on(events.NewMessage())
    async def login_steps(event):
        """Multi-step login. Only active while a login is in progress."""
        if not settings.is_owner(event.sender_id):
            return
        if not event.text or event.text.startswith("/"):
            return
        state = await mongo.get_login_state()
        if not state:
            return

        step = state.get("step")
        if step == "phone":
            await _step_phone(event, state)
        elif step == "otp":
            await _step_otp(event, state)
        elif step == "password":
            await _step_password(event, state)

    async def _step_phone(event, state) -> None:
        phone = _normalise_phone(event.text)
        await _say(event, f"📞 Number: `{phone}`\n⏳ Requesting the code…")

        client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except FloodWaitError as exc:
            await client.disconnect()
            await mongo.set_login_state(None)
            await _say(event, f"⏳ **Too many attempts.** Try again in `{exc.seconds}`s.")
            return
        except Exception as exc:
            await client.disconnect()
            await mongo.set_login_state(None)
            await _say(event, f"❌ Could not send the code: `{exc}`\n\nSend /login to retry.")
            return

        _pending[event.sender_id] = client
        await mongo.set_login_state({"step": "otp", "phone": phone, "hash": sent.phone_code_hash})
        await _say(
            event,
            "✅ **Code sent — step 2 of 3**\n\n"
            "Send the login code from Telegram (spaces are fine).\n\n"
            "⚠️ Send it within 2 minutes. Your message will be deleted "
            "immediately for safety.",
        )

    async def _step_otp(event, state) -> None:
        code = re.sub(r"\D", "", event.text)
        await _delete_secret(event)
        if len(code) < 5:
            await _say(event, "❗ That code looks too short. Send it again:")
            return

        client = _pending.get(event.sender_id)
        if client is None or not client.is_connected():
            await mongo.set_login_state(None)
            _pending.pop(event.sender_id, None)
            await _say(event, "⚠️ **The login expired.** Send /login to start again.")
            return

        try:
            await client.sign_in(state["phone"], code, phone_code_hash=state["hash"])
        except SessionPasswordNeededError:
            await mongo.set_login_state({"step": "password", "phone": state["phone"]})
            await _say(
                event,
                "🔐 **Two-step verification — step 3 of 3**\n\n"
                "Send your Telegram cloud password. The message will be "
                "deleted immediately.",
            )
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
            await mongo.set_login_state(None)
            _pending.pop(event.sender_id, None)
            with_suppressed(client.disconnect())
            reason = (
                "that code was wrong"
                if isinstance(exc, PhoneCodeInvalidError)
                else "that code expired"
            )
            await _say(event, f"❌ Sign-in failed — {reason}. Send /login to retry.")
            return
        except Exception as exc:
            await mongo.set_login_state(None)
            _pending.pop(event.sender_id, None)
            with_suppressed(client.disconnect())
            await _say(event, f"❌ Sign-in failed: `{exc}`\n\nSend /login to retry.")
            return

        await _finish_login(event, client)

    async def _step_password(event, state) -> None:
        password = event.text
        await _delete_secret(event)
        client = _pending.get(event.sender_id)
        if client is None or not client.is_connected():
            await mongo.set_login_state(None)
            await _say(event, "⚠️ **The login expired.** Send /login to start again.")
            return
        try:
            await client.sign_in(password=password)
        except Exception as exc:
            await mongo.set_login_state(None)
            _pending.pop(event.sender_id, None)
            with_suppressed(client.disconnect())
            await _say(event, f"❌ Wrong password: `{exc}`\n\nSend /login to try again.")
            return
        await _finish_login(event, client)

    async def _finish_login(event, client: TelegramClient) -> None:
        me = await client.get_me()
        await mongo.save_session(client.session.save(), user_id=me.id)
        await mongo.set_login_state(None)
        _pending.pop(event.sender_id, None)
        await _say(
            event,
            f"✅ **Signed in as {me.first_name}**\n"
            f"🆔 `{me.id}` · @{me.username or 'no username'}\n"
            "🔐 Session encrypted and saved.\n\n"
            "⏳ Starting the auto-reply…",
        )
        try:
            await _start_user_client(existing_client=client, me=me)
            await _say(event, "🚀 **Auto-reply is running.**")
        except Exception as exc:
            log.exception("could not start the userbot after login")
            await _say(event, f"⚠️ Signed in, but starting failed: `{exc}`")

    @bot.on(events.NewMessage(pattern=r"^/logout$"))
    @_owner_only
    async def cmd_logout(event):
        if not _user_clients:
            await mongo.delete_session()
            await _say(event, "👋 No account was running. Stored sessions cleared.")
            return
        for user_id, client in list(_user_clients.items()):
            try:
                await client.log_out()
            except Exception:
                log.warning("log_out failed for %s", user_id)
            await mongo.delete_session(user_id)
            _user_clients.pop(user_id, None)
        await _say(event, "👋 **Signed out.** Sessions deleted.")

    # ── behaviour ─────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/pause$"))
    @_owner_only
    async def cmd_pause(event):
        await mongo.set_setting("locked", True)
        await _say(event, "🔒 **Paused.** No auto-replies until /resume.")

    @bot.on(events.NewMessage(pattern=r"^/resume$"))
    @_owner_only
    async def cmd_resume(event):
        await mongo.set_setting("locked", False)
        await _say(event, "🔓 **Resumed.** Auto-replies are active.")

    @bot.on(events.NewMessage(pattern=r"^/prompt(?:\s+([\s\S]+))?$"))
    @_owner_only
    async def cmd_prompt(event):
        text = (event.pattern_match.group(1) or "").strip()
        if not text:
            current = await mongo.get_prompt()
            await _say(
                event,
                f"📝 **Custom prompt**\n\n{current or '_none — using the persona_'}\n\n"
                "Set one with `/prompt <text>`, remove it with /clearprompt.",
            )
            return
        await mongo.set_setting("prompt", text)
        await _say(event, "✅ Custom prompt saved. It overrides the persona.")

    @bot.on(events.NewMessage(pattern=r"^/clearprompt$"))
    @_owner_only
    async def cmd_clearprompt(event):
        await mongo.set_setting("prompt", None)
        persona = await mongo.get_persona_key()
        await _say(event, f"✅ Custom prompt removed. Back to the `{persona}` persona.")

    @bot.on(events.NewMessage(pattern=r"^/quiet\s+(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})$"))
    @_owner_only
    async def cmd_quiet(event):
        window = event.pattern_match.group(1).replace(" ", "")
        await mongo.set_dnd(window)
        await _say(
            event,
            f"😴 **Quiet hours set:** `{window}` ({settings.timezone})\n"
            "No replies are sent in that window.",
        )

    @bot.on(events.NewMessage(pattern=r"^/quietoff$"))
    @_owner_only
    async def cmd_quietoff(event):
        await mongo.set_dnd(None)
        await _say(event, "✅ Quiet hours off.")

    # ── people and groups ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/block\s+(\d+)$"))
    @_owner_only
    async def cmd_block(event):
        uid = int(event.pattern_match.group(1))
        await mongo.blacklist_user(uid)
        await _say(event, f"🚫 `{uid}` will no longer get replies.")

    @bot.on(events.NewMessage(pattern=r"^/unblock\s+(\d+)$"))
    @_owner_only
    async def cmd_unblock(event):
        uid = int(event.pattern_match.group(1))
        await mongo.unblacklist_user(uid)
        await _say(event, f"✅ `{uid}` can get replies again.")

    @bot.on(events.NewMessage(pattern=r"^/blocked$"))
    @_owner_only
    async def cmd_blocked(event):
        blocked = await mongo.list_blacklisted()
        body = "\n".join(f"• `{u}`" for u in blocked) if blocked else "_nobody_"
        await _say(event, f"🚫 **Blocked ({len(blocked)})**\n{body}")

    @bot.on(events.NewMessage(pattern=r"^/allowgroup\s+(-?\d+)$"))
    @_owner_only
    async def cmd_allowgroup(event):
        gid = int(event.pattern_match.group(1))
        await mongo.allow_group(gid)
        await _say(
            event,
            f"✅ **Group allowed:** `{gid}`\n"
            "Replies happen there only when the account is mentioned.",
        )

    @bot.on(events.NewMessage(pattern=r"^/disallowgroup\s+(-?\d+)$"))
    @_owner_only
    async def cmd_disallowgroup(event):
        gid = int(event.pattern_match.group(1))
        await mongo.disallow_group(gid)
        await _say(event, f"❌ Group `{gid}` removed.")

    @bot.on(events.NewMessage(pattern=r"^/groups$"))
    @_owner_only
    async def cmd_groups(event):
        groups = await mongo.list_allowed_groups()
        body = "\n".join(f"• `{g}`" for g in groups) if groups else "_none_"
        await _say(event, f"👥 **Allowed groups ({len(groups)})**\n{body}")

    # ── history ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/forget\s+(\d+)$"))
    @_owner_only
    async def cmd_forget(event):
        uid = int(event.pattern_match.group(1))
        removed = await mongo.clear_history(uid)
        await _say(event, f"🗑 Cleared `{removed}` messages for `{uid}`.")

    @bot.on(events.NewMessage(pattern=r"^/forgetall$"))
    @_owner_only
    async def cmd_forgetall(event):
        removed = await mongo.clear_all_history()
        await _say(event, f"🗑 Cleared `{removed}` messages for every chat.")

    # ── schedules ─────────────────────────────────────────────────────────
    @bot.on(
        events.NewMessage(
            pattern=r"^/schedule\s+(\d+)\s+(morning|afternoon|night)\s+(\d{1,2}:\d{2})$"
        )
    )
    @_owner_only
    async def cmd_schedule(event):
        uid = int(event.pattern_match.group(1))
        kind = event.pattern_match.group(2)
        when = event.pattern_match.group(3)
        await mongo.add_schedule(uid, kind, when)
        await _say(
            event,
            f"📅 Daily **{kind}** message to `{uid}` at `{when}` ({settings.timezone}).",
        )

    @bot.on(events.NewMessage(pattern=r"^/unschedule\s+(\d+)\s+(morning|afternoon|night)$"))
    @_owner_only
    async def cmd_unschedule(event):
        uid = int(event.pattern_match.group(1))
        kind = event.pattern_match.group(2)
        await mongo.remove_schedule(uid, kind)
        await _say(event, f"❌ Removed the {kind} message for `{uid}`.")

    @bot.on(events.NewMessage(pattern=r"^/schedules$"))
    @_owner_only
    async def cmd_schedules(event):
        rows = await mongo.get_active_schedules()
        if not rows:
            await _say(event, "📅 **Scheduled messages:** _none_")
            return
        body = "\n".join(f"• `{r['user_id']}` — {r['type']} at `{r['time']}`" for r in rows)
        await _say(event, f"📅 **Scheduled messages ({len(rows)})**\n{body}")

    # ── help ──────────────────────────────────────────────────────────────
    # ── rich messages ─────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/rich(?:\s+(-?\d+))?$"))
    @_owner_only
    async def cmd_rich(event):
        """Show the rich-message sample, here or in a real chat."""
        target = event.pattern_match.group(1)
        if not target:
            await send_rich(bot, event.chat_id, demo_message())
            await _say(
                event,
                "☝️ That is the control bot sending it.\n\n"
                "To see it arrive from **your own account** — the way your "
                "contacts will see it — send `/rich <user id>`. "
                "Try your own id first: `/rich " + str(event.sender_id) + "`",
            )
            return

        chat_id = int(target)
        clients = get_user_clients()
        if not clients:
            await _say(event, "🔴 No account is signed in. Send /login first.")
            return
        user_client = next(iter(clients.values()))
        try:
            await send_rich(user_client, chat_id, demo_message())
        except Exception as exc:
            await _say(
                event,
                f"❌ Could not send to `{chat_id}`: `{type(exc).__name__}`\n\n"
                "The account needs to be able to message that id — try your "
                "own id, or someone you have talked to before.",
            )
            return
        await _say(event, f"✅ Sent the rich sample to `{chat_id}` from your account.")

    # ── stranger guardian ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/trust\s+(-?\d+)$"))
    @_owner_only
    async def cmd_trust(event):
        chat_id = int(event.pattern_match.group(1))
        limiter.trust(chat_id)
        await _say(
            event,
            f"✅ `{chat_id}` is no longer treated as a stranger — "
            "the reply cap and the extra delay are lifted for it.",
        )

    @bot.on(events.NewMessage(pattern=r"^/help$"))
    @_owner_only
    async def cmd_help(event):
        await _say(
            event,
            "🤖 **Command guide**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "**Account**\n"
            "`/login` — sign the auto-reply account in\n"
            "`/logout` — sign out and delete the session\n"
            "`/cancel` — abort a login in progress\n\n"
            "**Control**\n"
            "`/pause` · `/resume` — stop and start replying\n"
            "`/status` — everything at a glance\n"
            "`/limits` — how close you are to the safety caps\n"
            "`/diag` — test every AI provider right now\n\n"
            "**Personality**\n"
            "`/persona` — Professional · Casual · Romantic\n"
            "`/prompt <text>` — a custom personality that overrides it\n"
            "`/clearprompt` — go back to the persona\n"
            "`/model` — pick the AI provider\n\n"
            "**Quiet hours**\n"
            "`/quiet 23:00-07:00` — no replies in that window\n"
            "`/quietoff` — always available\n\n"
            "**People**\n"
            "`/block <id>` · `/unblock <id>` · `/blocked`\n"
            "`/trust <id>` — stop treating someone as a stranger\n\n"
            "**Rich messages**\n"
            "`/rich` — see the formatted sample here\n"
            "`/rich <id>` — send it from your own account\n\n"
            "**Groups** _(replies need a mention as well)_\n"
            "`/allowgroup <id>` · `/disallowgroup <id>` · `/groups`\n\n"
            "**Memory**\n"
            "`/forget <id>` — clear one chat's history\n"
            "`/forgetall` — clear everything\n\n"
            "**Scheduled messages**\n"
            "`/schedule <id> morning|afternoon|night HH:MM`\n"
            "`/unschedule <id> <kind>` · `/schedules`\n\n"
            "_Tip: forward a message to @userinfobot to find a user or group id._",
        )


def with_suppressed(coro) -> None:
    """Fire a coroutine and ignore whatever it raises."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
