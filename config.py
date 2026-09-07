"""Typed, validated configuration.

Everything the bot needs comes from environment variables, so the same image
runs locally and on Render with no code change. Values are read once at
import and validated by :func:`Settings.validate`, which fails fast with a
readable list of problems instead of crashing later on a missing key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["ConfigError", "Settings", "settings"]


class ConfigError(RuntimeError):
    """Raised when the bot cannot possibly start with this configuration."""


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_ids(key: str) -> tuple[int, ...]:
    raw = _env(key).replace(";", ",")
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.append(int(chunk))
    return tuple(out)


@dataclass(slots=True)
class Settings:
    # -- Telegram ---------------------------------------------------------
    api_id: int = field(default_factory=lambda: _env_int("API_ID", 0))
    api_hash: str = field(default_factory=lambda: _env("API_HASH"))
    bot_token: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    #: Who may drive the control bot. Everyone else is ignored outright.
    owner_ids: tuple[int, ...] = field(default_factory=lambda: _env_ids("OWNER_IDS"))

    # -- Storage ----------------------------------------------------------
    mongo_uri: str = field(default_factory=lambda: _env("MONGO_URI") or _env("MONGODB_URI"))
    mongo_db: str = field(default_factory=lambda: _env("MONGO_DB", "userbot"))
    #: Fernet key protecting the stored Telegram session strings.
    encryption_key: str = field(default_factory=lambda: _env("ENCRYPTION_KEY"))

    # -- AI providers -----------------------------------------------------
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    sambanova_api_key: str = field(default_factory=lambda: _env("SAMBANOVA_API_KEY"))
    nvidia_api_key: str = field(default_factory=lambda: _env("NVIDIA_API_KEY"))
    ai_timeout: float = field(default_factory=lambda: _env_float("AI_TIMEOUT", 25.0))
    ai_max_tokens: int = field(default_factory=lambda: _env_int("AI_MAX_TOKENS", 300))
    ai_temperature: float = field(default_factory=lambda: _env_float("AI_TEMPERATURE", 0.85))

    # -- Behaviour --------------------------------------------------------
    default_persona: str = field(default_factory=lambda: _env("DEFAULT_PERSONA", "casual").lower())
    #: Local timezone for schedules, quiet hours and the daily counter.
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Asia/Kolkata"))
    history_limit: int = field(default_factory=lambda: _env_int("HISTORY_LIMIT", 20))
    dm_retention_days: int = field(default_factory=lambda: _env_int("DM_RETENTION_DAYS", 14))
    group_retention_hours: int = field(
        default_factory=lambda: _env_int("GROUP_RETENTION_HOURS", 24)
    )

    # -- Human-like timing ------------------------------------------------
    typing_speed: float = field(default_factory=lambda: _env_float("TYPING_SPEED", 0.04))
    max_typing_time: float = field(default_factory=lambda: _env_float("MAX_TYPING_TIME", 6.0))
    reactions_enabled: bool = field(default_factory=lambda: _env_bool("REACTIONS_ENABLED", True))

    # -- Safety rails -----------------------------------------------------
    #
    # This runs on a *real* Telegram account, and Telegram bans accounts that
    # behave like bots: instant replies, identical cadence, unlimited volume.
    # These defaults are deliberately conservative.
    #
    #: Minimum seconds between two replies in the same chat.
    per_chat_cooldown: float = field(default_factory=lambda: _env_float("PER_CHAT_COOLDOWN", 4.0))
    #: Maximum replies to one chat per hour.
    per_chat_hourly_limit: int = field(
        default_factory=lambda: _env_int("PER_CHAT_HOURLY_LIMIT", 30)
    )
    #: Maximum replies across all chats per hour.
    global_hourly_limit: int = field(default_factory=lambda: _env_int("GLOBAL_HOURLY_LIMIT", 120))
    #: Maximum replies across all chats per day.
    global_daily_limit: int = field(default_factory=lambda: _env_int("GLOBAL_DAILY_LIMIT", 500))
    #: Extra pause before the first-ever reply to an unknown chat, so a new
    #: conversation never starts with a suspiciously instant answer.
    new_chat_extra_delay: float = field(
        default_factory=lambda: _env_float("NEW_CHAT_EXTRA_DELAY", 3.0)
    )
    #: Ignore messages that arrive while a reply is already being composed.
    drop_while_replying: bool = field(
        default_factory=lambda: _env_bool("DROP_WHILE_REPLYING", True)
    )

    # -- Runtime ----------------------------------------------------------
    port: int = field(default_factory=lambda: _env_int("PORT", 8080))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    # -- Derived ----------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @property
    def ai_configured(self) -> bool:
        return bool(self.groq_api_key or self.sambanova_api_key or self.nvidia_api_key)

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    def validate(self) -> None:
        """Raise :class:`ConfigError` listing everything that is missing."""
        problems: list[str] = []
        if not self.api_id:
            problems.append("API_ID is not set (get it from my.telegram.org)")
        if not self.api_hash:
            problems.append("API_HASH is not set (get it from my.telegram.org)")
        if not self.bot_token:
            problems.append("BOT_TOKEN is not set (create one with @BotFather)")
        if not self.owner_ids:
            problems.append("OWNER_IDS is not set - nobody would be able to control the bot")
        if not self.mongo_uri:
            problems.append("MONGO_URI is not set (a free MongoDB Atlas tier is fine)")
        if not self.encryption_key:
            problems.append(
                "ENCRYPTION_KEY is not set - session strings would be stored in "
                "plain text. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        if not self.ai_configured:
            problems.append(
                "No AI provider key set - add GROQ_API_KEY, SAMBANOVA_API_KEY or NVIDIA_API_KEY"
            )
        if problems:
            raise ConfigError(
                "Configuration is incomplete:\n" + "\n".join(f"  - {p}" for p in problems)
            )

    def describe(self) -> dict[str, object]:
        """Non-secret summary, safe to log at startup."""
        return {
            "owners": len(self.owner_ids),
            "persona": self.default_persona,
            "timezone": self.timezone,
            "providers": [
                name
                for name, key in (
                    ("groq", self.groq_api_key),
                    ("sambanova", self.sambanova_api_key),
                    ("nvidia", self.nvidia_api_key),
                )
                if key
            ],
            "reactions": self.reactions_enabled,
            "limits": {
                "per_chat_hourly": self.per_chat_hourly_limit,
                "global_hourly": self.global_hourly_limit,
                "global_daily": self.global_daily_limit,
            },
        }


settings = Settings()
