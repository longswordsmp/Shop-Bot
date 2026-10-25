from __future__ import annotations

from shopbot.db.repos import state
from shopbot.features.setup.service import run_setup
from tests.helpers import TestEnv, provision_channels


async def test_setup_withdraws_panels_of_features_that_are_not_ready(tenv: TestEnv) -> None:
    app = tenv.app
    await provision_channels(tenv)
    admin = tenv.member(admin=True)
    await run_setup(app, tenv.guild, actor_id=admin.id)
    panel = await app.db.run(state.published_get, app.env.guild_id, "tickets:panel")
    assert panel is not None
    channel = tenv.guild.get_channel(panel.channel_id)
    assert any(m.id == panel.message_id for m in channel.messages)

    # A required ticket text is removed -> tickets is no longer ready -> the next /setup withdraws the panel.
    values = dict(tenv.content)
    values.pop("tickets.panel.title")
    app.content.replace(file_values=values)
    app.recompute_readiness()
    assert not app.ready("tickets")
    dry = await run_setup(app, tenv.guild, actor_id=admin.id, dry_run=True)
    assert any("tickets:panel" in line for line in dry.lines["panels"])
    assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is not None

    await run_setup(app, tenv.guild, actor_id=admin.id)
    assert await app.db.run(state.published_get, app.env.guild_id, "tickets:panel") is None
    assert not any(m.id == panel.message_id for m in channel.messages)
