<div align="center">

<!-- Animated Header Banner -->
<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=200&section=header&text=TG%20AI%20Userbot&fontSize=60&fontColor=fff&animation=twinkling&fontAlignY=35&desc=Smart%20Telegram%20Auto-Reply%20Bot&descAlignY=55&descSize=18" width="100%"/>

<!-- Badges Row 1 -->
<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Telethon-1.36.0-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white"/>
  <img src="https://img.shields.io/badge/MongoDB-Motor-47A248?style=for-the-badge&logo=mongodb&logoColor=white"/>
  <img src="https://img.shields.io/badge/Render-Deployed-46E3B7?style=for-the-badge&logo=render&logoColor=white"/>
</p>

<!-- Badges Row 2 -->
<p align="center">
  <img src="https://img.shields.io/badge/Groq-Llama%203.3%2070B-F55036?style=for-the-badge&logo=groq&logoColor=white"/>
  <img src="https://img.shields.io/badge/SambaNova-Llama%203.3%2070B-8A2BE2?style=for-the-badge&logoColor=white"/>
  <img src="https://img.shields.io/badge/NVIDIA%20NIM-Llama%204%20Maverick-76B900?style=for-the-badge&logo=nvidia&logoColor=white"/>
</p>

<!-- Badges Row 3 -->
<p align="center">
  <img src="https://img.shields.io/badge/License-Personal%20Use%20Only-red?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Anti--Spam-Protected-success?style=for-the-badge&logo=shield&logoColor=white"/>
  <img src="https://img.shields.io/badge/Status-Active-brightgreen?style=for-the-badge"/>
</p>

<br/>

<!-- Animated typing effect description -->
<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=18&pause=1000&color=00D9FF&center=true&vWidth=600&lines=AI-powered+Telegram+Auto-Reply+Bot;Supports+Groq+%2B+SambaNova+%2B+NVIDIA+NIM;Human-like+typing+simulation;Personal+use+only+%E2%80%94+no+spam" alt="Typing SVG"/>

</div>

---

## ⚠️ DISCLAIMER

> **This project is strictly for personal, educational, and non-commercial use only.**
>
> - ❌ This bot is **NOT** intended for spamming, mass messaging, or any form of unsolicited communication
> - ❌ It does **NOT** send messages to random users — it only auto-replies to people who message you first
> - ✅ Built for personal productivity: auto-reply when busy, scheduled greetings to close friends/family
> - ✅ All conversations are one-on-one, consensual, and user-initiated
>
> **The author takes no responsibility for misuse of this tool. Using this against Telegram's Terms of Service is solely the user's responsibility. Use responsibly.**

---

## ✨ Features

<div align="center">
<img src="https://raw.githubusercontent.com/Tarikul-Islam-Anik/Animated-Fluent-Emojis/master/Emojis/Travel%20and%20places/Glowing%20Star.png" width="25"/>
</div>

```
🤖  AI Auto-Reply       — Replies to DMs & mentions using real AI models
🧠  Multi-Model Support — Groq, SambaNova, NVIDIA NIM with 1-tap switching
💬  Context Memory      — Remembers last 10 messages per user
🔒  Session Encryption  — Telegram session stored encrypted in MongoDB
⌨️  Typing Simulation   — Human-like typing delays & reading time
😴  DND Mode            — Auto-sleep between custom hours
📅  Scheduled Greetings — AI-generated morning/afternoon/night messages
🚫  Blacklist/Whitelist — Block or prioritize specific users
📊  Stats Tracking      — Track total & daily replies
📝  Custom Persona      — Set your own AI personality prompt
🔄  Fallback Chain      — Auto-switches model if one fails
```

---

## 🤖 Supported AI Models

<div align="center">

| Provider | Model | Speed | Quality |
|:---:|:---:|:---:|:---:|
| ⚡ **Groq** | Llama 3.3 70B | Fastest | ⭐⭐⭐⭐⭐ |
| ⚡ **Groq** | Llama 3.1 8B | Ultra Fast | ⭐⭐⭐⭐ |
| 🚀 **SambaNova** | Llama 3.3 70B | Fast | ⭐⭐⭐⭐⭐ |
| 🟢 **NVIDIA NIM** | Llama 3.3 70B | Fast | ⭐⭐⭐⭐⭐ |
| 🟢 **NVIDIA NIM** | Llama 4 Maverick 🔥 | Fast | ⭐⭐⭐⭐⭐ |

