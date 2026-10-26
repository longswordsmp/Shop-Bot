"""The welcome mention: a member who joins is mentioned in the entry channel (one message for joins close together),
and the message is deleted verification.welcome_mention_seconds later by a durable task (also after a restart)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from shopbot.features.server_keys import channel_mentions
from shopbot.features.verification import welcome
from tests.verification.util import channel, verification_env

ON = {"verification": {"welcome_mention_seconds": 30}}


async def test_a_join_is_mentioned_and_the_mention_deleted_after_30_seconds(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, ON)
    try:
        entry = channel(tenv, "welcome")
        before = len(entry.messages)
        newcomer = tenv.guild.add_member(name="newcomer")
        await welcome.on_member_join(tenv.app, newcomer)
        await welcome.flush(tenv.app)
        assert len(entry.messages) == before + 1
        posted = entry.messages[-1]
        assert posted.content == newcomer.mention  # the mention pings
        assert posted.allowed_mentions is not None and [u.id for u in posted.allowed_mentions.users] == [newcomer.id]
        assert posted.embeds[0].description == tenv.app.content.render(
            "verification.welcome_mention.description", **channel_mentions(tenv.app)
        )
        assert posted.attachments == []  # no picture: it is short-lived
        tenv.clock.advance(29)
        await tenv.app.scheduler.run_due()
        assert posted in entry.messages
        tenv.clock.advance(2)
        await tenv.app.scheduler.run_due()
        assert posted.deleted and posted not in entry.messages
    finally:
        await tenv.close()


async def test_joins_close_together_share_one_message_and_bots_are_not_mentioned(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, ON)
    try:
        entry = channel(tenv, "welcome")
        before = len(entry.messages)
        members = [tenv.guild.add_member(name=f"m{i}") for i in range(3)]
        bot = tenv.guild.add_member(name="other-bot", bot=True)
        for member in [*members, bot]:
            await welcome.on_member_join(tenv.app, member)
        await welcome.flush(tenv.app)
        assert len(entry.messages) == before + 1
        assert entry.messages[-1].content == " ".join(m.mention for m in members)
    finally:
        await tenv.close()


async def test_a_mention_that_is_already_gone_counts_as_cleaned_up(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, ON)
    try:
        entry = channel(tenv, "welcome")
        await welcome.on_member_join(tenv.app, tenv.guild.add_member(name="newcomer"))
        await welcome.flush(tenv.app)
        posted = entry.messages[-1]
        await posted.delete()  # a moderator removed it first
        tenv.clock.advance(31)
        await tenv.app.scheduler.run_due()
        task = await tenv.app.db.run(
            lambda conn: conn.execute(
                "SELECT status FROM scheduled_tasks WHERE dedupe_key = ?", (welcome.cleanup_key(posted.id),)
            ).fetchone()
        )
        assert task["status"] == "done"
    finally:
        await tenv.close()


async def test_nothing_is_posted_while_the_mention_is_off(tmp_path: Path) -> None:
    tenv = await verification_env(tmp_path, {"verification": {"welcome_mention_seconds": 0}})
    try:
        entry = channel(tenv, "welcome")
        before = len(entry.messages)
        await welcome.on_member_join(tenv.app, tenv.guild.add_member(name="newcomer"))
        await welcome.flush(tenv.app)
        assert len(entry.messages) == before
    finally:
        await tenv.close()


async def test_the_group_timer_posts_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(welcome, "GROUP_SECONDS", 0.01)
    tenv = await verification_env(tmp_path, ON)
    try:
        entry = channel(tenv, "welcome")
        before = len(entry.messages)
        first, second = tenv.guild.add_member(name="a"), tenv.guild.add_member(name="b")
        await welcome.on_member_join(tenv.app, first)
        await welcome.on_member_join(tenv.app, second)
        await asyncio.sleep(0.1)
        assert len(entry.messages) == before + 1
        assert entry.messages[-1].content == f"{first.mention} {second.mention}"
    finally:
        await tenv.close()
