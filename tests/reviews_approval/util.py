"""Helpers for the review approval tests (``reviews.require_approval``).

The approval environment adds a staff-only ``staff_chat`` channel (as shipped) to the test base layout and turns
approval on; orders and their order channels are inserted directly, like the reviews tests do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from shopbot.context import App
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, views
from tests.fakes import FakeInteraction, FakeMember, FakeMessage, FakeTextChannel, click, submit_modal
from tests.helpers import TestEnv, deep_merge, server_extension
from tests.reviews.util import make_env, prompted, review_of

APPROVAL_CHANNEL = "staff_chat"


def approval_overrides(
    extra: dict[str, Any] | None = None, *, roles: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    base = deep_merge(
        server_extension(roles=roles, channels=[{"key": APPROVAL_CHANNEL, "category": "staff", "access": "inherit"}]),
        {"reviews": {"require_approval": True, "approval_channel": APPROVAL_CHANNEL}},
    )
    return deep_merge(base, extra or {})


async def make_approval_env(
    tmp_path: Path,
    extra: dict[str, Any] | None = None,
    *,
    roles: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> TestEnv:
    """All features enabled, approval on, every channel provisioned (as /setup would)."""
    return await make_env(tmp_path, approval_overrides(extra, roles=roles), **kwargs)


def approval_channel(tenv: TestEnv, app: App | None = None) -> FakeTextChannel:
    app = app or tenv.app
    channel_id = app.resources.channel_id(APPROVAL_CHANNEL)
    channel = tenv.guild.get_channel(channel_id)
    assert isinstance(channel, FakeTextChannel)
    return channel


def only_message(channel: FakeTextChannel) -> FakeMessage:
    assert len(channel.messages) == 1, channel.messages
    return channel.messages[0]


async def submit(
    tenv: TestEnv,
    user: FakeMember,
    review: repo.Review,
    request: FakeMessage,
    *,
    rating: str = "4",
    comment: str = "[kept comment]",
    app: App | None = None,
) -> FakeInteraction:
    """The customer picks a rating and submits the comment form in their direct messages."""
    app = app or tenv.app
    await click(app, user=user, custom_id=views.rate_id(review.id), message=request, values=[rating], dm=True)
    return await submit_modal(
        app,
        user=user,
        custom_id=views.comment_id(review.id),
        fields={views.COMMENT_INPUT_ID: comment},
        message=request,
        dm=True,
    )


async def pending_review(
    tenv: TestEnv, user: FakeMember, *, comment: str = "[kept comment]", **kwargs: Any
) -> tuple[Any, repo.Review, FakeMessage]:
    """A submitted review waiting for approval, with its approval message posted. Returns (order, review, message)."""
    order, review, request = await prompted(tenv, user, **kwargs)
    await submit(tenv, user, review, request, comment=comment)
    current = await review_of(tenv, order)
    assert current.status == repo.PENDING_APPROVAL, current
    message = approval_channel(tenv).messages[-1]
    assert message.id == current.approval_message_id
    return order, current, message


async def approve(
    tenv: TestEnv, staff: FakeMember, review: repo.Review, message: FakeMessage | None, app: App | None = None
) -> FakeInteraction:
    return await click(app or tenv.app, user=staff, custom_id=views.approve_id(review.id), message=message)


async def reject(
    tenv: TestEnv,
    staff: FakeMember,
    review: repo.Review,
    message: FakeMessage | None,
    reason: str = "",
    app: App | None = None,
) -> FakeInteraction:
    return await submit_modal(
        app or tenv.app,
        user=staff,
        custom_id=views.reject_form_id(review.id),
        fields={views.REASON_INPUT_ID: reason},
        message=message,
    )


def staff_reply(interaction: FakeInteraction) -> discord.Embed:
    """The private operator reply that follows a staff decision."""
    message = interaction.last_sent
    assert message is not None and message.embeds
    assert getattr(message, "ephemeral", False)
    return message.embeds[0]


def field_pairs(embed: discord.Embed) -> list[tuple[str | None, str | None]]:
    return [(f.name, f.value) for f in embed.fields]


def status_value(tenv: TestEnv, state: str, staff_id: int | None = None) -> str:
    text = tenv.text(k.approval_status_key(state))
    return text.replace("{staff_mention}", f"<@{staff_id}>" if staff_id else "")
