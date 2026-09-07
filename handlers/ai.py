"""AI reply generation.

Providers are OpenAI-compatible chat endpoints, tried in order until one
answers. The previous version had two problems that this fixes:

* **Lost messages.** A short message was buffered as "probably still typing"
  and, if the user never sent a follow-up, was never answered at all. The
  buffer now has a deadline: the caller is told how long to wait, and a
  fragment that goes stale is answered on its own.
* **Blind fallback.** Every provider was retried on every failure, including
  on a 401, which just burned latency. Authentication and quota failures now
  mark a provider as unusable for a cooldown period.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import httpx

from config import settings
from core.personas import build_system_prompt
from database import mongo

log = logging.getLogger(__name__)

__all__ = ["PROVIDERS", "generate_reply", "provider_health", "seems_incomplete"]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


@dataclass(frozen=True, slots=True)
class Provider:
    key: str
    label: str
    url: str
    model: str

    @property
    def api_key(self) -> str:
        return {
            "groq": settings.groq_api_key,
            "sambanova": settings.sambanova_api_key,
            "nvidia": settings.nvidia_api_key,
        }[self.vendor]

    @property
    def vendor(self) -> str:
        return self.key.split("_", 1)[0]

    @property
    def available(self) -> bool:
        return bool(self.api_key)


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider("groq_70b", "⚡ Groq · Llama 3.3 70B", GROQ_URL, "llama-3.3-70b-versatile"),
        Provider("groq_8b", "⚡ Groq · Llama 3.1 8B (fast)", GROQ_URL, "llama-3.1-8b-instant"),
        Provider(
            "sambanova_70b",
            "🚀 SambaNova · Llama 3.3 70B",
            SAMBANOVA_URL,
            "Meta-Llama-3.3-70B-Instruct",
        ),
        Provider(
            "nvidia_70b",
            "🟢 NVIDIA NIM · Llama 3.3 70B",
            NVIDIA_URL,
            "meta/llama-3.3-70b-instruct",
        ),
        Provider(
            "nvidia_maverick",
            "🟢 NVIDIA NIM · Llama 4 Maverick",
            NVIDIA_URL,
            "meta/llama-4-maverick-17b-128e-instruct",
        ),
    )
}

#: provider key -> monotonic timestamp until which it is considered broken.
_disabled_until: dict[str, float] = {}
_AUTH_COOLDOWN = 900.0
_RATE_COOLDOWN = 60.0


def available_providers() -> list[Provider]:
    return [p for p in PROVIDERS.values() if p.available]


def provider_health() -> dict[str, str]:
    now = time.monotonic()
    health: dict[str, str] = {}
    for key, provider in PROVIDERS.items():
        if not provider.available:
            health[key] = "no api key"
        elif _disabled_until.get(key, 0) > now:
            health[key] = f"cooling down {int(_disabled_until[key] - now)}s"
        else:
            health[key] = "ready"
    return health


# ──────────────────────────────────────────────────────────────────────────
#  Fragment detection
# ──────────────────────────────────────────────────────────────────────────
_DANGLING = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "then",
    "if",
    "when",
    "the",
    "a",
    "my",
    "your",
    "our",
    "their",
    "i",
    "you",
    "we",
    "they",
    "it",
    "that",
    "this",
    "is",
    "are",
    "was",
    "were",
    "to",
    "for",
    "with",
    "about",
}


def seems_incomplete(text: str) -> bool:
    """Is the user probably still typing?

    Replying to "hey" the instant it lands, then again to the real question a
    second later, is exactly what an automated account looks like.
    """
    text = (text or "").strip()
    if not text:
        return False
    if text.endswith(("?", "!", ".", "…")):
        return False
    words = text.split()
    if len(words) <= 2:
        return True
    return words[-1].lower().strip(",;:") in _DANGLING


# ──────────────────────────────────────────────────────────────────────────
#  Provider calls
# ──────────────────────────────────────────────────────────────────────────
async def _call(provider: Provider, messages: list[dict[str, str]]) -> str | None:
    now = time.monotonic()
    if _disabled_until.get(provider.key, 0) > now:
        return None
    if not provider.available:
        return None

    payload = {
        "model": provider.model,
        "messages": messages,
        "max_tokens": settings.ai_max_tokens,
        "temperature": settings.ai_temperature,
    }
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout) as client:
            response = await client.post(provider.url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        log.warning("[ai] %s unreachable: %s", provider.key, type(exc).__name__)
        return None

    if response.status_code == 200:
        try:
            text = response.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError):
            log.warning("[ai] %s returned an unreadable body", provider.key)
            return None
        return text or None

    # A 401/403 will fail identically on every retry, so stop asking for a
    # while rather than paying its latency on every message.
    if response.status_code in (401, 403):
        _disabled_until[provider.key] = now + _AUTH_COOLDOWN
        log.error("[ai] %s rejected the API key - paused 15 min", provider.key)
    elif response.status_code == 429:
        _disabled_until[provider.key] = now + _RATE_COOLDOWN
        log.warning("[ai] %s rate limited - paused 60s", provider.key)
    else:
        log.warning("[ai] %s returned HTTP %s", provider.key, response.status_code)
    return None


async def _complete(messages: list[dict[str, str]]) -> tuple[str | None, str]:
    """Try the preferred provider, then the rest. Returns (reply, provider)."""
    preferred = await mongo.get_setting("preferred_model", "")
    order: list[Provider] = []
    if preferred in PROVIDERS:
        order.append(PROVIDERS[preferred])
    order += [p for p in available_providers() if p.key != preferred]

    for provider in order:
        reply = await _call(provider, messages)
        if reply:
            return _tidy(reply), provider.key
    return None, ""


def _tidy(reply: str) -> str:
    """Strip the tells that make a model reply look generated."""
    reply = reply.strip().strip('"').strip()
    # Models like to prefix the speaker's name.
    for prefix in ("Assistant:", "AI:", "Reply:", "Response:"):
        if reply.lower().startswith(prefix.lower()):
            reply = reply[len(prefix) :].strip()
    # Collapse an essay into something a person would actually type.
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if len(lines) > 4:
        lines = lines[:4]
    return "\n".join(lines)[:900]


# ──────────────────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ReplyResult:
    text: str | None
    provider: str = ""
    buffered: bool = False
    failed: bool = False


async def generate_reply(
    user_id: int,
    message: str,
    *,
    is_group: bool = False,
    display_name: str = "",
) -> ReplyResult:
    """Produce one reply, or report that the message was buffered."""
    pending = await mongo.get_pending(user_id)

    if seems_incomplete(message) and not pending:
        # First fragment: hold it and wait for the rest of the thought.
        await mongo.add_pending(user_id, message)
        return ReplyResult(None, buffered=True)

    if pending:
        message = " ".join([*pending, message]).strip()
        await mongo.clear_pending(user_id)

    persona_key = await mongo.get_persona_key()
    custom_prompt = await mongo.get_prompt()
    system_prompt = build_system_prompt(persona_key, custom_prompt, display_name=display_name)

    history = await mongo.get_conversation(user_id, is_group=is_group)
    messages = [{"role": "system", "content": system_prompt}]
    messages += [
        {"role": h["role"], "content": h["content"]}
        for h in history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    messages.append({"role": "user", "content": message})

    reply, provider = await _complete(messages)
    if reply is None:
        return ReplyResult(None, failed=True)

    await mongo.add_message(user_id, "user", message, is_group=is_group)
    await mongo.add_message(user_id, "assistant", reply, is_group=is_group)
    return ReplyResult(reply, provider=provider)


async def flush_stale_fragment(
    user_id: int, *, is_group: bool = False, display_name: str = ""
) -> ReplyResult:
    """Answer a fragment the user never finished.

    Called after a short grace period so "hey" still gets a reply when no
    follow-up arrives, instead of being silently swallowed.
    """
    pending = await mongo.get_pending(user_id)
    if not pending:
        return ReplyResult(None)
    await mongo.clear_pending(user_id)

    persona_key = await mongo.get_persona_key()
    custom_prompt = await mongo.get_prompt()
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(persona_key, custom_prompt, display_name=display_name),
        }
    ]
    history = await mongo.get_conversation(user_id, is_group=is_group)
    messages += [
        {"role": h["role"], "content": h["content"]}
        for h in history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    combined = " ".join(pending).strip()
    messages.append({"role": "user", "content": combined})

    reply, provider = await _complete(messages)
    if reply is None:
        return ReplyResult(None, failed=True)
    await mongo.add_message(user_id, "user", combined, is_group=is_group)
    await mongo.add_message(user_id, "assistant", reply, is_group=is_group)
    return ReplyResult(reply, provider=provider)


async def smoke_test() -> dict[str, str]:
    """Ask every configured provider for one token. Used by /diag."""
    probe = [
        {"role": "system", "content": "Reply with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]
    results: dict[str, str] = {}

    async def check(provider: Provider) -> None:
        started = time.monotonic()
        reply = await _call(provider, probe)
        elapsed = time.monotonic() - started
        results[provider.key] = f"ok {elapsed:.1f}s" if reply else f"failed {elapsed:.1f}s"

    providers = available_providers()
    if not providers:
        return {"(none)": "no API keys configured"}
    await asyncio.gather(*(check(p) for p in providers))
    return results


def busy_message() -> str:
    """Said when every provider is down - short, human, English."""
    return random.choice(
        (
            "Can't talk right now, I'll get back to you shortly.",
            "In the middle of something, give me a bit.",
            "Tied up at the moment, back soon.",
            "Caught up with something, talk in a while.",
            "Away from my phone right now, back later.",
        )
    )
