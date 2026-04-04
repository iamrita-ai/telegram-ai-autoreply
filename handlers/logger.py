import datetime
from config import LOG_CHANNEL_ID


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


async def log_message(client, event, reply_text: str, is_group: bool = False):
    """Send a formatted message log to the log channel."""
    if not LOG_CHANNEL_ID:
        return
    try:
        sender = await event.get_sender()
        chat   = await event.get_chat()

        name     = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
        username = f"@{sender.username}" if getattr(sender, "username", None) else "—"
        link     = f"[{name}](tg://user?id={sender.id})"

        if is_group:
            loc = f"👥 **Group:** {getattr(chat, 'title', 'Unknown')}"
        else:
            loc = "💬 **DM**"

        text = (
            f"📩 **Message Log**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 **From:** {link}\n"
            f"🆔 **ID:** `{sender.id}`\n"
            f"🔗 **Username:** {username}\n"
            f"{loc}\n"
            f"⏰ **Time:** `{_now()} UTC`\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📨 **Their message:**\n{event.text}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Bot replied:**\n{reply_text}"
        )
        await client.send_message(LOG_CHANNEL_ID, text, parse_mode="markdown")
    except Exception as e:
        print(f"[Logger] log_message error: {e}")


async def log_edited(client, event):
    """Log when someone edits a message."""
    if not LOG_CHANNEL_ID:
        return
    try:
        sender = await event.get_sender()
        name   = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
        text   = (
            f"✏️ **Edited Message**\n"
            f"👤 {name} (`{sender.id}`)\n"
            f"⏰ `{_now()} UTC`\n"
            f"📝 **New text:** {event.text}"
        )
        await client.send_message(LOG_CHANNEL_ID, text, parse_mode="markdown")
    except Exception as e:
        print(f"[Logger] log_edited error: {e}")


async def log_deleted(client, event):
    """Log deleted messages (if text was captured before deletion)."""
    if not LOG_CHANNEL_ID:
        return
    try:
        text = (
            f"🗑️ **Message Deleted**\n"
            f"⏰ `{_now()} UTC`\n"
            f"📝 **Content:** _(unavailable — Telegram doesn't expose deleted content)_"
        )
        await client.send_message(LOG_CHANNEL_ID, text, parse_mode="markdown")
    except Exception as e:
        print(f"[Logger] log_deleted error: {e}")


async def log_startup(client, me):
    """Notify log channel that the bot started."""
    if not LOG_CHANNEL_ID:
        return
    try:
        await client.send_message(
            LOG_CHANNEL_ID,
            f"✅ **Userbot Started**\n"
            f"👤 Logged in as: **{me.first_name}** (`{me.id}`)\n"
            f"⏰ `{_now()} UTC`",
            parse_mode="markdown",
        )
    except Exception as e:
        print(f"[Logger] log_startup error: {e}")
