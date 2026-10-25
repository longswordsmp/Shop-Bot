from __future__ import annotations

from shopbot.core.panels import remove_message, upsert_message
from shopbot.ui.spec import EmbedSpec, MessageSpec
from tests.helpers import TestEnv


async def test_upsert_is_idempotent_and_recovers_deleted_messages(tenv: TestEnv) -> None:
    app = tenv.app
    channel = tenv.guild.add_text_channel(name="panel")
    other = tenv.guild.add_text_channel(name="other")
    spec = MessageSpec(embeds=(EmbedSpec(title="a"),))

    _, action = await upsert_message(app, "panel:x", channel, spec)
    assert action == "created" and len(channel.messages) == 1
    _, action = await upsert_message(app, "panel:x", channel, spec)
    assert action == "unchanged" and len(channel.messages) == 1

    _, action = await upsert_message(app, "panel:x", channel, MessageSpec(embeds=(EmbedSpec(title="b"),)))
    assert action == "updated" and len(channel.messages) == 1
    assert channel.messages[0].embeds[0].title == "b"

    await channel.messages[0].delete()
    _, action = await upsert_message(app, "panel:x", channel, spec)
    assert action == "recreated" and len(channel.messages) == 1

    _, action = await upsert_message(app, "panel:x", other, spec)
    assert action == "moved" and len(channel.messages) == 0 and len(other.messages) == 1

    assert await remove_message(app, "panel:x") is True
    assert other.messages == []
    assert await remove_message(app, "panel:x") is False
