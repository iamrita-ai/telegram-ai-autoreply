# 🤖 Telegram AI Auto-Reply

**An AI that answers your Telegram messages in your voice, while you're asleep, in a meeting, or just away from your phone.**

You stay signed in as yourself. A separate control bot is your remote: pause it, switch its personality, set quiet hours, and see exactly what it has been doing.

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%20|%203.12-blue">
  <img alt="Telethon" src="https://img.shields.io/badge/telethon-1.38+-2CA5E0">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## What it does

| | |
|---|---|
| 🎭 **Three personalities** | Professional, Casual or Romantic — switch instantly with `/persona`, or write your own with `/prompt` |
| 🇬🇧 **English throughout** | Every reply, command and message is in English |
| 🛡 **Account-safety rails** | Rate limits, cooldowns and human pacing, because Telegram bans accounts that behave like bots |
| 🧠 **Remembers the conversation** | 14 days per DM, 24 hours per group, kept apart so a group thread never leaks into a private one |
| ⌨️ **Types like a person** | Reads, pauses, types in bursts — no instant robotic answers |
| 👥 **Groups are opt-in** | Only allowed groups, and only when you're actually mentioned |
| 😴 **Quiet hours** | Silent between the hours you choose, in *your* timezone |
| 📅 **Scheduled messages** | Daily morning / afternoon / night greetings, freshly written each time |
| 🔁 **Four AI providers** | Groq, SambaNova and NVIDIA NIM, with automatic failover |
| 🔐 **Encrypted sessions** | Session strings are Fernet-encrypted at rest |

---

## How it works

```
                    ┌──────────────────┐
   you  ──/commands─▶│   Control bot    │   @YourControlBot
                    │  (bot token)     │   pause, persona, limits, login
                    └────────┬─────────┘
                             │ starts / configures
                    ┌────────▼─────────┐
 friends ──DM──────▶│  Your account    │──▶ AI provider (Groq / SambaNova / NVIDIA)
                    │  (user session)  │◀── reply, typed out at human speed
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │     MongoDB      │  history · settings · encrypted session
                    └──────────────────┘
```

Two Telegram identities, one process. The **control bot** never reads your DMs; the **user session** never takes commands from anyone.

---

## Setup

### 1. Collect five things

