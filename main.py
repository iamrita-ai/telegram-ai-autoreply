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

from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest

from config import ConfigError, settings
from core.logging_setup import setup_logging
from core.safety import limiter
from database import mongo
from handlers import control, scheduler, userbot

log = logging.getLogger("autoreply")

_STARTED_AT = time.time()
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
        "warnings": warnings,
        "config": settings.describe(),
        "safety": limiter.snapshot(),
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
#  Userbot startup
# ──────────────────────────────────────────────────────────────────────────
async def _launch(client: TelegramClient, me: Any = None) -> bool:
    if not client.is_connected():
        await client.connect()
    if not await client.is_user_authorized():
        log.warning("a stored session is no longer authorised - skipping it")
        return False

    me = me or await client.get_me()
    display_name = me.first_name or me.username or "me"

    # Stay invisible: an account that is suddenly online 24/7 is conspicuous.
    with contextlib.suppress(Exception):
        await client(UpdateStatusRequest(offline=True))

    userbot.attach(client, me_id=me.id, display_name=display_name)
    control.set_user_client(client, me.id)
    _spawn(scheduler.run_scheduler(client), name=f"scheduler:{me.id}")
    _spawn(client.run_until_disconnected(), name=f"client:{me.id}")
    _clients.append(client)

    log.info("auto-reply active for %s (@%s)", display_name, me.username or me.id)
    return True


async def start_user_client(existing_client: TelegramClient = None, me=None) -> None:
    """Start one freshly signed-in client, or every stored session."""
    if existing_client is not None:
        await _launch(existing_client, me)
        return

    sessions = await mongo.load_all_sessions()
    if not sessions:
        log.info("no stored session yet - send /login to the control bot")
        return

    for record in sessions:
        client = TelegramClient(
            StringSession(record["session"]), settings.api_id, settings.api_hash
        )
        if not await _launch(client):
            log.warning("session for %s is invalid", record.get("user_id"))
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

    bot = TelegramClient(
        StringSession(await mongo.get_setting("control_session", "")),
        settings.api_id,
        settings.api_hash,
    )
    await bot.start(bot_token=settings.bot_token)
    # Persist the control-bot session so a restart does not re-authenticate
    # (the old code wrote a bot_session file that Render throws away).
    await mongo.set_setting("control_session", bot.session.save())

    control.register(bot, start_user_client)
    me = await bot.get_me()
    log.info("control bot @%s ready", me.username)

    await start_user_client()
    _spawn(userbot.cleanup_loop(), name="history-cleanup")

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
