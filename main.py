"""Entry point: starts the control bot, the userbot(s) and the health server."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import signal
import time
from typing import Any

import aiohttp
from aiohttp import web
from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest

from config import ConfigError, settings
from core import safety
from core.logging_setup import setup_logging
from database import mongo
from handlers import control, scheduler, userbot

log = logging.getLogger("autoreply")

_STARTED_AT = time.time()

#: The control bot, so background code can reach the owner.
_bot: TelegramClient | None = None
_tasks: set[asyncio.Task] = set()
_clients: list[TelegramClient] = []


def _spawn(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# ──────────────────────────────────────────────────────────────────────────
#  Health endpoint
# ──────────────────────────────────────────────────────────────────────────
def _health_payload() -> dict[str, Any]:
    warnings: list[str] = []
    if not settings.timezone_ok:
        # Surfaced here because it is otherwise invisible: the bot keeps
        # working, it just runs every schedule on the wrong clock.
        warnings.append(
            f"TIMEZONE={settings.timezone!r} did not resolve; schedules are running on UTC"
        )
    return {
        "status": "ok" if _clients else "degraded",
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "accounts_running": len(_clients),
        "timezone": {
            "configured": settings.timezone,
            "effective": settings.timezone_effective,
            "resolved": settings.timezone_ok,
            "local_time": dt.datetime.now(settings.tz).isoformat(timespec="seconds"),
        },
        "keepalive": dict(_keepalive_state),
        "warnings": warnings,
        "config": settings.describe(),
        "safety": safety.aggregate_snapshot(),
    }


async def _start_health_server() -> web.AppRunner:
    """Serve / and /healthz.

    Render needs a listening port, and uptime pingers need something cheap.
    The payload reports whether a user account is actually signed in, so a
    half-dead deploy is visible instead of answering a bare "OK".
    """
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        payload = _health_payload()
        status = 200 if payload["status"] == "ok" else 503
        return web.json_response(payload, status=status)

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app.router.add_get("/", root)
    app.router.add_get("/healthz", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.port).start()
    log.info("health endpoint listening on :%s", settings.port)
    return runner


# ──────────────────────────────────────────────────────────────────────────
#  Keep-alive
# ──────────────────────────────────────────────────────────────────────────
#: Last keepalive result, surfaced on /healthz so a silent failure is visible.
_keepalive_state: dict[str, Any] = {"enabled": False, "url": "", "last": None, "ok": None}


def keepalive_target(url: str) -> str:
    """Normalise a base URL into the exact endpoint to ping."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if url.endswith(("/healthz", "/health")):
        return url
    return url + "/healthz"