| Value | Where from |
|---|---|
| `API_ID`, `API_HASH` | [my.telegram.org](https://my.telegram.org) → API development tools |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` — this is the *control* bot |
| `OWNER_IDS` | Forward any message to [@userinfobot](https://t.me/userinfobot) |
| `MONGO_URI` | A free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster |
| An AI key | [Groq](https://console.groq.com) (free tier), [SambaNova](https://cloud.sambanova.ai), or [NVIDIA NIM](https://build.nvidia.com) |

Generate the encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Deploy

<details open>
<summary><b>Render (recommended)</b></summary>

1. Fork this repo.
2. **New → Web Service**, point it at your fork. `render.yaml` sets the rest up.
3. Add the environment variables from [`.env.example`](.env.example) in the dashboard.
4. Deploy, then open `https://your-app.onrender.com/healthz` — it should return JSON.

</details>

<details>
<summary><b>Docker</b></summary>

```bash
cp .env.example .env      # fill it in
docker build -t tg-autoreply .
docker run --env-file .env -p 8080:8080 tg-autoreply
```

</details>

<details>
<summary><b>Locally</b></summary>

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill it in
python main.py
```

</details>

### 3. Sign in

Message your control bot:

```
/login
```

It asks for the phone number of the account that should auto-reply, then the code Telegram sends you, then your 2FA password if you have one. **Your code and password messages are deleted automatically.**

That's it — the account starts replying.

---

## Who can use it

The bot is **public**: anyone can open it, connect their own Telegram account
and get their own auto-reply. Each person is completely separate — their own
persona, custom prompt, quiet hours, blocked list, groups, schedules, voice
settings, conversation history and rate limits. Nobody can see or change
anybody else's.

`OWNER_IDS` no longer gates access; those ids are the **operators**, and they
additionally get `/users`, `/gstats`, `/broadcast`, `/ban` and `/unban`.

Every user can erase themselves completely with `/deleteme`.

---

## Commands

All commands go to the **control bot**, and act only on your own account.

### Account
| Command | Description |
|---|---|
| `/login` | Sign the auto-reply account in |
| `/logout` | Sign out and delete the stored session |
| `/cancel` | Abort a login in progress |

### Control
| Command | Description |
|---|---|
| `/pause` · `/resume` | Stop and start replying |
| `/status` | Persona, model, quiet hours, replies today, hourly usage |
| `/limits` | How close you are to the safety caps |
| `/diag` | Test every AI provider right now, with timings |

### Personality
| Command | Description |
|---|---|
| `/persona` | Pick Professional, Casual or Romantic |
| `/prompt <text>` | Write your own personality; overrides the persona |
| `/clearprompt` | Go back to the chosen persona |
| `/model` | Pick an AI provider, or leave it automatic |

### Reach
| Command | Description |
|---|---|
| `/quiet 23:00-07:00` · `/quietoff` | Silent hours, in your timezone |
| `/block <id>` · `/unblock <id>` · `/blocked` | Never reply to someone |
| `/trust <id>` | Stop treating someone as a stranger (lifts the reply cap) |
| `/allowgroup <id>` · `/disallowgroup <id>` · `/groups` | Group allow-list |

### Voice
| Command | Description |
|---|---|
| `/voice` | Show the current voice settings |
| `/voice on` · `/voice off` | Turn spoken replies on and off |
| `/voice chance 25` | Percentage of eligible replies that get spoken |
| `/voice name troy` | Pick the Orpheus voice |
| `/voice test <text>` | Hear it immediately |

### Rich messages
| Command | Description |
|---|---|
| `/rich` | Send the formatted sample to yourself, from the control bot |
| `/rich <id>` | Send the same sample **from your own account** to that chat |

### Privacy
| Command | Description |
|---|---|
| `/deleteme` | Erase your session, settings, schedules and history |

### Admin _(only ids in `OWNER_IDS`)_
| Command | Description |
|---|---|
| `/users` | Who is using the bot, and who is connected |
| `/gstats` | Totals across everybody |
| `/broadcast <text>` | Message every user |
| `/ban <id>` · `/unban <id>` | Block someone from the bot |

### Memory & scheduling
| Command | Description |
|---|---|
| `/forget <id>` · `/forgetall` | Clear your conversation history |
| `/schedule <id> morning\|afternoon\|night HH:MM` | Daily greeting |
| `/unschedule <id> <kind>` · `/schedules` | Manage them |

---

## Rich messages

Replies are plain text on purpose — formatted small talk looks synthetic. But
the control bot, the `/start` screen and anything you send deliberately can use
Telegram's full formatting, built in [`core/rich.py`](core/rich.py):

**bold**, *italic*, underline, ~~strikethrough~~, `inline code`, syntax-highlighted
code blocks, real hyperlinks, tap-to-reveal **spoilers**, ordinary blockquotes and
**expandable** blockquotes that start collapsed.

Send `/rich` to see all of it in one message, or `/rich <user id>` to have it
arrive **from your own account**, exactly as a contact would receive it. There is
also an offline preview:

```bash
python scripts/render_rich_preview.py docs/rich-preview.html
```

Two details worth knowing if you extend it:

- Telethon's HTML parser has no tag for spoilers or expandable quotes. Those
  need raw `MessageEntity` objects, which is what `RichMessage` builds.
- Entity offsets are counted in **UTF-16 code units, not Python characters**.
  One emoji outside the basic plane shifts every entity after it by one. The
  builder counts correctly, so emoji are safe anywhere in the text.

---

## Voice replies

Off by default. When enabled, a share of short replies are sent as a Telegram
voice note spoken by Groq's `canopylabs/orpheus-v1-english`.

Worth knowing before you turn it on:

- Orpheus is a **text-to-speech** model, not a chat model — it is deliberately
  kept out of the reply fallback chain, where it would fail on every call.
- Groq's speech endpoint rejects input over **200 characters**, so only short,
  single-line replies are eligible. Everything else stays text.
- Telegram renders a true voice note only for OGG/Opus. If the endpoint returns
  WAV, the clip is sent as an audio file instead.
- Voice notes are **never** sent to strangers — audio is far more intrusive
  than text and is exactly what gets reported.
- You must accept the model terms once at
  [console.groq.com/playground](https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english),
  or the endpoint returns 401 and the bot quietly stays on text.

Any failure — no key, terms not accepted, rate limit, oversized text — falls
back to sending the reply as text, so voice can never cost you a message.

---

## The bot's own look

`assets/` holds the generated artwork:

| File | Use |
|---|---|
| `assets/profile.jpg` | Profile picture — set it with @BotFather → `/setuserpic` |
| `assets/start.jpg` | Banner sent with `/start` |

---

## Staying un-banned

Telegram restricts accounts that behave mechanically. This is the part most auto-reply projects ignore, so it is worth being explicit about what the bot does on your behalf:

| Layer | What it stops |
|---|---|
| **Human typing** | Reads, pauses, types at human speed, **scaled to the length of the reply** — never answers instantly |
| **Never answers a bot** | Two bots replying to each other at machine speed, which Telegram counts against both |
| **Per-chat cooldown** | Machine-gunning one conversation _(not applied to your contacts)_ |
| **Account-wide minimum gap** | A burst spread thinly over ten chats — Telegram judges the *account*, not the chat |
| **Burst damping** | Speeding up exactly when you should slow down: every reply in the last 10 minutes adds delay, up to 45s |
| **Volume caps** | Per chat, per hour, per day (`/limits` shows live usage) _(not applied to your contacts)_ |
| **New-chat pause** | Instant answers to someone who just messaged you for the first time |
| **Stranger guardian** | Unknown senders wait ~25s, and get at most **3** replies before the bot stops and pings you |
| **Stranger screening** | Auto-replying to scams, phishing, investment pitches, prize bait and link spam — a reply confirms your number is live |
| **Duplicate guard** | Sending the same (or nearly the same) text twice within 30 minutes — the single clearest spam signal |
| **Echo-loop guard** | Ping-pong with another bot or a stuck client repeating one message |
| **Quiet hours** | Replying at 4am, and everything is 2.5× slower near those hours |
| **One reply in flight** | A burst of incoming messages producing a burst of answers |
| **Group locks** | Groups you have not allowed, and messages that do not mention you |
| **FloodWait backoff** | Retrying into a limit Telegram has already announced |
| **Silence on failure** | A canned "I'm busy" line repeated across chats when the AI is down |

Anything the guardian blocks is reported to you in `/limits`, and the higher-risk
blocks (a screened stranger, a reply cap reached) ping you directly.

The defaults are conservative on purpose. Every one is tunable in [`.env.example`](.env.example).

**Every layer above is per account.** One user's traffic never counts against
another's limits, and two users talking to the same contact are tracked
separately.

### Your contacts are not rate limited

A volume cap exists so the account cannot spray messages at people who never
asked for them. Somebody saved in your own contact list is the opposite of
that, so contacts are exempt from the cooldown and from the per-chat, hourly
and daily caps, and they get a much shorter pacing gap (`CONTACT_MIN_GAP`,
2s) instead of the stranger-facing 8s. Set `CONTACT_UNLIMITED=false` to treat
everyone identically.

What still applies to contacts, because these protect the account rather than
limit it: the account-wide pacing gap, the duplicate guard, the echo-loop
guard, the one-reply-in-flight guard and FloodWait backoff.

### Never answers another bot

A bot answering a bot is an unbounded loop running at machine speed, and both
accounts get flagged for it. The check is deliberately paranoid: Telethon's
`bot` flag, deleted and support accounts, accounts Telegram has marked scam or
fake, channel posts, anonymous admins, messages sent through an inline bot,
Telegram's own service ids (777000 and friends), and a fallback on handles
ending in `bot` for anybody who is not in your contacts. Scheduled greetings
run the same check before sending. There is no setting to turn this off.

### Delay follows the length of the message

Reading, thinking and typing time are all computed from length, so the rhythm
matches what is actually being sent:

| Reply | Roughly |
|---|---|
| `ok` | ~2s |
| a normal sentence | ~4s |
| a full paragraph | ~15-20s |

A fixed delay is wrong in both directions — it makes short answers feel dead
and makes long ones look pre-written. Tune with `TYPING_SPEED`,
`MIN_TYPING_TIME`, `MAX_TYPING_TIME`, `READING_SPEED` and `THINKING_SPEED`.

> **Note:** automating a user account is against Telegram's Terms of Service. This project is for replying to your own conversations; the safety rails reduce risk but cannot eliminate it. Do not use it to send unsolicited messages.

---

## Configuration

Every setting is an environment variable, documented in [`.env.example`](.env.example). The ones worth knowing:

| Variable | Default | What it does |
|---|---|---|
| `DEFAULT_PERSONA` | `casual` | Personality on first start |
| `TIMEZONE` | `Asia/Kolkata` | Quiet hours, schedules, daily counter |
| `PER_CHAT_HOURLY_LIMIT` | `30` | Replies to one chat per hour |
| `GLOBAL_DAILY_LIMIT` | `500` | Replies across all chats per day |
| `GLOBAL_MIN_GAP` | `8.0` | Minimum seconds between any two outgoing messages |
| `CONTACT_UNLIMITED` | `true` | Exempt saved contacts from every volume limit |
| `CONTACT_MIN_GAP` | `2.0` | Pacing gap while talking to a contact |
| `TYPING_SPEED` | `0.045` | Seconds per character — the reply-length delay curve |
| `MAX_TYPING_TIME` | `18.0` | Ceiling on typing time for a very long reply |
| `KEEPALIVE` | `true` | Ping the service's own URL so the host does not suspend it |
| `KEEPALIVE_INTERVAL` | `600` | Seconds between keep-alive pings |
| `STRANGER_MAX_REPLIES` | `3` | Replies an unknown sender can pull out of the account |
| `STRANGER_SCREENING` | `true` | Refuse to auto-reply to scam and spam patterns |
| `DUPLICATE_WINDOW` | `1800` | Seconds a sent message is remembered, to avoid repeats |
| `HISTORY_LIMIT` | `20` | Turns of context sent to the model |
| `REACTIONS_ENABLED` | `true` | React to incoming DMs with an emoji |
| `LOG_LEVEL` | `INFO` | `DEBUG` for troubleshooting |

---

## Health and monitoring

```bash
curl https://your-app.onrender.com/healthz
```

```json
{
  "status": "ok",
  "uptime_s": 3184.2,
  "accounts_running": 1,
  "config": { "persona": "casual", "providers": ["groq", "sambanova"], "timezone": "Asia/Kolkata" },
  "safety": { "replies_last_hour": 7, "replies_last_day": 42 }
}
```

It returns **503** when no account is signed in, so an uptime monitor catches a half-dead deploy instead of a green tick on a bot that stopped replying hours ago.

### Staying awake

Free hosting plans suspend a web service that has received no **inbound HTTP
request** for about 15 minutes. Telegram traffic runs over an outbound socket
and does not count, so an idle-looking service can be shut down in the middle
of a conversation — in the logs that appears as `shutting down` / `bye` a few
minutes after the last reply, with no error.

The service therefore requests its own public URL every 10 minutes. On Render
this is automatic: `RENDER_EXTERNAL_URL` is injected by the platform. Anywhere
else, set `KEEPALIVE_URL` to the service's public address. The last ping is
reported in `/healthz`:

```json
"keepalive": { "enabled": true, "url": "https://your-app.onrender.com/healthz",
               "ok": true, "status": 200, "last": 1757251200.4 }
```

Note that this keeps the instance resident, so it consumes free instance hours
continuously. An external uptime monitor pointed at `/healthz` works just as
well if you would rather not self-ping.

---

## Project layout

```
main.py                 startup, health endpoint, graceful shutdown
config.py               typed settings, validated at boot
core/
├── personas.py         the three personalities + shared house rules
├── safety.py           rate limits, stranger guardian, duplicate guard
├── voice.py            optional voice-note replies (Orpheus TTS)
├── rich.py             formatted-message builder (UTF-16 safe entities)
├── humanize.py         typing rhythm, reactions, quiet hours
└── logging_setup.py    structured logging
handlers/
├── userbot.py          incoming message pipeline
├── control.py          the owner's command surface
├── ai.py               providers, failover, fragment buffering
└── scheduler.py        daily greetings
database/mongo.py       storage, encryption, retention, per-user scoping
assets/                 profile picture and /start banner
scripts/                offline rich-message preview renderer
tests/                  248 tests, no network required
```

---

## Development

```bash
pip install -r requirements.txt pytest pytest-asyncio ruff
python -m pytest -q      # 248 tests, all offline
ruff check . && ruff format --check .
```

`tests/test_call_signatures.py` walks the AST of every module and binds each
cross-module call against the real signature. Mocks accept any arguments, so
unit tests cannot catch a call site that was missed during a refactor - this
does.

CI runs the same three checks — lint, tests on 3.11 and 3.12, and a Docker build — on every push.

---

## Troubleshooting

<details>
<summary><b>"The login expired" during sign-in</b></summary>

Telegram invalidates a login code if it is used on a different connection than the one that requested it, and codes expire in about two minutes. Send `/login` again and paste the code promptly. If the service restarted mid-login, start over.
</details>

<details>
<summary><b>The bot never replies</b></summary>

Check `/status` first:
- `Replies: 🔴 paused` → send `/resume`
- Quiet hours covering the current time → `/quietoff`
- In a group: the group must be in `/groups` **and** you must be mentioned
- `/limits` at a cap → wait, or raise the cap
- `/diag` showing every provider failing → your API key is wrong or out of quota
</details>

<details>
<summary><b>The bot goes quiet after a few minutes, and the logs say "shutting down / bye"</b></summary>

That is the host suspending an idle web service, not a crash. Telegram traffic
does not count as activity — only inbound HTTP does. Keep-alive is on by
default and pings `RENDER_EXTERNAL_URL` every 10 minutes; check the
`keepalive` block in `/healthz`. If `enabled` is `false`, the platform did not
supply a public URL, so set `KEEPALIVE_URL` yourself.
</details>

<details>
<summary><b>Replies take too long, or come back too fast</b></summary>

Delay is computed from the length of the reply, plus safety pacing. `/limits`
shows the current burst penalty and whether a cap is being hit. Your saved
contacts skip the caps and the cooldown entirely; strangers deliberately wait
~25s. Tune the curve with `TYPING_SPEED` and `MAX_TYPING_TIME`.
</details>

<details>
<summary><b>Replies sound wrong</b></summary>

`/persona` switches the tone. For something specific, `/prompt` takes a free-text personality and overrides the persona entirely; `/clearprompt` reverts.
</details>

<details>
<summary><b>Scheduled messages arrive at the wrong time</b></summary>

Open `/healthz` and look at the `timezone` block, or send `/status`:

```json
"timezone": { "configured": "Asia/Kolkata", "effective": "Asia/Kolkata",
              "resolved": true, "local_time": "2026-09-07T18:40:00+05:30" }
```

If `resolved` is `false`, Python could not find the zone and everything is
running on UTC — an 08:00 schedule fires at 13:30 IST. Either the name is
misspelled (it must be an IANA name like `Asia/Kolkata`, not `IST`), or the
image is missing the timezone database. The Dockerfile installs `tzdata` and
it is pinned in `requirements.txt`; a stripped-down base image without both
will hit this.
</details>

<details>
<summary><b>Every AI provider fails in /diag</b></summary>

Providers retire models. Groq shut down its Llama chat models in August 2026,
and a request to a decommissioned model id returns `404 model_not_found` every
time. The bot now detects that and pauses the provider for six hours instead
of paying the round trip on every message — but the fix is to update the model
id in `handlers/ai.py` against the provider's current list.
</details>

<details>
<summary><b>"Fernet key must be 32 url-safe base64-encoded bytes"</b></summary>

`ENCRYPTION_KEY` is not a valid Fernet key. It must be exactly 44 characters
of url-safe base64, ending in `=`. Generate one:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The bot now reports this at startup alongside any other missing settings,
instead of crashing once the health server is already up.
</details>

<details>
<summary><b>It asks me to /login again after every deploy</b></summary>

The session is stored encrypted in MongoDB and restored on boot, so a redeploy
should never need a new login. If it does, `ENCRYPTION_KEY` is changing between
deploys — a session encrypted with the old key cannot be read with the new one.
Set it once in Render's Environment tab and leave it alone.

When a session genuinely cannot be recovered, the bot deletes the broken record
and messages you to sign in again, rather than silently starting with zero
accounts. Transient failures (network, Telegram outage) never delete anything.
</details>

<details>
<summary><b>Health check returns 503</b></summary>

No user account is signed in — the control bot is up but nothing is replying. Send `/login`.
</details>

---

## License

MIT — see [LICENSE](LICENSE).
