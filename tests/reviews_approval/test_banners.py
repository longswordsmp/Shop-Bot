"""Banners with staff approval: the approval message is one message whose banner follows its state (pending,
approved, rejected, removed), the customer's notices and the staff replies show theirs, and edits that keep the
state never upload the banner again."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.config.banner_ids import BannerId
from shopbot.features.reviews import MODULE
from shopbot.features.reviews import content_keys as k
from tests.fakes import FakeInteraction, FakeMessage
from tests.helpers import TestEnv, banner_names, make_banner_env, provision_channels
from tests.reviews.util import invoke, only_embed, review_of, reviews_channel
from tests.reviews_approval.util import approval_overrides, approve, pending_review, reject


@pytest.fixture
async def bn(tmp_path: Path) -> AsyncIterator[TestEnv]:
    """Reviews with staff approval on, every channel provisioned, banners on."""
    tenv = await make_banner_env(tmp_path, approval_overrides())
    await provision_channels(tenv)
    try:
        yield tenv
    finally:
        await tenv.close()


def name(tenv: TestEnv, banner_id: BannerId) -> str:
    banner = tenv.app.ui.banner(banner_id)
    assert banner is not None
    return banner.filename


def uploaded_banners(edit: dict[str, Any]) -> list[str]:
    """Banner files an edit uploads again (a kept attachment is not an upload)."""
    return [
        a.filename
        for a in edit.get("attachments") or []
        if isinstance(a, discord.File) and a.filename.startswith("banner-")
    ]


def reply(interaction: FakeInteraction) -> FakeMessage:
    assert interaction.last_sent is not None and getattr(interaction.last_sent, "ephemeral", False)
    return interaction.last_sent


async def test_approval_swaps_the_banners_and_tells_the_customer(bn: TestEnv) -> None:
    customer, staff = bn.member(), bn.member(name="mod", staff=True)
    order, review, message = await pending_review(bn, customer)
    thanks = customer.dms[0]  # the request, replaced by the 'pending' thank-you text
    assert banner_names(thanks) == [name(bn, "pending-review")] and len(thanks.attachments) == 1
    assert banner_names(message) == [name(bn, "pending-review")]

    decided = await approve(bn, staff, review, message)
    assert banner_names(message) == [name(bn, "successful-confirmation")] and len(message.attachments) == 1
    assert banner_names(reply(decided)) == [name(bn, "general-response")]
    (post,) = reviews_channel(bn).messages
    assert banner_names(post) == [name(bn, "reviews")]
    assert banner_names(customer.dms[-1]) == [name(bn, "successful-confirmation")]  # the approval notice

    again = await approve(bn, staff, review, message)  # decided already: shown as it is, refused
    assert "attachments" not in message.edits[-1] and banner_names(message) == [name(bn, "successful-confirmation")]
    assert banner_names(reply(again)) == [name(bn, "error")]

    await invoke(bn, staff, "remove", order_number=order.number)  # the published review is closed
    assert (await review_of(bn, order)).status == "declined"
    assert banner_names(message) == [name(bn, "cancellation")] and len(message.attachments) == 1


async def test_rejection_swaps_the_banner_and_tells_the_customer(bn: TestEnv) -> None:
    customer, staff = bn.member(), bn.member(name="mod", staff=True)
    _, review, message = await pending_review(bn, customer)
    decided = await reject(bn, staff, review, message, "[reason]")
    assert banner_names(message) == [name(bn, "rejected-or-unsuccessful-submission")]
    assert len(message.attachments) == 1
    assert banner_names(reply(decided)) == [name(bn, "general-response")]
    assert banner_names(customer.dms[-1]) == [name(bn, "rejected-or-unsuccessful-submission")]
    assert reviews_channel(bn).messages == []


async def test_removing_a_pending_review_shows_the_cancellation_banner(bn: TestEnv) -> None:
    customer, staff = bn.member(), bn.member(name="mod", staff=True)
    order, _, message = await pending_review(bn, customer)
    removed = await invoke(bn, staff, "remove", order_number=order.number)
    assert banner_names(removed.last_sent) == [name(bn, "general-response")]  # type: ignore[arg-type]
    assert banner_names(message) == [name(bn, "cancellation")] and len(message.attachments) == 1
    decided = await invoke(bn, staff, "approve", order_number=order.number)  # nothing to decide any more
    assert banner_names(decided.last_sent) == [name(bn, "error")]  # type: ignore[arg-type]


async def test_setup_edits_pending_approval_messages_without_uploading_the_banner_again(bn: TestEnv) -> None:
    assert MODULE.publish is not None
    order, _, message = await pending_review(bn, bn.member())
    bn.app.content.replace(file_values={**bn.content, k.APPROVAL_TITLE: "[new title] {order_number}"})
    await MODULE.publish(bn.app)
    assert only_embed(message).title == f"[new title] {order.number}"
    assert uploaded_banners(message.edits[-1]) == [] and banner_names(message) == [name(bn, "pending-review")]
