"""Helpers shared by the new-arrivals tests."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shopbot.config.settings import Settings
from shopbot.context import App
from shopbot.db.repos import audit as audit_repo
from shopbot.features.catalog import repo as catalog_repo
from shopbot.features.catalog import service as catalog_service
from shopbot.features.new_arrivals import repo, service
from tests.fakes import FakeInteraction, FakeMember, FakeMessage, FakeRole, FakeTextChannel, click
from tests.helpers import (
    GUILD_ID,
    TestEnv,
    deep_merge,
    make_settings,
    make_test_env,
    provision_channels,
    server_extension,
)

ROLE_KEY = "restock_alerts"
CHANNEL_KEY = "new_arrivals"
TOGGLE_ID = "sb:na:toggle"
STAFF_ID = 999_000_000_000_000_001

NA_SETTINGS: dict[str, Any] = deep_merge(
    server_extension(
        roles=[{"key": ROLE_KEY, "mentionable": True}],
        channels=[{"key": CHANNEL_KEY, "category": "support", "access": "inherit"}],
    ),
    {"features": {"new_arrivals": True}, "new_arrivals": {"channel": CHANNEL_KEY, "role": ROLE_KEY}},
)


def na_settings(overrides: dict[str, Any] | None = None) -> Settings:
    return make_settings(deep_merge(NA_SETTINGS, overrides or {}))


@dataclass
class NaEnv:
    tenv: TestEnv
    role: FakeRole
    channel: FakeTextChannel

    @property
    def app(self) -> App:
        return self.tenv.app

    def text(self, key: str) -> str:
        return self.tenv.text(key)

    def member(self, name: str = "customer", *, subscribed: bool = False) -> FakeMember:
        member = self.tenv.member(name=name)
        if subscribed:
            member.add_role(self.role)
        return member

    def panels(self) -> list[FakeMessage]:
        return [m for m in self.channel.messages if TOGGLE_ID in m.custom_ids()]

    def updates(self) -> list[FakeMessage]:
        """Bot messages in the new-arrivals channel other than the panel (the automatic product updates)."""
        return [m for m in self.channel.messages if TOGGLE_ID not in m.custom_ids()]

    async def close(self) -> None:
        await self.tenv.close()


async def na_env(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    content: dict[str, str] | None = None,
    role_position: int = 5,
) -> NaEnv:
    """A ready new-arrivals environment: channels provisioned, the notification role created and recorded."""
    tenv = await make_test_env(tmp_path, settings=na_settings(overrides), content=content)
    await provision_channels(tenv)
    role = tenv.guild.add_role(name="restock-alerts", position=role_position)
    await tenv.app.resources.remember("role", ROLE_KEY, role.id)
    channel = tenv.app.resources.text_channel(tenv.guild, CHANNEL_KEY)  # type: ignore[arg-type]
    assert isinstance(channel, FakeTextChannel)
    return NaEnv(tenv=tenv, role=role, channel=channel)


def has_role(member: FakeMember, role: FakeRole) -> bool:
    return any(r.id == role.id for r in member.roles)


def role_count(member: FakeMember, role: FakeRole) -> int:
    return sum(1 for r in member.roles if r.id == role.id)


async def toggle(
    env: NaEnv, member: FakeMember, *, app: App | None = None, message: FakeMessage | None = None
) -> FakeInteraction:
    return await click(app or env.app, user=member, custom_id=TOGGLE_ID, message=message, channel=env.channel)


def reply_text(interaction: FakeInteraction) -> str:
    return "\n".join(m.text() for m in interaction.sent)


async def audit(env: NaEnv, prefix: str = "new_arrivals.") -> list[audit_repo.AuditEvent]:
    events = await env.app.db.run(audit_repo.recent, GUILD_ID, limit=200)
    return [e for e in reversed(events) if e.action.startswith(prefix)]


# -- catalog changes through the real catalog service (PRODUCT_CHANGED is published exactly as in production) ---


def form(
    name: str = "[new product]", *, price: str = "12.50", stock: str = "", description: str = ""
) -> dict[str, str]:
    return {"name": name, "price": price, "stock": stock, "max_per_order": "", "description": description}


async def add_product(env: NaEnv, name: str = "[new product]", *, stock: str = "", description: str = "") -> int:
    reply = await catalog_service.create_from_form(
        env.app, actor_id=STAFF_ID, fields=form(name, stock=stock, description=description), nonce=None
    )
    assert reply.tone == "success", reply
    products = await env.app.db.run(catalog_repo.list_products, GUILD_ID, include_inactive=True, include_archived=True)
    return max(p.id for p in products)


async def product(env: NaEnv, product_id: int) -> catalog_repo.Product:
    found = await env.app.db.run(catalog_repo.get_product, GUILD_ID, product_id)
    assert found is not None
    return found


async def set_stock(env: NaEnv, product_id: int, mode: str, amount: int | None = None) -> None:
    reply = await catalog_service.change_stock(
        env.app, actor_id=STAFF_ID, product_id=product_id, mode=mode, amount=amount
    )
    assert reply.tone == "success", reply


async def run_tasks(env: NaEnv, app: App | None = None) -> int:
    return await (app or env.app).scheduler.run_due()


async def announcements(env: NaEnv, product_id: int) -> list[repo.Announcement]:
    return await env.app.db.run(repo.list_for_product, GUILD_ID, product_id)


def _task_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT dedupe_key, status FROM scheduled_tasks WHERE kind = ?", (service.ANNOUNCE_TASK,))
    return [(str(r["dedupe_key"]), str(r["status"])) for r in rows]


async def update_tasks(env: NaEnv) -> list[tuple[str, str]]:
    """(dedupe key, status) of every scheduled new-arrivals update task."""
    return await env.app.db.run(_task_rows)
