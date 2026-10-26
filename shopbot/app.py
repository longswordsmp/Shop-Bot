"""Process entry point: validate, initialize, connect, and shut down cleanly.

Startup order: environment → logging → single-instance lock → configuration → database + migrations →
application services → Discord connection (commands and persistent components are registered in
``setup_hook``; recovery runs in ``on_ready``). Fatal startup errors are logged with a clear diagnostic
and the process waits ``STARTUP_FAILURE_DELAY_SECONDS`` before exiting, so a platform restart policy
cannot spin in a tight loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time

import discord
from dotenv import load_dotenv

from shopbot import __version__
from shopbot.config.content import ContentFileError
from shopbot.config.loader import load_config
from shopbot.config.settings import SettingsError
from shopbot.core.clock import SystemClock
from shopbot.core.instance_lock import InstanceLock, InstanceLockError
from shopbot.core.intents import effective, read_portal_intents
from shopbot.core.intents import report as intents_report
from shopbot.db.database import Database, DatabaseError
from shopbot.db.migrate import MigrationError, apply_migrations
from shopbot.db.repos import state
from shopbot.env import Env, EnvError, load_env
from shopbot.logging_setup import configure_logging, log_event

log = logging.getLogger("shopbot")

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_CONFIG_ERROR = 78
EXIT_DB_ERROR = 70
EXIT_LOCKED = 75
EXIT_UNAVAILABLE = 69  # could not reach Discord before the first READY (network/outage)


def _fatal_delay(seconds: int) -> None:
    if seconds > 0:
        log_event(log, logging.INFO, "startup_failure_delay", seconds=seconds)
        time.sleep(seconds)


async def run(env: Env) -> int:
    from shopbot.bot import ShopBot
    from shopbot.context import App
    from shopbot.features import all_modules

    try:
        loaded = load_config(env.config_dir)
    except (SettingsError, ContentFileError) as exc:
        for error in exc.errors:
            log_event(log, logging.ERROR, "config_invalid", problem=error)
        return EXIT_CONFIG_ERROR

    db = Database(env.db_path)
    try:
        await db.open()
        applied = await db.run(apply_migrations, db_path=env.db_path)
        log_event(log, logging.INFO, "db_ready", migrations_applied=applied)
        overrides = await db.run(state.overrides_all)
    except (DatabaseError, MigrationError, OSError) as exc:
        log_event(log, logging.CRITICAL, "db_init_failed", error=str(exc))
        await db.close()
        return EXIT_DB_ERROR

    try:
        app = App(
            env=env,
            settings=loaded.settings,
            content_values=loaded.content_values,
            overrides=overrides,
            db=db,
            clock=SystemClock(),
            modules=all_modules(),
            config_dir=env.config_dir,
        )
    except ValueError as exc:
        log_event(log, logging.CRITICAL, "app_init_failed", error=str(exc))
        await db.close()
        return EXIT_CONFIG_ERROR

    for status in app.readiness.statuses.values():
        if status.enabled and not status.ready:
            log_event(
                log,
                logging.WARNING,
                "feature_not_ready",
                feature=status.name,
                problems=len(status.problems),
                sample=[f"{p.key or ''} {p.message}".strip() for p in status.problems[:5]],
            )
    if app.readiness.unknown_content_keys:
        log_event(log, logging.WARNING, "content_unknown_keys", keys=app.readiness.unknown_content_keys[:20])

    # A privileged intent the Developer Portal does not allow is left out instead of making Discord refuse the login.
    requested = app.gateway_intents
    app.portal_intents = await read_portal_intents(env.discord_token)
    app.gateway_intents = effective(requested, app.portal_intents)
    for name in intents_report(requested, app.portal_intents):
        log_event(
            log,
            logging.WARNING,
            "privileged_intent_not_enabled",
            intent=name,
            hint="switch it on in the Developer Portal (Bot -> Privileged Gateway Intents), then restart",
        )

    bot = ShopBot(app)
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    background: set[asyncio.Task[None]] = set()

    def request_stop(signame: str) -> None:
        if stop_requested.is_set():
            return
        log_event(log, logging.INFO, "shutdown_signal", signal=signame)
        stop_requested.set()
        task = loop.create_task(bot.close())
        background.add(task)
        task.add_done_callback(background.discard)

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop, sig.name)

    exit_code = EXIT_OK
    try:
        async with bot:
            await bot.start(env.discord_token, reconnect=True)
    except discord.LoginFailure:
        log_event(log, logging.CRITICAL, "discord_login_failed", hint="DISCORD_TOKEN is invalid or was reset")
        exit_code = EXIT_CONFIG_ERROR
    except discord.PrivilegedIntentsRequired:
        log_event(
            log,
            logging.CRITICAL,
            "privileged_intents_required",
            hint="enable Message Content (and Server Members if members_intent=true) in the Developer Portal",
        )
        exit_code = EXIT_CONFIG_ERROR
    except Exception as exc:
        if not getattr(bot, "gateway_ready_seen", False):
            # Login/first connection failed (network error, Discord outage). discord.py retries reconnects only
            # after a successful connection; exit and let the platform restart a clean process after a delay.
            log_event(log, logging.ERROR, "discord_unreachable_at_startup", error=type(exc).__name__)
            exit_code = EXIT_UNAVAILABLE
        else:
            log.exception("bot stopped unexpectedly")
            exit_code = EXIT_RUNTIME_ERROR
    finally:
        await app.scheduler.stop()
        await db.close()
    if not stop_requested.is_set() and exit_code == EXIT_OK:
        exit_code = EXIT_RUNTIME_ERROR  # the client returned without a shutdown request
    log_event(log, logging.INFO, "shutdown_complete", exit_code=exit_code)
    return exit_code


def main() -> int:
    load_dotenv(override=False)  # local development only; never overrides real environment variables
    try:
        env = load_env()
    except EnvError as exc:
        configure_logging("INFO", "json")
        for problem in exc.problems:
            log_event(log, logging.CRITICAL, "env_invalid", problem=problem)
        _fatal_delay(30)
        return EXIT_CONFIG_ERROR

    configure_logging(env.log_level, env.log_format, secrets=[env.discord_token])
    log_event(
        log,
        logging.INFO,
        "starting",
        version=__version__,
        python=sys.version.split()[0],
        discord_py=discord.__version__,
        app_env=env.app_env,
        db_path=str(env.db_path),
    )

    lock = InstanceLock(env.db_path.with_name(env.db_path.name + ".lock"))
    try:
        lock.acquire()
    except InstanceLockError as exc:
        log_event(log, logging.CRITICAL, "instance_locked", error=str(exc))
        _fatal_delay(env.startup_failure_delay_seconds)
        return EXIT_LOCKED
    try:
        code = asyncio.run(run(env))
    finally:
        lock.release()
    if code in (EXIT_CONFIG_ERROR, EXIT_DB_ERROR, EXIT_UNAVAILABLE):
        _fatal_delay(env.startup_failure_delay_seconds)
    return code