</div>

> Switch models anytime with `/model` — shows **only the models whose API key you've set**.

---

## 🚀 Deployment (Render)

### Step 1 — Clone & Push to GitHub

```bash
git clone https://github.com/yourusername/tg-userbot
cd tg-userbot
git add .
git commit -m "init"
git push
```

### Step 2 — Create Render Web Service

1. Go to [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`

### Step 3 — Set Environment Variables

| Variable | Description | Required |
|:---|:---|:---:|
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) | ✅ |
| `API_HASH` | Telegram API Hash | ✅ |
| `BOT_TOKEN` | Control bot token from [@BotFather](https://t.me/BotFather) | ✅ |
| `MONGO_URI` | MongoDB connection string | ✅ |
| `ENCRYPTION_KEY` | Fernet key (generate below) | ✅ |
| `GROQ_API_KEY` | From [console.groq.com](https://console.groq.com) | ⚡ Optional |
| `SAMBANOVA_API_KEY` | From [cloud.sambanova.ai](https://cloud.sambanova.ai) | 🚀 Optional |
| `NVIDIA_API_KEY` | From [build.nvidia.com](https://build.nvidia.com) | 🟢 Optional |
| `LOG_CHANNEL_ID` | Telegram channel ID for logs | Optional |
| `PHONE_NUMBER` | Your Telegram phone number | Optional |

> **At least one AI API key is required** (Groq / SambaNova / NVIDIA)

### Generate Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Paste the output as `ENCRYPTION_KEY` in Render ENV.

---

## 🎮 Bot Commands

```
/login                              — Login with phone → OTP → 2FA
/logout                             — Logout & wipe session
/lock  /unlock                      — Pause / resume auto-replies
/status                             — Full status overview
/stats                              — Reply count stats
/model                              — Switch AI model (inline buttons)
/prompt <text>                      — Set custom AI personality
/clearprompt                        — Reset to default prompt
/getprompt                          — View current prompt
/blacklist <user_id>                — Block a user
/unblacklist <user_id>              — Unblock a user
/whitelist <user_id>                — Whitelist priority user
/unwhitelist <user_id>              — Remove from whitelist
/clearhistory <user_id>             — Clear conversation history
/delay <seconds>                    — Set reply delay (e.g. /delay 2.5)
/dnd HH:MM-HH:MM                    — Enable Do Not Disturb
/dndoff                             — Disable DND
/schedule <id> morning|afternoon|night HH:MM
/unschedule <id> morning|afternoon|night
/help                               — Show all commands
```

---

## 📁 Project Structure

```
tg-userbot/
├── main.py                  # Entry point
├── config.py                # All settings & API keys
├── requirements.txt         # Dependencies
├── render.yaml              # Render deploy config
├── .gitignore               # Git ignore (sessions, keys, etc.)
│
├── database/
│   ├── __init__.py
│   └── mongo.py             # MongoDB operations
│
├── handlers/
│   ├── __init__.py
│   ├── ai_handler.py        # AI reply logic + model routing
│   ├── bot_handler.py       # Telegram control bot commands
│   ├── command_handler.py   # Userbot outgoing commands
│   ├── logger.py            # Log channel messages
│   └── scheduler.py        # Scheduled AI greetings
│
└── utils/
    ├── __init__.py
    └── helpers.py           # Typing simulation, reactions, DND
```

---

## 🔒 Security Notes

- ✅ Telegram session is **AES-encrypted** (Fernet) before saving to MongoDB
- ✅ No API keys or sessions are ever committed to git (see `.gitignore`)
- ✅ Only users in `OWNER_IDS` can control the bot
- ✅ Bot ignores other bots, media messages, and group messages unless mentioned

---

## 👤 Author & Contact

<div align="center">

<img src="https://img.shields.io/badge/Telegram-@YourUsername-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" href="https://t.me/YourUsername"/>

> Built with ❤️ for personal productivity. Not affiliated with Telegram, Groq, SambaNova, or NVIDIA.

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=100&section=footer" width="100%"/>

</div>
