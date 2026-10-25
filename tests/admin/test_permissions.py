"""Every admin command and control is administrator-only (staff and customers are denied)."""

from __future__ import annotations

from typing import Any

import discord
import pytest

from shopbot.db.repos import state as state_repo
from shopbot.features import core_keys as ck
from shopbot.features.admin.commands import (
    clear_key_autocomplete,
    feature_autocomplete,
    key_autocomplete,
)
from shopbot.features.admin.state import state_for
from tests.admin.util import admin, audit_actions, customer, embed, owner, run_command, staff
from tests.fakes import FakeInteraction, click, submit_modal
from tests.helpers import TestEnv

COMMANDS: list[tuple[str | None, str, dict[str, Any]]] = [
    (None, "status", {}),
    (None, "status", {"feature": "tickets"}),
    (None, "reload", {}),
    (None, "backup", {}),
    (None, "audit", {}),
    (None, "tasks", {}),
    ("content", "set", {"key": ck.ERROR_GENERIC}),
    ("content", "show", {"key": ck.ERROR_GENERIC}),
    ("content", "clear", {"key": ck.ERROR_GENERIC}),
    ("content", "missing", {}),
    ("content", "keys", {}),
    ("content", "export", {}),
]


def _ids(item: tuple[str | None, str, dict[str, Any]]) -> str:
    group, name, kwargs = item
    return " ".join(p for p in (group, name, *kwargs) if p)


@pytest.mark.parametrize("command", COMMANDS, ids=[_ids(c) for c in COMMANDS])
@pytest.mark.parametrize("who", ["customer", "staff"])
async def test_non_admins_are_denied_every_command(tenv: TestEnv, command: Any, who: str) -> None:
    group, name, kwargs = command
    user = customer(tenv) if who == "customer" else staff(tenv)
    before = await audit_actions(tenv.app)
    interaction = await run_command(tenv, name, group=group, user=user, **kwargs)
    assert interaction.response.modal is None
    assert len(interaction.sent) == 1
    assert embed(interaction).description == tenv.text(ck.ERROR_PERMISSION)
    assert interaction.sent[0].files == []
    assert await audit_actions(tenv.app) == before
    assert not (tenv.app.env.db_path.parent / "backups").exists()


@pytest.mark.parametrize("command", COMMANDS, ids=[_ids(c) for c in COMMANDS])
async def test_admins_and_owner_can_run_every_command(tenv: TestEnv, command: Any) -> None:
    group, name, kwargs = command
    for user in (admin(tenv), owner(tenv)):
        interaction = await run_command(tenv, name, group=group, user=user, **kwargs)
        if (group, name) == ("content", "set"):
            assert interaction.response.modal is not None
            continue
        assert interaction.sent, f"{group} {name}: no reply"
        assert all(getattr(m, "ephemeral", False) for m in interaction.sent)
        assert tenv.text(ck.ERROR_PERMISSION) not in "\n".join(m.text() for m in interaction.sent)


async def test_commands_are_hidden_from_non_admins_by_default(tenv: TestEnv) -> None:
    tree = tenv.bot.tree
    commands = tree.get_commands(guild=tenv.app.guild_object)
    names = {c.name for c in commands}
    assert {"status", "reload", "backup", "audit", "tasks", "content"} <= names
    for command in commands:
        if command.name in {"status", "reload", "backup", "audit", "tasks", "content"}:
            assert command.default_permissions == discord.Permissions(administrator=True)
            assert command.guild_only is True


async def test_status_pagination_buttons_are_admin_only(tenv: TestEnv) -> None:
    from shopbot.config.readiness import Problem

    status = tenv.app.readiness.statuses["tickets"]
    status.problems = [Problem("tickets", "missing_content", "not configured", f"x.k{i:03d}") for i in range(300)]
    user = admin(tenv)
    first = await run_command(tenv, "status", user=user, feature="tickets")
    message = first.last_sent
    assert message is not None
    assert "sb:ad:status:tickets:2" in message.custom_ids()
    original = message.embeds[0].to_dict()

    for intruder in (staff(tenv), customer(tenv)):
        denied = await click(tenv.app, user=intruder, custom_id="sb:ad:status:tickets:2", message=message)
        assert denied.last_sent is not None
        assert denied.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
        assert message.embeds[0].to_dict() == original

    moved = await click(tenv.app, user=user, custom_id="sb:ad:status:tickets:2", message=message)
    assert moved.response_kinds() == ["edit_message"]
    assert message.embeds[0].footer.text is not None and message.embeds[0].footer.text.startswith("Page 2 of ")


@pytest.mark.parametrize("action", ["miss", "keys"])
async def test_content_list_buttons_are_admin_only(tenv: TestEnv, action: str) -> None:
    interaction = await click(tenv.app, user=staff(tenv), custom_id=f"sb:ad:{action}:-:2")
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)


async def test_content_form_submission_is_admin_only(tenv: TestEnv) -> None:
    user = admin(tenv)
    opened = await run_command(tenv, "set", group="content", user=user, key=ck.ERROR_GENERIC)
    assert opened.response.modal is not None
    custom_id = opened.response.modal.custom_id
    intruder = staff(tenv)
    denied = await submit_modal(tenv.app, user=intruder, custom_id=custom_id, fields={"value": "hijack"})
    assert denied.last_sent is not None
    assert denied.last_sent.embeds[0].description == tenv.text(ck.ERROR_PERMISSION)
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert len(state_for(tenv.app).pending) == 1  # still usable by the admin who opened it


async def test_denials_without_configured_text_are_silent(tenv: TestEnv) -> None:
    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    interaction = await run_command(tenv, "status", user=staff(tenv))
    assert interaction.sent == []
    assert interaction.response_kinds() == ["defer"]
    clicked = await click(tenv.app, user=staff(tenv), custom_id="sb:ad:keys:-:1")
    assert clicked.sent == []
    assert clicked.response_kinds() == ["defer"]


async def test_admin_tools_work_when_customer_content_is_missing(tenv: TestEnv) -> None:
    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    assert tenv.app.ready("admin")
    interaction = await run_command(tenv, "status", user=admin(tenv))
    assert interaction.sent and interaction.sent[0].embeds[0].title == "Bot status"


async def test_autocomplete_only_answers_admins(tenv: TestEnv) -> None:
    def interaction_for(user: Any) -> Any:
        return FakeInteraction(client=tenv.bot, user=user, guild=tenv.guild, type=discord.InteractionType.autocomplete)

    for callback, query in (
        (key_autocomplete, "error"),
        (clear_key_autocomplete, "error"),
        (feature_autocomplete, "ti"),
    ):
        assert await callback(interaction_for(staff(tenv)), query) == []
        choices = await callback(interaction_for(admin(tenv)), query)
        assert 0 < len(choices) <= 25
        assert all(query in choice.value for choice in choices)


async def test_key_autocomplete_is_capped_and_prefers_prefix(tenv: TestEnv) -> None:
    interaction = FakeInteraction(
        client=tenv.bot, user=admin(tenv), guild=tenv.guild, type=discord.InteractionType.autocomplete
    )
    choices = await key_autocomplete(interaction, "")
    assert len(choices) == 25
    choices = await key_autocomplete(interaction, "common.")
    assert choices and all(c.value.startswith("common.") for c in choices)