async def _keepalive_loop(url: str, interval: float) -> None:
    """Request our own public URL on a timer so the host does not idle us out.

    Render suspends a web service after ~15 minutes with no inbound HTTP
    request. Telegram traffic runs over an outbound socket and does not
    count, so an account could be mid-conversation and still be shut down -
    which is exactly what "shutting down / bye" in the logs was, three
    minutes after the last reply. One cheap self-request per interval keeps
    the instance resident.

    The request goes to the *public* URL on purpose: hitting 127.0.0.1 would
    never reach the platform's router and would not reset the idle timer.
    """
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    log.info("keep-alive: pinging %s every %.0fs", url, interval)
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                async with session.get(url) as response:
                    # 503 is the health endpoint saying "no account signed
                    # in" - the ping still did its job, which is to be an
                    # inbound request.
                    _keepalive_state.update(ok=True, last=time.time(), status=response.status)
                    log.debug("keep-alive ping -> %s", response.status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _keepalive_state.update(ok=False, last=time.time(), error=type(exc).__name__)
                log.warning("keep-alive ping failed: %s", exc)
    except asyncio.CancelledError:
        raise
    finally:
        with contextlib.suppress(Exception):
            await session.close()


def start_keepalive() -> None:
    """Start the keep-alive task, if a public URL is known."""
    if not settings.keepalive:
        log.info("keep-alive disabled by configuration")
        return
    url = keepalive_target(settings.keepalive_url)
    if not url:
        log.warning(
            "keep-alive is on but no public URL is known - set KEEPALIVE_URL to this "
            "service's address, or the host will suspend it after a quiet period"
        )
        return
    _keepalive_state.update(enabled=True, url=url)
    _spawn(_keepalive_loop(url, settings.keepalive_interval), name="keepalive")


# ──────────────────────────────────────────────────────────────────────────
#  Userbot startup
# ──────────────────────────────────────────────────────────────────────────
#: Auth failures that mean a stored session is dead for good. Anything else
#: (a network blip, a Telegram outage) must NOT cost the user their session.
_DEAD_SESSION_ERRORS = (
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)


async def _notify(owner: int, text: str) -> None:
    """Message one user through the control bot."""
    if _bot is None or not owner:
        return
    with contextlib.suppress(Exception):
        await _bot.send_message(owner, text)


async def _launch(client: TelegramClient, owner: int, me: Any = None) -> str:
    """Start one client. Returns "ok", "dead" or "retry".

    "dead" means the session will never work again and should be deleted;
    "retry" means something transient went wrong and the session is kept.
    """
    try:
        if not client.is_connected():
            await client.connect()
        authorised = await client.is_user_authorized()
    except _DEAD_SESSION_ERRORS as exc:
        log.warning("stored session is dead (%s)", type(exc).__name__)
        return "dead"
    except Exception as exc:
        log.warning("could not start a stored session (%s) - keeping it", type(exc).__name__)
        return "retry"

    if not authorised:
        log.warning("a stored session is no longer authorised")
        return "dead"

    me = me or await client.get_me()
    display_name = me.first_name or me.username or "me"

    # Stay invisible: an account that is suddenly online 24/7 is conspicuous.
    with contextlib.suppress(Exception):
        await client(UpdateStatusRequest(offline=True))

    userbot.attach(client, owner=owner, me_id=me.id, display_name=display_name)
    control.set_user_client(client, owner)
    _spawn(scheduler.run_scheduler(client, owner), name=f"scheduler:{owner}")
    _spawn(client.run_until_disconnected(), name=f"client:{owner}")
    _clients.append(client)

    log.info("auto-reply active for %s (@%s), owner %s", display_name, me.username or me.id, owner)
    return "ok"


async def start_user_client(
    existing_client: TelegramClient = None, me=None, owner: int = 0
) -> None:
    """Start one freshly signed-in client, or restore every stored session.

    A redeploy must never ask the owner to sign in again: the session lives
    in MongoDB, so it is decrypted and resumed here. Only a session that is
    provably unusable is deleted, and the owner is told when that happens so
    the bot is never silently doing nothing.
    """
    if existing_client is not None:
        await _launch(existing_client, owner, me)
        return

    sessions, broken = await mongo.load_session_records()

    for owner_id in broken:
        # Undecryptable: ENCRYPTION_KEY was rotated or the row is corrupt.
        # It can never be recovered, so clear it out and say so.
        await mongo.delete_session(owner_id)
        log.error(
            "session for %s could not be decrypted (ENCRYPTION_KEY changed?) - deleted",
            owner_id,
        )
        await _notify(
            owner_id,
            "🔑 A stored login could not be decrypted, so it has been removed.\n\n"
            "This happens when ENCRYPTION_KEY changes between deploys. Send "
            "/login to sign in again - and keep that key stable from now on, "
            "or every redeploy will log the account out.",
        )

    if not sessions:
        if not broken:
            log.info("no stored session yet - send /login to the control bot")
        return

    for record in sessions:
        owner_id = record.get("owner_id")
        try:
            client = TelegramClient(
                StringSession(record["session"]), settings.api_id, settings.api_hash
            )
        except ValueError:
            # A truncated or corrupted session string. Telethon raises here,
            # which would otherwise abort startup entirely and leave the
            # service crash-looping with no obvious cause.
            await mongo.delete_session(owner_id)
            log.error("session for %s is corrupt and could not be parsed - deleted", owner_id)
            await _notify(
                owner_id,
                "⚠️ A saved login was corrupted and has been removed. Send /login to sign in again.",
            )
            continue
        outcome = await _launch(client, owner_id)
        if outcome == "ok":
            continue
        with contextlib.suppress(Exception):
            await client.disconnect()
        if outcome == "dead":
            await mongo.delete_session(owner_id)
            log.error("session for %s is no longer valid - deleted", owner_id)
            await _notify(
                owner_id,
                "⚠️ The saved login for this account stopped working - it was "
                "signed out from Telegram, revoked, or the account was "
                "restricted.\n\nThe broken session has been deleted. "
                "Send /login to connect it again.",
            )
        else:
            log.warning("session for %s kept, will retry on next restart", owner_id)

    log.info("%d account(s) running", len(_clients))


# ──────────────────────────────────────────────────────────────────────────
#  Lifecycle
# ──────────────────────────────────────────────────────────────────────────
async def _shutdown(bot: TelegramClient, runner: web.AppRunner) -> None:
    log.info("shutting down")
    for task in list(_tasks):
        task.cancel()
    for client in _clients:
        with contextlib.suppress(Exception):
            await client.disconnect()
    with contextlib.suppress(Exception):
        await bot.disconnect()
    with contextlib.suppress(Exception):
        await runner.cleanup()
    await mongo.disconnect()
    log.info("bye")


async def main() -> None:
    setup_logging(settings.log_level)

    try:
        settings.validate()
    except ConfigError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc

    log.info("configuration: %s", json.dumps(settings.describe()))

    runner = await _start_health_server()
    await mongo.connect()

    global _bot
    bot = TelegramClient(
        StringSession(await mongo.get_setting("control_session", "")),
        settings.api_id,
        settings.api_hash,
    )
    await bot.start(bot_token=settings.bot_token)
    _bot = bot
    # Persist the control-bot session so a restart does not re-authenticate
    # (the old code wrote a bot_session file that Render throws away).
    await mongo.set_setting("control_session", bot.session.save())

    # Pre-multi-user data belongs to the first configured admin.
    if settings.owner_ids:
        await mongo.migrate_to_multi_user(settings.owner_ids[0])

    userbot.set_notifier(_notify)
    control.register(bot, start_user_client)
    # The command menu next to the message box, so nothing has to be typed
    # from memory. Never fatal: a menu is a convenience.
    with contextlib.suppress(Exception):
        await control.publish_commands(bot)
    me = await bot.get_me()
    log.info("control bot @%s ready", me.username)

    await start_user_client()
    _spawn(userbot.cleanup_loop(), name="history-cleanup")
    start_keepalive()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    log.info("all services ready")
    await stop.wait()
    await _shutdown(bot, runner)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
