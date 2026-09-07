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
import contextlib
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
from core import safety, voice
from core.personas import persona_choices
from core.rich import RichMessage, demo_message, send_rich
from core.safety import forget_limiter, limiter_for
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


def set_user_client(client: TelegramClient, owner: int) -> None:
    """Remember the running client for one owner."""
    _user_clients[owner] = client


def get_user_clients() -> dict[int, TelegramClient]:
    return _user_clients


def _registered(handler):
    """Any Telegram user may use the bot - each one gets their own space.

    The caller's id is the ``owner`` every command scopes its data to, so
    two people can never read or change each other's settings.
    """

    async def wrapper(event):
        owner = event.sender_id
        if await mongo.is_banned(owner):
            return
        sender = await event.get_sender()
        await mongo.register_user(
            owner,
            username=getattr(sender, "username", None),
            name=getattr(sender, "first_name", None),
        )
        return await handler(event)

    return wrapper


def _admin_only(handler):
    """Only the ids in OWNER_IDS - operator commands, not user commands."""

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
    @_registered
    async def cmd_start(event):
        session = await mongo.load_session(event.sender_id)
        signed_in = bool(session)
        state = (
            "🟢 Your account is connected"
            if signed_in
            else "🔴 No account connected yet — send /login"
        )
        message = (
            RichMessage()
            .bold("Serena · your AI auto-reply")
            .newline(2)
            .text_(state)
            .newline(2)
            .quote(
                "I answer your Telegram messages in your own voice while you "
                "are away, and I pace myself so your account stays safe."
            )
            .newline(2)
        )
        if not signed_in:
            message = (
                message.bold("How it works")
                .newline()
                .text_("1. ")
                .code("/login")
                .text_(" — connect your Telegram account\n")
                .text_("2. ")
                .code("/persona")
                .text_(" — pick Professional, Casual or Romantic\n")
                .text_("3. Go about your day. I reply for you.\n")
                .newline()
                .quote(
                    "Please read before connecting: automating a user account "
                    "is against Telegram's Terms of Service. This is meant for "
                    "answering your own conversations, never for sending "
                    "unsolicited messages. Your session is encrypted, only you "
                    "can control it, and /deleteme erases everything. You use "
                    "it at your own risk.",
                    expandable=True,
                )
                .newline(2)
                .text_("Ready? Send ")
                .code("/login")
                .text_("   ·   everything else: ")
                .code("/help")
            )
        else:
            message = (
                message.text_("Next: ").code("/status").text_("   ·   full list: ").code("/help")
            )
        banner = START_IMAGE if START_IMAGE.exists() else None
        await send_rich(bot, event.chat_id, message, file=banner)

    @bot.on(events.NewMessage(pattern=r"^/status$"))
    @_registered
    async def cmd_status(event):
        owner = event.sender_id
        locked = await mongo.is_locked(owner)
        persona_key = await mongo.get_persona_key(owner)
        custom = await mongo.get_prompt(owner)
        model = await mongo.get_user_setting(owner, "preferred_model", "auto")
        dnd = await mongo.get_dnd(owner)
        total = await mongo.get_stat(owner, "total_replies")
        today = await mongo.get_today_count(owner)
        session = await mongo.load_session(owner)
        stats = limiter_for(owner).snapshot()

        label = ai.PROVIDERS[model].label if model in ai.PROVIDERS else "Automatic"
        await _say(
            event,
            "⚡ **Status**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Account: {'connected' if session else 'not connected'}\n"
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
    @_registered
    async def cmd_limits(event):
        s = limiter_for(event.sender_id).snapshot()
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
    @_registered
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
    @_registered
    async def cmd_persona(event):
        owner = event.sender_id
        current = await mongo.get_persona_key(owner)
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
        if await mongo.is_banned(event.sender_id):
            return
        key = event.data.decode().split(":", 1)[1]
        await mongo.set_persona_key(event.sender_id, key)
        await event.answer("Personality updated")
        await event.edit(
            f"✅ **Personality set to `{key}`.**\n\n"
            "_A custom /prompt, if set, still overrides this._",
            parse_mode="markdown",
        )

    # ── model ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/model$"))
    @_registered
    async def cmd_model(event):
        providers = ai.available_providers()
        if not providers:
            await _say(
                event,
                "❌ **No AI provider configured.**\n\n"
                "Set `GROQ_API_KEY`, `SAMBANOVA_API_KEY` or `NVIDIA_API_KEY`.",
            )
            return
        current = await mongo.get_user_setting(event.sender_id, "preferred_model", "auto")
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
        if await mongo.is_banned(event.sender_id):
            return
        key = event.data.decode().split(":", 1)[1]
        await mongo.set_user_setting(
            event.sender_id, "preferred_model", "" if key == "auto" else key
        )
        label = ai.PROVIDERS[key].label if key in ai.PROVIDERS else "Automatic"
        await event.answer("Model updated")
        await event.edit(f"✅ **Model:** {label}", parse_mode="markdown")

    # ── login ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/login$"))
    @_registered
    async def cmd_login(event):
        old = _pending.pop(event.sender_id, None)
        if old:
            with_suppressed(old.disconnect())
        await mongo.set_login_state(event.sender_id, {"step": "phone"})
        await _say(
            event,
            "📱 **Sign in — step 1 of 3**\n\n"
            "Send the phone number of the account that should auto-reply:\n"
            "`+919876543210` · `9876543210` · `09876543210`\n\n"
            "_Send /cancel to stop._",
        )

    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    @_registered
    async def cmd_cancel(event):
        client = _pending.pop(event.sender_id, None)
        if client:
            with_suppressed(client.disconnect())
        await mongo.set_login_state(event.sender_id, None)
        await _say(event, "✅ Cancelled.")

    @bot.on(events.NewMessage())
    async def login_steps(event):
        """Multi-step login. Only active while a login is in progress."""
        if await mongo.is_banned(event.sender_id):
            return
        if not event.text or event.text.startswith("/"):
            return
        state = await mongo.get_login_state(event.sender_id)
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
            await mongo.set_login_state(event.sender_id, None)
            await _say(event, f"⏳ **Too many attempts.** Try again in `{exc.seconds}`s.")
            return
        except Exception as exc:
            await client.disconnect()
            await mongo.set_login_state(event.sender_id, None)
            await _say(event, f"❌ Could not send the code: `{exc}`\n\nSend /login to retry.")
            return

        _pending[event.sender_id] = client
        await mongo.set_login_state(
            event.sender_id, {"step": "otp", "phone": phone, "hash": sent.phone_code_hash}
        )
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
            await mongo.set_login_state(event.sender_id, None)
            _pending.pop(event.sender_id, None)
            await _say(event, "⚠️ **The login expired.** Send /login to start again.")
            return

        try:
            await client.sign_in(state["phone"], code, phone_code_hash=state["hash"])
        except SessionPasswordNeededError:
            await mongo.set_login_state(
                event.sender_id, {"step": "password", "phone": state["phone"]}
            )
            await _say(
                event,
                "🔐 **Two-step verification — step 3 of 3**\n\n"
                "Send your Telegram cloud password. The message will be "
                "deleted immediately.",
            )
            return
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
            await mongo.set_login_state(event.sender_id, None)
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
            await mongo.set_login_state(event.sender_id, None)
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
            await mongo.set_login_state(event.sender_id, None)
            await _say(event, "⚠️ **The login expired.** Send /login to start again.")
            return
        try:
            await client.sign_in(password=password)
        except Exception as exc:
            await mongo.set_login_state(event.sender_id, None)
            _pending.pop(event.sender_id, None)
            with_suppressed(client.disconnect())
            await _say(event, f"❌ Wrong password: `{exc}`\n\nSend /login to try again.")
            return
        await _finish_login(event, client)

    async def _finish_login(event, client: TelegramClient) -> None:
        me = await client.get_me()
        await mongo.save_session(event.sender_id, client.session.save(), account_id=me.id)
        await mongo.set_login_state(event.sender_id, None)
        _pending.pop(event.sender_id, None)
        await _say(
            event,
            f"✅ **Signed in as {me.first_name}**\n"
            f"🆔 `{me.id}` · @{me.username or 'no username'}\n"
            "🔐 Session encrypted and saved.\n\n"
            "⏳ Starting the auto-reply…",
        )
        try:
            await _start_user_client(existing_client=client, me=me, owner=event.sender_id)
            await _say(event, "🚀 **Auto-reply is running.**")
        except Exception as exc:
            log.exception("could not start the userbot after login")
            await _say(event, f"⚠️ Signed in, but starting failed: `{exc}`")

    @bot.on(events.NewMessage(pattern=r"^/logout$"))
    @_registered
    async def cmd_logout(event):
        owner = event.sender_id
        client = _user_clients.pop(owner, None)
        if client is not None:
            try:
                await client.log_out()
            except Exception:
                log.warning("log_out failed for %s", owner)
        removed = await mongo.delete_session(owner)
        forget_limiter(owner)
        if not client and not removed:
            await _say(event, "👋 You had no account connected.")
            return
        await _say(event, "👋 **Signed out.** Your session has been deleted.")

    # ── behaviour ─────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/pause$"))
    @_registered
    async def cmd_pause(event):
        await mongo.set_user_setting(event.sender_id, "locked", True)
        await _say(event, "🔒 **Paused.** No auto-replies until /resume.")

    @bot.on(events.NewMessage(pattern=r"^/resume$"))
    @_registered
    async def cmd_resume(event):
        await mongo.set_user_setting(event.sender_id, "locked", False)
        await _say(event, "🔓 **Resumed.** Auto-replies are active.")

    @bot.on(events.NewMessage(pattern=r"^/prompt(?:\s+([\s\S]+))?$"))
    @_registered
    async def cmd_prompt(event):
        text = (event.pattern_match.group(1) or "").strip()
        if not text:
            current = await mongo.get_prompt(event.sender_id)
            await _say(
                event,
                f"📝 **Custom prompt**\n\n{current or '_none — using the persona_'}\n\n"
                "Set one with `/prompt <text>`, remove it with /clearprompt.",
            )
            return
        await mongo.set_user_setting(event.sender_id, "prompt", text)
        await _say(event, "✅ Custom prompt saved. It overrides the persona.")

    @bot.on(events.NewMessage(pattern=r"^/clearprompt$"))
    @_registered
    async def cmd_clearprompt(event):
        await mongo.set_user_setting(event.sender_id, "prompt", None)
        persona = await mongo.get_persona_key(event.sender_id)
        await _say(event, f"✅ Custom prompt removed. Back to the `{persona}` persona.")

    @bot.on(events.NewMessage(pattern=r"^/quiet\s+(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})$"))
    @_registered
    async def cmd_quiet(event):
        window = event.pattern_match.group(1).replace(" ", "")
        await mongo.set_dnd(event.sender_id, window)
        await _say(
            event,
            f"😴 **Quiet hours set:** `{window}` ({settings.timezone})\n"
            "No replies are sent in that window.",
        )

    @bot.on(events.NewMessage(pattern=r"^/quietoff$"))
    @_registered
    async def cmd_quietoff(event):
        await mongo.set_dnd(event.sender_id, None)
        await _say(event, "✅ Quiet hours off.")

    # ── people and groups ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/block\s+(\d+)$"))
    @_registered
    async def cmd_block(event):
        uid = int(event.pattern_match.group(1))
        await mongo.blacklist_user(event.sender_id, uid)
        await _say(event, f"🚫 `{uid}` will no longer get replies.")

    @bot.on(events.NewMessage(pattern=r"^/unblock\s+(\d+)$"))
    @_registered
    async def cmd_unblock(event):
        uid = int(event.pattern_match.group(1))
        await mongo.unblacklist_user(event.sender_id, uid)
        await _say(event, f"✅ `{uid}` can get replies again.")

    @bot.on(events.NewMessage(pattern=r"^/blocked$"))
    @_registered
    async def cmd_blocked(event):
        blocked = await mongo.list_blacklisted(event.sender_id)
        body = "\n".join(f"• `{u}`" for u in blocked) if blocked else "_nobody_"
        await _say(event, f"🚫 **Blocked ({len(blocked)})**\n{body}")

    @bot.on(events.NewMessage(pattern=r"^/allowgroup\s+(-?\d+)$"))
    @_registered
    async def cmd_allowgroup(event):
        gid = int(event.pattern_match.group(1))
        await mongo.allow_group(event.sender_id, gid)
        await _say(
            event,
            f"✅ **Group allowed:** `{gid}`\n"
            "Replies happen there only when the account is mentioned.",
        )

    @bot.on(events.NewMessage(pattern=r"^/disallowgroup\s+(-?\d+)$"))
    @_registered
    async def cmd_disallowgroup(event):
        gid = int(event.pattern_match.group(1))
        await mongo.disallow_group(event.sender_id, gid)
        await _say(event, f"❌ Group `{gid}` removed.")

    @bot.on(events.NewMessage(pattern=r"^/groups$"))
    @_registered
    async def cmd_groups(event):
        groups = await mongo.list_allowed_groups(event.sender_id)
        body = "\n".join(f"• `{g}`" for g in groups) if groups else "_none_"
        await _say(event, f"👥 **Allowed groups ({len(groups)})**\n{body}")

    # ── history ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/forget\s+(\d+)$"))
    @_registered
    async def cmd_forget(event):
        uid = int(event.pattern_match.group(1))
        removed = await mongo.clear_history(event.sender_id, uid)
        await _say(event, f"🗑 Cleared `{removed}` messages for `{uid}`.")

    @bot.on(events.NewMessage(pattern=r"^/forgetall$"))
    @_registered
    async def cmd_forgetall(event):
        removed = await mongo.clear_all_history(event.sender_id)
        await _say(event, f"🗑 Cleared `{removed}` messages across your chats.")

    # ── schedules ─────────────────────────────────────────────────────────
    @bot.on(
        events.NewMessage(
            pattern=r"^/schedule\s+(\d+)\s+(morning|afternoon|night)\s+(\d{1,2}:\d{2})$"
        )
    )
    @_registered
    async def cmd_schedule(event):
        uid = int(event.pattern_match.group(1))
        kind = event.pattern_match.group(2)
        when = event.pattern_match.group(3)
        await mongo.add_schedule(event.sender_id, uid, kind, when)
        await _say(
            event,
            f"📅 Daily **{kind}** message to `{uid}` at `{when}` ({settings.timezone}).",
        )

    @bot.on(events.NewMessage(pattern=r"^/unschedule\s+(\d+)\s+(morning|afternoon|night)$"))
    @_registered
    async def cmd_unschedule(event):
        uid = int(event.pattern_match.group(1))
        kind = event.pattern_match.group(2)
        await mongo.remove_schedule(event.sender_id, uid, kind)
        await _say(event, f"❌ Removed the {kind} message for `{uid}`.")

    @bot.on(events.NewMessage(pattern=r"^/schedules$"))
    @_registered
    async def cmd_schedules(event):
        rows = await mongo.get_active_schedules(event.sender_id)
        if not rows:
            await _say(event, "📅 **Scheduled messages:** _none_")
            return
        body = "\n".join(f"• `{r['target_id']}` — {r['type']} at `{r['time']}`" for r in rows)
        await _say(event, f"📅 **Scheduled messages ({len(rows)})**\n{body}")

    # ── help ──────────────────────────────────────────────────────────────
    # ── rich messages ─────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/rich(?:\s+(-?\d+))?$"))
    @_registered
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
        user_client = _user_clients.get(event.sender_id)
        if user_client is None:
            await _say(event, "🔴 Your account is not connected. Send /login first.")
            return
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

    # ── voice replies ─────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/voice(?:\s+([\s\S]+))?$"))
    @_registered
    async def cmd_voice(event):
        argument = (event.pattern_match.group(1) or "").strip().lower()
        state = await voice.settings_summary(event.sender_id)

        if not argument:
            await _say(
                event,
                "🎙 **Voice replies**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Status: {'🟢 on' if state['enabled'] else '🔴 off'}\n"
                f"Chance: `{int(state['chance'] * 100)}%` of eligible replies\n"
                f"Voice: `{state['voice']}`\n"
                f"Max length: `{state['max_chars']}` characters\n"
                f"Provider: {'ready' if state['available'] else 'unavailable'}\n\n"
                "`/voice on` · `/voice off`\n"
                "`/voice chance 25` — percentage of replies spoken\n"
                f"`/voice name <{' | '.join(ai.TTS_VOICES)}>`\n"
                "`/voice test <text>` — hear it right now\n\n"
                "_Only short, single-line replies are spoken, and never to "
                "strangers. Groq's Orpheus needs its model terms accepted "
                "once in the Groq console._",
            )
            return

        if argument in ("on", "off"):
            await mongo.set_user_setting(event.sender_id, "voice_replies", argument == "on")
            await _say(event, f"🎙 Voice replies are now **{argument}**.")
            return

        if argument.startswith("chance"):
            try:
                percent = int(argument.split()[1].rstrip("%"))
            except (IndexError, ValueError):
                await _say(event, "Use `/voice chance 25` — a percentage from 0 to 100.")
                return
            percent = max(0, min(100, percent))
            await mongo.set_user_setting(event.sender_id, "voice_chance", percent / 100)
            await _say(event, f"🎙 `{percent}%` of eligible replies will be spoken.")
            return

        if argument.startswith("name"):
            parts = argument.split()
            if len(parts) < 2 or parts[1] not in ai.TTS_VOICES:
                await _say(event, "Pick one of: `" + "` · `".join(ai.TTS_VOICES) + "`")
                return
            await mongo.set_user_setting(event.sender_id, "voice_name", parts[1])
            await _say(event, f"🎙 Voice set to `{parts[1]}`.")
            return

        if argument.startswith("test"):
            body = (event.pattern_match.group(1) or "")[4:].strip()
            body = body or "Hey, this is how I sound when I answer for you."
            note = await event.respond("🎙 Generating…")
            sent = await voice.send_as_voice(event.sender_id, bot, event.chat_id, body)
            await note.delete()
            if not sent:
                await _say(
                    event,
                    "❌ Could not generate speech.\n\n"
                    "Check that `GROQ_API_KEY` is set, that the text is under "
                    f"`{settings.voice_max_chars}` characters, and that you have "
                    "accepted the Orpheus model terms once at "
                    "console.groq.com/playground.",
                )
            return

        await _say(event, "Unknown option. Send `/voice` to see what it takes.")

    # ── stranger guardian ─────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/trust\s+(-?\d+)$"))
    @_registered
    async def cmd_trust(event):
        chat_id = int(event.pattern_match.group(1))
        limiter_for(event.sender_id).trust(chat_id)
        await _say(
            event,
            f"✅ `{chat_id}` is no longer treated as a stranger — "
            "the reply cap and the extra delay are lifted for it.",
        )

    # ── privacy ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/deleteme(?:\s+(CONFIRM))?$"))
    @_registered
    async def cmd_deleteme(event):
        """Erase everything belonging to this user."""
        if not event.pattern_match.group(1):
            await _say(
                event,
                "⚠️ **This deletes everything.**\n\n"
                "Your session, settings, schedules, blocked list and all stored "
                "conversation history — permanently, with no way back.\n\n"
                "Send `/deleteme CONFIRM` if you are sure.",
            )
            return

        owner = event.sender_id
        client = _user_clients.pop(owner, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.log_out()
        forget_limiter(owner)
        removed = await mongo.forget_user(owner)
        await _say(
            event,
            "🗑 **Everything has been deleted.**\n"
            + (
                "\n".join(f"· {name}: `{count}`" for name, count in removed.items())
                or "_nothing was stored_"
            )
            + "\n\nSend /start if you ever want to come back.",
        )

    # ── admin ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/users$"))
    @_admin_only
    async def cmd_users(event):
        users = await mongo.list_users()
        running = len(_user_clients)
        lines = []
        for row in users[-25:]:
            handle = f"@{row['username']}" if row.get("username") else row.get("name") or "?"
            mark = "🟢" if row["owner_id"] in _user_clients else "⚪"
            banned = " 🚫" if row.get("banned") else ""
            lines.append(f"{mark} `{row['owner_id']}` {handle}{banned}")
        await _say(
            event,
            f"👥 **Users ({len(users)})** · `{running}` connected\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            + ("\n".join(lines) or "_nobody yet_")
            + ("\n\n_showing the 25 most recent_" if len(users) > 25 else ""),
        )

    @bot.on(events.NewMessage(pattern=r"^/gstats$"))
    @_admin_only
    async def cmd_gstats(event):
        total = await mongo.total_replies_all_users()
        users = await mongo.count_users()
        overall = safety.aggregate_snapshot()
        await _say(
            event,
            "📊 **Across everybody**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Users: `{users}`\n"
            f"Accounts connected: `{len(_user_clients)}`\n"
            f"Replies (all time): `{total}`\n"
            f"Replies last hour: `{overall['replies_last_hour']}`\n"
            f"Replies last day: `{overall['replies_last_day']}`\n"
            f"Strangers answered: `{overall['strangers_answered']}`",
        )

    @bot.on(events.NewMessage(pattern=r"^/ban\s+(\d+)$"))
    @_admin_only
    async def cmd_ban(event):
        target = int(event.pattern_match.group(1))
        await mongo.set_banned(target, True)
        client = _user_clients.pop(target, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
        await _say(event, f"🚫 `{target}` can no longer use the bot.")

    @bot.on(events.NewMessage(pattern=r"^/unban\s+(\d+)$"))
    @_admin_only
    async def cmd_unban(event):
        target = int(event.pattern_match.group(1))
        await mongo.set_banned(target, False)
        await _say(event, f"✅ `{target}` may use the bot again.")

    @bot.on(events.NewMessage(pattern=r"^/broadcast\s+([\s\S]+)$"))
    @_admin_only
    async def cmd_broadcast(event):
        body = event.pattern_match.group(1).strip()
        users = await mongo.list_users()
        sent = failed = 0
        for row in users:
            if row.get("banned"):
                continue
            try:
                await bot.send_message(row["owner_id"], body, parse_mode="markdown")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # stay under Telegram's broadcast limits
        await _say(event, f"📣 Delivered to `{sent}`, failed `{failed}`.")

    @bot.on(events.NewMessage(pattern=r"^/help$"))
    @_registered
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
            "**Voice**\n"
            "`/voice` — on/off, chance, voice name, test\n\n"
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
            "**Privacy**\n"
            "`/deleteme` — erase your session, settings and history\n\n"
            "_Tip: forward a message to @userinfobot to find a user or group id._"
            + (
                "\n\n**Admin**\n"
                "`/users` · `/gstats` · `/broadcast <text>`\n"
                "`/ban <id>` · `/unban <id>`"
                if settings.is_owner(event.sender_id)
                else ""
            ),
        )


def with_suppressed(coro) -> None:
    """Fire a coroutine and ignore whatever it raises."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
