from telethon import events
from config import OWNER_IDS
from database.mongo import (
    set_setting, get_setting, is_locked,
    get_prompt, blacklist_user, unblacklist_user,
    whitelist_user, unwhitelist_user,
    clear_history, get_stat, set_dnd, get_dnd,
    add_schedule, remove_schedule,
)


def _owner(event) -> bool:
    return event.sender_id in OWNER_IDS


def register_commands(client):

    # ── Lock / Unlock ─────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/lock$"))
    async def cmd_lock(event):
        if not _owner(event): return
        await set_setting("locked", True)
        await event.edit("🔒 **Bot Locked!** No auto-replies until /unlock.")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/unlock$"))
    async def cmd_unlock(event):
        if not _owner(event): return
        await set_setting("locked", False)
        await event.edit("🔓 **Bot Unlocked!** Auto-replies are active.")

    # ── Prompt ────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/prompt (.+)"))
    async def cmd_set_prompt(event):
        if not _owner(event): return
        prompt = event.pattern_match.group(1)
        await set_setting("prompt", prompt)
        await event.edit(f"✅ **Prompt updated!**\n\n`{prompt}`")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/clearprompt$"))
    async def cmd_clear_prompt(event):
        if not _owner(event): return
        await set_setting("prompt", None)
        await event.edit("✅ **Custom prompt removed.** Using default.")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/getprompt$"))
    async def cmd_get_prompt(event):
        if not _owner(event): return
        p = await get_prompt()
        await event.edit(f"📝 **Current Prompt:**\n\n{p or '_Default_'}")

    # ── Status ────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/status$"))
    async def cmd_status(event):
        if not _owner(event): return
        locked  = await is_locked()
        prompt  = await get_prompt()
        model   = await get_setting("preferred_model", "groq")
        dnd     = await get_dnd()
        total   = await get_stat("total_replies")
        today   = await get_stat("today_replies")
        await event.edit(
            f"⚡ **Userbot Status**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔒 **Lock:** {'Locked 🔴' if locked else 'Active 🟢'}\n"
            f"🤖 **Model:** {model.upper()}\n"
            f"💬 **Total Replies:** {total}\n"
            f"📅 **Today:** {today}\n"
            f"😴 **DND:** {dnd or 'Off'}\n"
            f"📝 **Prompt:** {'Custom ✅' if prompt else 'Default'}\n"
            f"━━━━━━━━━━━━━━━━━"
        )

    # ── Stats ─────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/stats$"))
    async def cmd_stats(event):
        if not _owner(event): return
        total = await get_stat("total_replies")
        today = await get_stat("today_replies")
        await event.edit(
            f"📊 **Stats**\n"
            f"💬 Total replies: `{total}`\n"
            f"📅 Today: `{today}`"
        )

    # ── Blacklist ─────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/blacklist (\d+)$"))
    async def cmd_blacklist(event):
        if not _owner(event): return
        uid = int(event.pattern_match.group(1))
        await blacklist_user(uid)
        await event.edit(f"🚫 User `{uid}` blacklisted!")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/unblacklist (\d+)$"))
    async def cmd_unblacklist(event):
        if not _owner(event): return
        uid = int(event.pattern_match.group(1))
        await unblacklist_user(uid)
        await event.edit(f"✅ User `{uid}` removed from blacklist!")

    # ── Whitelist ─────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/whitelist (\d+)$"))
    async def cmd_whitelist(event):
        if not _owner(event): return
        uid = int(event.pattern_match.group(1))
        await whitelist_user(uid)
        await event.edit(f"✅ User `{uid}` whitelisted!")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/unwhitelist (\d+)$"))
    async def cmd_unwhitelist(event):
        if not _owner(event): return
        uid = int(event.pattern_match.group(1))
        await unwhitelist_user(uid)
        await event.edit(f"❌ User `{uid}` removed from whitelist!")

    # ── Clear History ─────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/clearhistory(?: (\d+))?$"))
    async def cmd_clearhistory(event):
        if not _owner(event): return
        uid = event.pattern_match.group(1)
        if uid:
            await clear_history(int(uid))
            await event.edit(f"🗑️ History cleared for `{uid}`!")
        else:
            await event.edit("❗ Usage: `/clearhistory <user_id>`")

    # ── Delay ─────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/delay (\d+\.?\d*)$"))
    async def cmd_delay(event):
        if not _owner(event): return
        d = float(event.pattern_match.group(1))
        await set_setting("min_delay_override", d)
        await event.edit(f"⏱️ Min reply delay set to `{d}s`")

    # ── Model Switch ──────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/model (groq|sambanova)$"))
    async def cmd_model(event):
        if not _owner(event): return
        model = event.pattern_match.group(1)
        await set_setting("preferred_model", model)
        await event.edit(f"🔄 Preferred model set to **{model.upper()}**")

    # ── DND ───────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/dnd (.+)$"))
    async def cmd_dnd(event):
        if not _owner(event): return
        tr = event.pattern_match.group(1)  # e.g. "23:00-07:00"
        await set_dnd(tr)
        await event.edit(f"😴 **DND Mode ON:** `{tr}`")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/dndoff$"))
    async def cmd_dndoff(event):
        if not _owner(event): return
        await set_dnd(None)
        await event.edit("✅ **DND Mode OFF**")

    # ── Scheduled Greetings ───────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/schedule (\d+) (morning|afternoon|night) (\d{1,2}:\d{2})$"))
    async def cmd_schedule(event):
        if not _owner(event): return
        uid   = int(event.pattern_match.group(1))
        stype = event.pattern_match.group(2)
        time  = event.pattern_match.group(3)
        await add_schedule(uid, stype, time)
        await event.edit(f"📅 Scheduled **{stype}** for `{uid}` at `{time}`")

    @client.on(events.NewMessage(outgoing=True, pattern=r"^/unschedule (\d+) (morning|afternoon|night)$"))
    async def cmd_unschedule(event):
        if not _owner(event): return
        uid   = int(event.pattern_match.group(1))
        stype = event.pattern_match.group(2)
        await remove_schedule(uid, stype)
        await event.edit(f"❌ Removed **{stype}** schedule for `{uid}`")

    # ── Help ──────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/help$"))
    async def cmd_help(event):
        if not _owner(event): return
        await event.edit(
            "🤖 **Userbot Commands**\n"
            "━━━━━━━━━━━━━━━━━\n"
            "🔒 `/lock` — Stop all replies\n"
            "🔓 `/unlock` — Resume replies\n"
            "📝 `/prompt <text>` — Set custom AI prompt\n"
            "🗑️ `/clearprompt` — Reset to default prompt\n"
            "📋 `/getprompt` — View current prompt\n"
            "📊 `/status` — Bot status overview\n"
            "📈 `/stats` — Reply statistics\n"
            "🚫 `/blacklist <id>` — Block a user\n"
            "✅ `/unblacklist <id>` — Unblock a user\n"
            "⭐ `/whitelist <id>` — Whitelist a user\n"
            "❌ `/unwhitelist <id>` — Remove from whitelist\n"
            "🗑️ `/clearhistory <id>` — Clear chat history\n"
            "⏱️ `/delay <seconds>` — Set reply delay\n"
            "🤖 `/model groq|sambanova` — Switch AI model\n"
            "😴 `/dnd HH:MM-HH:MM` — Enable DND mode\n"
            "🌅 `/dndoff` — Disable DND\n"
            "📅 `/schedule <id> morning|afternoon|night HH:MM`\n"
            "❌ `/unschedule <id> morning|afternoon|night`\n"
            "🚪 `/logout` — Log out safely\n"
            "━━━━━━━━━━━━━━━━━"
        )

    # ── Logout ────────────────────────────────────────────────
    @client.on(events.NewMessage(outgoing=True, pattern=r"^/logout$"))
    async def cmd_logout(event):
        if not _owner(event): return
        await event.edit("👋 **Logging out...**")
        await client.log_out()
