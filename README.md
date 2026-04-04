# 🤖 Telegram Userbot — Auto Reply with AI

A smart Telegram userbot that replies to your DMs and allowed groups using AI, with a deep romantic personality (Serena), conversation memory, and full owner control via a separate bot.

---

## ✨ Features

| Feature | Details |
|---|---|
| 🧠 AI Replies | Groq / SambaNova / NVIDIA — auto fallback chain |
| 💬 Conversation Memory | 14-day rolling history per user |
| 👥 Group Control | Only replies in groups you explicitly allow |
| 🧩 Incomplete Message Detection | Waits for full thought before replying |
| 🎭 Serena Persona | Romantic, poetic, emotionally deep AI personality |
| 🔄 Auto Cleanup | Old history auto-deleted (DM: 14d, Group: 24h) |
| 😴 DND Mode | Silent hours — no replies in set time window |
| 📅 Scheduled Messages | Morning / Afternoon / Night auto-greetings |
| 🔥 Big Reactions | Animated emoji reactions on every message |
| ⌨️ Typing Animation | Human-like chunked typing simulation |
| 🔒 Session Encrypted | MongoDB with Fernet encryption |
| 🌐 Offline Mode | Always appears offline even while running |

---

## 🚀 Setup

### 1. Environment Variables (Render / Railway / VPS)

```
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_control_bot_token
MONGO_URI=mongodb+srv://...
ENCRYPTION_KEY=your_fernet_key_here
SAMBANOVA_API_KEY=optional
GROQ_API_KEY=optional
NVIDIA_API_KEY=optional
```

> At least one AI API key is required. Get free keys:
> - Groq: https://console.groq.com
> - SambaNova: https://cloud.sambanova.ai
> - NVIDIA: https://build.nvidia.com

### 2. Generate Encryption Key

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### 3. Deploy on Render

- New Web Service → connect your GitHub repo
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Add all ENV vars in the Render dashboard

---

## 🎮 Bot Commands (Control Bot)

### 🔑 Login / Logout
```
/login       — Account login karo (phone → OTP → 2FA)
/logout      — Session delete karo
```

### 🔒 Lock / Unlock
```
/lock        — Auto-reply band karo
/unlock      — Auto-reply chalu karo
```

### 📊 Status
```
/status      — Sab settings ek jagah
/stats       — Total aur aaj ke replies
```

### 🤖 AI Model
```
/model       — Model switch karo (inline buttons)
```

### 📝 Prompt / Persona
```
/prompt <text>     — AI ka personality badlo
/clearprompt       — Default Serena persona wapas
/getprompt         — Current prompt dekho
```
**Example:**
```
/prompt Tum Serena ho, romantic aur poetic tarike se baat karo
```

### 🚫 Blacklist / Whitelist
```
/blacklist <user_id>       — User ko block karo
/unblacklist <user_id>     — Unblock karo
/whitelist <user_id>       — VIP user
/unwhitelist <user_id>     — VIP hatao
```
**User ID kaise pata kare:** @userinfobot pe koi bhi message forward karo

### 🗑️ History
```
/clearhistory <user_id>    — Ek user ki history wipe
/clearallhistory           — Sabki history ek saath clear
```
> Auto-cleanup: DM history 14 din baad, Group history 24 ghante baad automatically delete hoti hai

### 👥 Group Control
```
/allowgroup <group_id>     — Is group mein reply allow karo
/disallowgroup <group_id>  — Group remove karo
/listgroups                — Saare allowed groups dekho
```
**Group ID kaise pata kare:**
1. Group mein @userinfobot add karo
2. `/id` bhejo — group ID milegi (usually negative number like `-1001234567890`)

> ⚠️ Groups mein bot sirf tab reply karta hai jab:
> 1. Group allowed ho (`/allowgroup` se)
> 2. Bot ko @mention kiya gaya ho

### ⏱️ Delay
```
/delay 2.5       — Reply se pehle 2.5 second ruko
```

### 😴 DND (Do Not Disturb)
```
/dnd 23:00-07:00     — Raat 11 se subah 7 tak silent
/dndoff              — DND hatao
```

### 📅 Scheduled Messages
```
/schedule <user_id> morning 08:00      — Roz subah 8 baje good morning
/schedule <user_id> afternoon 14:00    — Dopahar 2 baje
/schedule <user_id> night 22:00        — Raat 10 baje good night
/unschedule <user_id> morning          — Schedule hatao
```

---

## 🎭 Serena — AI Personality

Default persona for `@TechnicalSerena` account:

- **Romantic & Poetic** — Novel-inspired language, deep emotional connection
- **Intelligent** — Thinks like the Professor from Money Heist
- **Adaptive** — Matches user's language (Hinglish / English) and tone
- **Short replies** — 1–3 lines, real chat feel, never long paragraphs
- **Never breaks character** — Always human, never admits being AI

---

## 🧠 Smart Features

### Incomplete Message Detection
If a user sends fragments like:
```
User: "tum"
User: "kha se hoo?"
```
Bot waits for the full thought before replying. Single words or dangling sentences are buffered (up to 10 minutes) and combined with the next message.

### 14-Day Rolling History
- Every user's conversation is remembered for 14 days
- Day 15 onwards, Day 1 messages start auto-deleting (rolling window)
- Users with no activity for 14 days → full history wiped
- Group history: only 24 hours retained

### Daily Rotating Busy Messages
When AI fails, bot sends a natural "unavailable" message. The pool rotates daily so the same excuse never repeats two days in a row.

---

## 📁 Project Structure

```
├── main.py                  — Entry point, event handlers
├── config.py                — All settings and constants
├── handlers/
│   ├── ai_handler.py        — AI API calls, Serena prompt, message buffering
│   ├── bot_handler.py       — Control bot commands
│   ├── logger.py            — Disabled (no log channel)
│   └── scheduler.py         — Scheduled morning/night messages
├── database/
│   └── mongo.py             — All DB operations
├── utils/
│   └── helpers.py           — Typing animation, reactions, DND
└── requirements.txt
```

---

## 📦 Requirements

```
telethon
motor
cryptography
httpx
phonenumbers
```
