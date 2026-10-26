"""/announce preset:<id>: autocomplete, the private preview (with and without a usable coupon), Post (once, after a
restart, a double press, two staff members), Cancel, mention safety, the claim button on the post, and the button
switching to "offer ended" when the code ends (/discount end), expires (a press) or ended while the bot was down."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.features import core_keys as ck
from shopbot.features.announcements import MODULE, presets, repo, service
from shopbot.features.announcements.commands import mention_autocomplete, preset_autocomplete
from shopbot.features.announcements.content_keys import (
    PRESET_CLAIM_BUTTON,
    PRESET_DESCRIPTION,
    PRESET_ENDED_BUTTON,
    PRESET_TITLE,
    preset_key,
)
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import discount_codes, discount_repo, discount_views
from shopbot.features.checkout import discounts as d
from shopbot.ui.spec import ButtonSpec, MessageSpec
from tests.announcements.util import (
    CDN_URL,
    AnnounceEnv,
    Upload,
    announce_env,
    audit_events,
    form_fields,
    invoke,
    restarted_app,
    rows,
    submit,
)
from tests.fakes import FakeInteraction, FakeMessage, click, http_error
from tests.helpers import GUILD_ID
from tools.check_limits import check_state, unrouted_components

DAY = 86_400
PRESET_ID = "warehouse_sale"
PRESET_NAME = "[Warehouse sale]"
COMMAND = "/discount create code:BOGO type:Buy one, get one free once_per_customer:false products:<ids>"
PRESETS: dict[str, Any] = {
    "announcements": {
        "presets": [
            {"id": PRESET_ID, "name": PRESET_NAME, "coupon": "bogo", "coupon_command": COMMAND},
            {"id": "closed", "name": "[Closed for the holidays]"},
        ]
    }
}
DESCRIPTION = "[Dear customers] {icon_gift} [claim it below] {channel_catalog}"


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[AnnounceEnv]:
    announce = await announce_env(tmp_path, PRESETS, content={preset_key(PRESET_ID, PRESET_DESCRIPTION): DESCRIPTION})
    try:
        yield announce
    finally:
        await announce.close()


async def make_code(
    env: AnnounceEnv, code: str = "BOGO", *, days: int | None = None, max_uses: int | None = None
) -> d.Code:
    now = env.tenv.clock.now()
    return await env.app.db.transaction(
        discount_repo.create_code,
        guild_id=GUILD_ID,
        code=code,
        kind=d.BOGO,
        value=0,
        min_subtotal_minor=0,
        max_uses=max_uses,
        once_per_customer=False,
        expires_at=now + days * DAY if days else None,
        product_ids=[],
        actor_id=1,
        now=now,
    )


def op(env: AnnounceEnv, key: str, **values: Any) -> str:
    return env.app.op(key, **values)


def stored_url(message: FakeMessage) -> str:
    """The link the bot builds from the stored ids of a post."""
    assert message.channel is not None
    return f"https://discord.com/channels/{GUILD_ID}/{message.channel.id}/{message.id}"


def buttons(message: FakeMessage) -> list[tuple[str | None, str | None, bool]]:
    """(custom_id, label, disabled) of every button of a message."""
    found: list[tuple[str | None, str | None, bool]] = []
    for child in message.components():
        item = getattr(child, "item", child)
        found.append((getattr(item, "custom_id", None), getattr(item, "label", None), bool(item.disabled)))
    return found


def link_urls(message: FakeMessage) -> list[str]:
    """The URLs of the link buttons of a message (e.g. Open announcement)."""
    return [str(getattr(getattr(c, "item", c), "url", None)) for c in message.components() if getattr(c, "url", None)]


def card_of(interaction: FakeInteraction) -> FakeMessage:
    card = interaction.last_sent
    assert card is not None and card.flags.ephemeral
    return card


async def preview(env: AnnounceEnv, user: Any, preset: str = PRESET_ID, **kwargs: Any) -> FakeInteraction:
    return await invoke(env, user, preset=preset, **kwargs)


async def post(env: AnnounceEnv, user: Any, card: FakeMessage, app: Any = None) -> FakeInteraction:
    post_id = next(cid for cid, _, _ in buttons(card) if cid and cid.startswith("sb:an:post:"))
    return await click(app or env.app, user=user, custom_id=post_id, message=card)


def token_of(card: FakeMessage) -> str:
    post_id = next(cid for cid, _, _ in buttons(card) if cid and cid.startswith("sb:an:post:"))
    return post_id.rsplit(":", 1)[-1]


async def posted(env: AnnounceEnv, staff: Any | None = None, **kwargs: Any) -> tuple[FakeMessage, FakeMessage]:
    """Preview and post the preset; returns (the post, the preview card)."""
    staff = staff or env.staff()
    card = card_of(await preview(env, staff, **kwargs))
    await post(env, staff, card)
    assert len(env.channel.messages) == 1, card.text()
    return env.channel.messages[-1], card


async def autocomplete(env: AnnounceEnv, user: Any, current: str) -> list[tuple[str, str]]:
    interaction = FakeInteraction(client=env.app.bot, user=user, guild=env.tenv.guild)
    choices = await preset_autocomplete(interaction, current)  # type: ignore[arg-type]
    return [(c.name, str(c.value)) for c in choices]


# -- autocomplete -------------------------------------------------------------------------------------------------


async def test_autocomplete_offers_the_presets_by_name_to_staff_only(env: AnnounceEnv) -> None:
    staff = env.staff()
    assert await autocomplete(env, staff, "") == [(PRESET_NAME, PRESET_ID), ("[Closed for the holidays]", "closed")]
    assert await autocomplete(env, staff, "warehouse") == [(PRESET_NAME, PRESET_ID)]
    assert await autocomplete(env, staff, "closed") == [("[Closed for the holidays]", "closed")]
    assert await autocomplete(env, env.customer(), "") == []
    # The mention autocomplete is unchanged.
    interaction = FakeInteraction(client=env.app.bot, user=staff, guild=env.tenv.guild)
    assert [c.value for c in await mention_autocomplete(interaction, "")] == ["alerts", "quiet"]  # type: ignore[arg-type]


# -- the preview ----------------------------------------------------------------------------------------------------


async def test_preview_with_a_usable_coupon(env: AnnounceEnv) -> None:
    code = await make_code(env)
    staff = env.staff()
    interaction = await preview(env, staff, mention="alerts")
    assert interaction.response_kinds() == ["defer"]
    copy, card = interaction.sent
    assert copy.flags.ephemeral and card.flags.ephemeral
    # The announcement exactly as it will be posted, its claim button disabled and nobody pinged by the preview.
    embed = copy.embeds[0]
    assert embed.title == env.text(preset_key(PRESET_ID, PRESET_TITLE))
    catalog = env.app.resources.channel_id("catalog")
    assert embed.description is not None and f"<#{catalog}>" in embed.description
    assert buttons(copy) == [
        (discount_views.claim_id(code.id), env.text(preset_key(PRESET_ID, PRESET_CLAIM_BUTTON)), True)
    ]
    assert copy.content == ""
    # The card: the channel, the mention, the coupon's terms in plain words; Post announcement and Cancel.
    text = card.text()
    assert card.embeds[0].title == op(env, "announcements.preset.preview_title", name=PRESET_NAME)
    assert env.channel.mention in text and f"<@&{env.alerts_role.id}>" in text
    terms = await discount_codes.terms_text(env.app, code)
    assert op(env, "announcements.preset.coupon", code="BOGO", terms=terms) in text
    stored = await rows(env)
    assert len(stored) == 1 and stored[0]["preset_id"] == PRESET_ID and stored[0]["status"] == "draft"
    token = stored[0]["token"]
    assert [(cid, label) for cid, label, _ in buttons(card)] == [
        (f"sb:an:post:{token}", op(env, "announcements.preset.post")),
        (f"sb:an:cancel:{token}", op(env, "announcements.preset.cancel")),
    ]
    assert env.channel.messages == []  # nothing is posted by the preview


async def test_preview_without_the_coupon_names_the_command_and_offers_no_post(env: AnnounceEnv) -> None:
    interaction = await preview(env, env.staff())
    copy, card = interaction.sent
    assert buttons(copy)[0][2] is True  # the copy still shows the (disabled) button
    text = card.text()
    assert op(env, "announcements.preset.coupon_missing", code="BOGO", command=f"`{COMMAND}`") in text
    assert buttons(card) == []
    assert await rows(env) == [] and env.channel.messages == []


@pytest.mark.parametrize("case", ["ended", "expired", "used_up", "codes_off", "already"])
async def test_preview_refuses_a_coupon_that_cannot_be_claimed(env: AnnounceEnv, case: str) -> None:
    code = await make_code(env, days=1, max_uses=1)
    if case == "ended":
        await discount_codes.end(env.app, actor_id=1, code="BOGO")
        expected = op(env, "announcements.preset.coupon_missing", code="BOGO", command=f"`{COMMAND}`")
    elif case == "expired":
        env.tenv.clock.advance(DAY)
        expected = op(env, "announcements.preset.coupon_missing", code="BOGO", command=f"`{COMMAND}`")
    elif case == "used_up":
        await env.app.db.run(
            lambda conn: conn.execute(
                "INSERT INTO orders (guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
                "payment_provider, created_at, updated_at, discount_code_id, discount_code, discount_kind) "
                "VALUES (?, 1, 5, 'pending_payment', 'USD', 100, 100, 'manual', 0, 0, ?, 'BOGO', 'bogo')",
                (GUILD_ID, code.id),
            )
        )
        expected = op(env, "announcements.preset.coupon_used_up", code="BOGO", uses=1)
    elif case == "codes_off":
        settings = env.app.settings
        env.app.settings = replace(settings, checkout=replace(settings.checkout, discount_codes=False))
        expected = op(env, "announcements.preset.coupons_off")
    else:
        message, _ = await posted(env)
        expected = op(env, "announcements.preset.already_announced", code="BOGO")
    card = card_of(await preview(env, env.staff()))
    assert expected in card.text()
    assert not any(cid and cid.startswith("sb:an:post:") for cid, _, _ in buttons(card))
    if case == "already":  # the card links the earlier announcement (Open announcement)
        assert link_urls(card) == [stored_url(message)]


async def test_unknown_preset_and_a_preset_without_coupon(env: AnnounceEnv) -> None:
    unknown = await preview(env, env.staff(), preset="[nope]")
    assert unknown.last_sent is not None
    assert op(env, "announcements.preset.unknown_preset", preset="[nope]") in unknown.last_sent.text()
    # By name works too, and a preset without a coupon has no button and no coupon line.
    closed = await preview(env, env.staff(), preset="[closed for the holidays]")
    copy, card = closed.sent
    assert buttons(copy) == []
    assert "sb:an:post:" in str(buttons(card))
    message, _ = await posted(env, preset="closed")
    assert buttons(message) == []


# -- Post ----------------------------------------------------------------------------------------------------------


async def test_post_publishes_once_with_the_claim_button_and_audits(env: AnnounceEnv) -> None:
    code = await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    pressed = await post(env, staff, card)
    assert pressed.response_kinds() == ["defer"]
    assert len(env.channel.messages) == 1
    message = env.channel.messages[0]
    embed = message.embeds[0]
    assert embed.title == env.text(preset_key(PRESET_ID, PRESET_TITLE))
    assert buttons(message) == [
        (discount_views.claim_id(code.id), env.text(preset_key(PRESET_ID, PRESET_CLAIM_BUTTON)), False)
    ]
    assert message.content == "" and message.allowed_mentions is not None
    assert message.allowed_mentions.everyone is False and message.allowed_mentions.roles is False
    # The card became the confirmation with a link to the post (and no Post button).
    assert card.embeds[0].title == op(env, "announcements.done.title")
    assert message.jump_url in card.text()
    assert [label for _, label, _ in buttons(card)] == [op(env, "announcements.done.open")]
    stored = (await rows(env))[0]
    assert stored["status"] == "published" and stored["message_id"] == message.id
    assert stored["discount_code_id"] == code.id and stored["preset_id"] == PRESET_ID
    events = await audit_events(env)
    assert [e.action for e in events] == ["announcements.published"]
    assert events[0].details["preset"] == PRESET_ID and events[0].details["coupon"] == "BOGO"
    # A second press of the same Post button posts nothing more.
    again = await post_token(env, staff, token=stored["token"])
    assert len(env.channel.messages) == 1
    assert op(env, "announcements.preset.already", url=stored_url(message)) in again.text()


async def post_token(env: AnnounceEnv, user: Any, *, token: str, app: Any = None) -> FakeMessage:
    card = FakeMessage(env.channel)
    await click(app or env.app, user=user, custom_id=f"sb:an:post:{token}", message=card)
    return card


async def test_double_press_while_posting_posts_once(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    token = token_of(card)
    other = restarted_app(env)  # e.g. the press reaching a second process (separate router, same database)
    await other.resources.refresh()
    same: list[FakeInteraction] = []
    second: list[FakeMessage] = []
    original_send = env.channel.send

    async def send(*args: Any, **kwargs: Any) -> Any:
        # Pressed again while the first press posts: the router drops the same click, the claim refuses the other.
        same.append(await click(env.app, user=staff, custom_id=f"sb:an:post:{token}", message=FakeMessage(None)))
        second.append(await post_token(env, staff, token=token, app=other))
        return await original_send(*args, **kwargs)

    env.channel.send = send  # type: ignore[method-assign]
    await post(env, staff, card)
    assert len(env.channel.messages) == 1
    assert same[0].last_sent is not None and same[0].last_sent.embeds[0].description == env.text(ck.ERROR_BUSY)
    assert op(env, "announcements.error.busy") in second[0].text()
    assert card.embeds[0].title == op(env, "announcements.done.title")


async def test_two_staff_previews_of_the_same_offer_post_once(env: AnnounceEnv) -> None:
    await make_code(env)
    first, second = env.staff("first"), env.staff("second")
    first_card = card_of(await preview(env, first))
    second_card = card_of(await preview(env, second))
    await post(env, first, first_card)
    await post(env, second, second_card)
    assert len(env.channel.messages) == 1
    message = env.channel.messages[0]
    url = stored_url(message)
    assert op(env, "announcements.preset.already_announced", code="BOGO") in second_card.text()
    assert link_urls(second_card) == [url]
    statuses = sorted(r["status"] for r in await rows(env))
    assert statuses == ["draft", "published"]
    # Once the post is deleted in Discord, the offer can be announced again.
    await message.delete()
    card = card_of(await preview(env, second))
    await post(env, second, card)
    assert len(env.channel.messages) == 1 and env.channel.messages[0] is not message


async def test_post_works_after_a_restart(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff, mention="alerts", image=Upload()))
    app = restarted_app(env)
    await app.resources.refresh()
    await post(env, staff, card, app=app)
    message = env.channel.messages[0]
    assert message.content == f"<@&{env.alerts_role.id}>"
    assert [f.filename for f in message.files] == ["announcement.png"]
    assert env.cdn.requests == [CDN_URL]


async def test_only_the_chosen_role_is_mentioned(env: AnnounceEnv) -> None:
    await make_code(env)
    message, _ = await posted(env, mention="alerts")
    mentions = message.allowed_mentions
    assert mentions is not None and mentions.everyone is False and mentions.users is False
    assert [r.id for r in mentions.roles] == [env.alerts_role.id]  # type: ignore[union-attr]
    assert message.content == f"<@&{env.alerts_role.id}>"


async def test_a_coupon_that_ended_before_post_is_refused(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    await discount_codes.end(env.app, actor_id=1, code="BOGO")
    await post(env, staff, card)
    assert env.channel.messages == []
    assert op(env, "announcements.preset.coupon_missing", code="BOGO", command=f"`{COMMAND}`") in card.text()
    assert buttons(card) == []  # run /announce again after creating the code
    assert [r["status"] for r in await rows(env)] == ["draft"]


async def test_a_coupon_created_again_since_the_preview_is_refused(env: AnnounceEnv) -> None:
    """The preview showed the terms of one code; BOGO ended and created again (here as 10% off) is another code, whose
    terms staff never saw: Post refuses it and asks for a new preview."""
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    await discount_codes.end(env.app, actor_id=1, code="BOGO")
    await env.app.db.transaction(
        discount_repo.create_code,
        guild_id=GUILD_ID,
        code="BOGO",
        kind=d.PERCENT,
        value=1000,
        min_subtotal_minor=0,
        max_uses=None,
        once_per_customer=True,
        expires_at=None,
        product_ids=[],
        actor_id=1,
        now=env.tenv.clock.now(),
    )
    await post(env, staff, card)
    assert env.channel.messages == []
    assert op(env, "announcements.preset.coupon_changed", code="BOGO") in card.text()
    assert op(env, "announcements.preset.post_rerun_hint", preset=PRESET_ID) in card.text()
    assert buttons(card) == []
    assert [r["status"] for r in await rows(env)] == ["draft"]
    # A new preview shows the new terms and posts.
    fresh = card_of(await preview(env, staff))
    await post(env, staff, fresh)
    assert len(env.channel.messages) == 1


async def test_a_code_that_ends_while_posting_gets_its_button_switched(env: AnnounceEnv) -> None:
    code = await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    original_send = env.channel.send

    async def send(*args: Any, **kwargs: Any) -> Any:
        # /discount end runs while Discord accepts the post: no announcement is published yet to switch.
        await discount_codes.end(env.app, actor_id=1, code="BOGO")
        return await original_send(*args, **kwargs)

    env.channel.send = send  # type: ignore[method-assign]
    await post(env, staff, card)
    assert op(env, "announcements.preset.ended_while_posting", code="BOGO") in card.text()
    await env.app.scheduler.run_due()
    assert buttons(env.channel.messages[0]) == [(discount_views.claim_id(code.id), ended_label(env), True)]


async def test_the_typed_form_routes_refuse_a_preset_preview(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    token = token_of(card_of(await preview(env, staff)))
    retry = await click(env.app, user=staff, custom_id=f"sb:an:retry:{token}", message=FakeMessage(None))
    assert "modal" not in retry.response_kinds()
    assert retry.last_sent is not None and op(env, "announcements.error.form_unknown") in retry.last_sent.text()
    typed = await submit(env, staff, f"sb:an:submit:{token}", form_fields())
    assert typed.last_sent is not None and op(env, "announcements.error.form_unknown") in typed.last_sent.text()
    assert env.channel.messages == []
    assert [(r["status"], r["title"]) for r in await rows(env)] == [("draft", None)]


async def test_a_server_problem_keeps_post_for_after_the_fix(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=discord.PermissionOverwrite(send_messages=False))
    await post(env, staff, card)
    assert env.channel.messages == []
    assert card.embeds[0].title == op(env, "announcements.error.title")
    assert op(env, "announcements.preset.post_retry_hint") in card.text()
    assert any(cid and cid.startswith("sb:an:post:") for cid, _, _ in buttons(card))
    assert [e.action for e in await audit_events(env)] == ["announcements.failed"]
    await env.channel.set_permissions(env.tenv.guild.me, overwrite=None)
    await post(env, staff, card)
    assert len(env.channel.messages) == 1


async def test_cancel_drops_the_preview(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    token = token_of(card)
    await click(env.app, user=staff, custom_id=f"sb:an:cancel:{token}", message=card)
    assert card.embeds[0].title == op(env, "announcements.preset.cancelled_title") and buttons(card) == []
    assert await rows(env) == []
    late = await post_token(env, staff, token=token)
    assert op(env, "announcements.preset.unknown") in late.text()
    assert env.channel.messages == []


async def test_customers_and_other_staff_cannot_post(env: AnnounceEnv) -> None:
    await make_code(env)
    staff = env.staff()
    card = card_of(await preview(env, staff))
    token = token_of(card)
    customer = await click(env.app, user=env.customer(), custom_id=f"sb:an:post:{token}", message=FakeMessage(None))
    assert customer.last_sent is not None  # refused by the router (staff only)
    other = await post_token(env, env.staff("other"), token=token)
    assert op(env, "announcements.preset.not_owner") in other.text()
    assert env.channel.messages == []


# -- the posted claim button ----------------------------------------------------------------------------------------


async def test_the_claim_button_on_the_post_claims_the_coupon(env: AnnounceEnv) -> None:
    code = await make_code(env)
    message, _ = await posted(env)
    customer = env.customer()
    claim_id = buttons(message)[0][0]
    assert claim_id is not None
    reply = await click(env.app, user=customer, custom_id=claim_id, message=message)
    assert reply.last_sent is not None
    assert reply.last_sent.embeds[0].title == env.text(checkout_keys.CLAIM_TITLE)
    assert await env.app.db.run(discount_repo.claimed, code.id, customer.id) is True


def ended_label(env: AnnounceEnv) -> str:
    return env.text(preset_key(PRESET_ID, PRESET_ENDED_BUTTON))


async def test_discount_end_switches_the_posted_button(env: AnnounceEnv) -> None:
    code = await make_code(env)
    message, _ = await posted(env)
    embeds = list(message.embeds)
    await discount_codes.end(env.app, actor_id=1, code="BOGO")
    await env.app.scheduler.run_due()
    assert buttons(message) == [(discount_views.claim_id(code.id), ended_label(env), True)]
    assert message.embeds == embeds  # only the button changed
    assert message.edits[-1].keys() == {"view"}
    stored = (await rows(env))[0]
    assert stored["offer_ended_at"] is not None
    # Nothing is scheduled again for a post that already shows the ended label.
    await presets.on_offer_ended(env.app, code_id=code.id)
    edits = len(message.edits)
    await env.app.scheduler.run_due()
    assert len(message.edits) == edits


async def test_a_press_after_expiry_answers_offer_ended_and_switches_the_button(env: AnnounceEnv) -> None:
    code = await make_code(env, days=1)
    message, _ = await posted(env)
    env.tenv.clock.advance(DAY)
    reply = await click(env.app, user=env.customer(), custom_id=discount_views.claim_id(code.id), message=message)
    assert reply.last_sent is not None
    assert reply.last_sent.embeds[0].title == env.text(checkout_keys.CLAIM_ENDED_TITLE)
    await env.app.scheduler.run_due()
    assert buttons(message) == [(discount_views.claim_id(code.id), ended_label(env), True)]


async def test_a_switch_that_failed_is_not_restarted_by_every_press(env: AnnounceEnv) -> None:
    """The bot may not edit the post: the switch fails once (one alert); later presses do not try again, while
    /discount end and the startup sweep do."""
    code = await make_code(env, days=1)
    message, _ = await posted(env)

    async def forbidden(**kwargs: Any) -> Any:
        raise http_error(discord.Forbidden, 403, 50001, "Missing Access")

    message.edit = forbidden  # type: ignore[method-assign]
    env.tenv.clock.advance(DAY)
    for number in range(3):
        press = discount_views.claim_id(code.id)
        await click(env.app, user=env.customer(f"[customer {number}]"), custom_id=press, message=message)
        await env.app.scheduler.run_due()
    failed = [e for e in await audit_events(env, prefix="task.") if e.action == "task.failed"]
    assert len(failed) == 1
    await presets.recover(env.app)  # the startup sweep tries again
    await env.app.scheduler.run_due()
    failed = [e for e in await audit_events(env, prefix="task.") if e.action == "task.failed"]
    assert len(failed) == 2


async def test_an_offer_that_ended_while_the_bot_was_down_is_switched_at_startup(env: AnnounceEnv) -> None:
    code = await make_code(env, days=2)
    message, _ = await posted(env)
    # Ended without the event (e.g. the bot stopped first), and another that just expired.
    await env.app.db.transaction(
        discount_repo.end_code, guild_id=GUILD_ID, code="BOGO", actor_id=1, now=env.tenv.clock.now()
    )
    app = restarted_app(env)
    await app.resources.refresh()
    await presets.recover(app)
    await app.scheduler.run_due()
    assert buttons(message) == [(discount_views.claim_id(code.id), ended_label(env), True)]


async def test_a_deleted_post_counts_as_switched(env: AnnounceEnv) -> None:
    await make_code(env)
    message, _ = await posted(env)
    await message.delete()
    await discount_codes.end(env.app, actor_id=1, code="BOGO")
    await env.app.scheduler.run_due()
    stored = (await rows(env))[0]
    assert stored["offer_ended_at"] is not None
    failed = await env.app.db.run(
        lambda conn: conn.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE status != 'done'").fetchone()[0]
    )
    assert failed == 0


async def test_a_used_up_code_keeps_the_button(env: AnnounceEnv) -> None:
    """Used up is not final (a use comes back when an unpaid order is cancelled): the press answers offer ended, the
    button stays."""
    code = await make_code(env, max_uses=1)
    message, _ = await posted(env)
    await env.app.db.run(
        lambda conn: conn.execute(
            "INSERT INTO orders (guild_id, number, user_id, status, currency, subtotal_minor, total_minor, "
            "payment_provider, created_at, updated_at, discount_code_id, discount_code, discount_kind) "
            "VALUES (?, 1, 5, 'pending_payment', 'USD', 100, 100, 'manual', 0, 0, ?, 'BOGO', 'bogo')",
            (GUILD_ID, code.id),
        )
    )
    await click(env.app, user=env.customer(), custom_id=discount_views.claim_id(code.id), message=message)
    await env.app.scheduler.run_due()
    assert buttons(message)[0][2] is False
    assert (await env.app.db.run(repo.get_by_id, (await rows(env))[0]["id"])).offer_ended_at is None  # type: ignore[union-attr]


# -- preview states ---------------------------------------------------------------------------------------------------

PRESET_BANNERS = {
    "preset_warehouse_sale_preview_announcement": "announcements",
    "preset_warehouse_sale_preview": "general-response",
    "preset_warehouse_sale_preview_problem": "error",
    "preset_warehouse_sale_announcement": "announcements",
    "preset_warehouse_sale_announcement_ended": "announcements",
    "preset_closed_preview_announcement": "announcements",
    "preset_closed_preview": "general-response",
    "preset_closed_announcement": "announcements",
    "preset_published": "successful-confirmation",
    "preset_post_error": "error",
    "preset_already_announced": "error",
    "preset_cancelled": "cancellation",
    "preset_notice_unknown": "error",
}


async def test_preview_states_of_the_presets(tmp_path: Path) -> None:
    env = await announce_env(
        tmp_path, PRESETS, content={preset_key(PRESET_ID, PRESET_DESCRIPTION): DESCRIPTION}, banners=True
    )
    try:
        assert MODULE.preview is not None
        states = {state.name: state for state in MODULE.preview(env.app)}
        assert set(PRESET_BANNERS) <= set(states)
        assert "preset_closed_preview_problem" not in states  # no coupon, nothing that can be missing
        for name, banner in PRESET_BANNERS.items():
            spec = states[name].spec
            assert isinstance(spec, MessageSpec)
            assert spec.banner == env.app.ui.banner(banner), name  # type: ignore[arg-type]
            assert check_state(states[name]) == [], name
            assert unrouted_components(states[name], env.app.router) == [], name
        ended = states["preset_warehouse_sale_announcement_ended"].spec
        assert isinstance(ended, MessageSpec)
        button = ended.rows[0][0]
        assert isinstance(button, ButtonSpec) and button.disabled and button.label == ended_label(env)
        copy = states["preset_warehouse_sale_preview_announcement"].spec
        assert isinstance(copy, MessageSpec) and not copy.mention_role_ids
        assert all(isinstance(b, ButtonSpec) and b.disabled for row in copy.rows for b in row)
    finally:
        await env.close()


async def test_a_presets_own_banner_counts_toward_the_image_limit(tmp_path: Path) -> None:
    """The image limit and the attach-files check use the banner the preset is posted with."""
    env = await announce_env(tmp_path, PRESETS, banners=True)
    try:
        settings = env.app.settings
        own = tuple(
            replace(p, banner="general-response") if p.id == PRESET_ID else p for p in settings.announcements.presets
        )
        env.app.settings = replace(settings, announcements=replace(settings.announcements, presets=own))
        assert service.banner_id(env.app, PRESET_ID) == "general-response"
        assert service.banner_id(env.app, "closed") == service.banner_id(env.app) == "announcements"
        general, announcements = env.app.ui.banner("general-response"), env.app.ui.banner("announcements")
        assert general is not None and announcements is not None and general.size != announcements.size
        difference = service.upload_limit(env.app) - service.upload_limit(env.app, "general-response")
        assert difference == general.size - announcements.size
    finally:
        await env.close()
