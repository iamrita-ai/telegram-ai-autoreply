# 🤖 AI Userbot — Setup Guide

## 📋 Environment Variables (Render ENV)

| Variable | Description |
|----------|-------------|
| `API_ID` | From https://my.telegram.org |
| `API_HASH` | From https://my.telegram.org |
| `PHONE_NUMBER` | Your phone with country code e.g. `+923001234567` |
| `SESSION_STRING` | (Optional) Pre-generated StringSession |
| `MONGO_URI` | MongoDB connection string |
| `GROQ_API_KEY` | From https://console.groq.com |
| `SAMBANOVA_API_KEY` | From https://cloud.sambanova.ai |
| `LOG_CHANNEL_ID` | Your log channel ID (e.g. `-100xxxxxxxxxx`) |

---

## 🚀 First-Time Login

If `SESSION_STRING` is not set, the bot will ask for:
1. OTP sent to your Telegram
2. 2FA password (if enabled)

After login, session is **auto-saved to MongoDB** — no OTP needed on restart.

**To generate SESSION_STRING manually (recommended):**
```bash
pip install telethon
python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
API_ID = int(input('API_ID: '))
API_HASH = input('API_HASH: ')
with TelegramClient(StringSession(), API_ID, API_HASH) as c:
    print('SESSION_STRING:', c.session.save())
"
```
Paste the output string into Render ENV as `SESSION_STRING`.

---

## 🐳 Deploy on Render (Web Service)

1. Push code to GitHub
2. Create **Web Service** on Render
3. Set **Build Command:** `pip install -r requirements.txt`
4. Set **Start Command:** `python main.py`
5. OR use **Docker** (auto-detected from Dockerfile)
6. Add all ENV variables
7. Deploy!

---

## 📖 Commands (send from YOUR account)

| Command | Action |
|---------|--------|
| `/help` | Show all commands |
| `/lock` | Stop all auto-replies |
| `/unlock` | Resume auto-replies |
| `/status` | View bot status |
| `/stats` | View reply count |
| `/prompt <text>` | Set custom AI personality |
| `/clearprompt` | Reset to default prompt |
| `/getprompt` | View current prompt |
| `/blacklist <id>` | Block a user |
| `/unblacklist <id>` | Unblock a user |
| `/whitelist <id>` | Whitelist a user |
| `/clearhistory <id>` | Clear chat history with user |
| `/delay <seconds>` | Set reply delay |
| `/model groq\|sambanova` | Switch AI model |
| `/dnd HH:MM-HH:MM` | Enable Do Not Disturb |
| `/dndoff` | Disable DND |
| `/schedule <id> morning\|afternoon\|night HH:MM` | Schedule greeting |
| `/unschedule <id> morning\|afternoon\|night` | Remove scheduled greeting |
| `/logout` | Log out safely |

---

## ✨ Features

- 🤖 Replies using **Groq** (primary) → **SambaNova** (fallback)
- 💬 Remembers conversation history per user (MongoDB)
- 🌍 Replies in **same language** as user
- ⌨️ Realistic **typing animation** before each reply
- 😍 Auto **reactions** based on message sentiment
- 📝 All messages **logged** to your channel
- 📅 Scheduled **Good Morning / Afternoon / Night** per user
- 😴 **DND mode** with time range support
- 🔒 Lock/unlock with single command
- 🚫 Blacklist/whitelist support
- 🤫 **Ignores bots, media, empty messages**
- 🔐 **2FA** support on login
