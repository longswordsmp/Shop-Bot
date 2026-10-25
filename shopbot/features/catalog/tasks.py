"""Scheduled listing refresh, restart recovery, and event listeners."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from shopbot.db.repos import state
from shopbot.db.repos.tasks import Task
from shopbot.features.catalog import service
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.logging_setup import log_event

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)


class ListingRefreshRetry(Exception):
    """A transient Discord error stopped the refresh; the scheduler retries with backoff."""


async def run_refresh_task(app: App, task: Task) -> None:
    """Task ``catalog.refresh_listing``: idempotent (re-reads products and edits messages in place)."""
    if not app.ready(CATALOG):
        return
    report = await service.refresh_if_needed(app)
    if report is not None and not report.ok and report.retry:
        raise ListingRefreshRetry("; ".join(report.lines))


async def on_ready(app: App) -> None:
    """Restart recovery: re-verify a previously published listing (re-posts deleted messages, never duplicates).

    Nothing is created when the listing was never published (that is /setup's job).
    """
    if not app.ready(CATALOG):
        return
    records = await app.db.run(state.published_with_prefix, app.env.guild_id, service.LISTING_PREFIX)
    if records:
        await service.schedule_listing_refresh(app)
        log_event(log, logging.INFO, "catalog_listing_verify_scheduled", messages=len(records))


async def on_raw_message_delete(app: App, payload: discord.RawMessageDeleteEvent) -> None:
    """A listing message was deleted by someone: schedule a refresh, which re-posts it."""
    if not app.ready(CATALOG):
        return
    records = await app.db.run(state.published_with_prefix, app.env.guild_id, service.LISTING_PREFIX)
    if any(record.message_id == payload.message_id for record in records):
        log_event(log, logging.INFO, "catalog_listing_message_deleted", message_id=payload.message_id)
        await service.schedule_listing_refresh(app)
