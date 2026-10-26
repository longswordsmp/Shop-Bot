"""/review for every member: the command and its options, which review it writes (verified for the newest delivered
order without a finished review, otherwise unverified), the unverified cooldown, approval forced for unverified
reviews, anonymity of the published review, the form and its refusals."""

from __future__ import annotations

import asyncio
from pathlib import Path

import discord

from shopbot.config.content import validate_value
from shopbot.features import core_keys as ck
from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import repo, service, views
from tests.fakes import click, modal_of, submit_modal
from tests.helpers import make_settings, make_test_env
from tests.reviews.util import (
    audit_actions,
    make_order,
    number_text,
    only_embed,
    prompt_now,
    prompted,
    published_title,
    rendered,
    reply_embed,
    review_of,
    reviews_channel,
    task,
)
from tests.reviews.write_util import (
    Upload,
    channel_by_key,
    make_write_env,
    reply_of,
    review_command,
    reviews_of,
    run_review,
    send_form,
    write,
)
from tests.reviews_approval.util import APPROVAL_CHANNEL, approve, field_pairs, reject

DAY = 86_400


def _description(message: object) -> str:
    embeds = getattr(message, "embeds", [])
    assert embeds
    return embeds[0].description or ""


# --------------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------------


async def test_every_member_sees_review_with_stars_and_an_optional_photo(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        command = review_command(tenv.app)
        assert command.default_permissions is None  # every member, unlike the staff group /reviews
        assert command.guild_only is True
        assert command.description == tenv.text(k.COMMAND_DESCRIPTION)
        stars, photo = command.parameters
        assert (stars.name, stars.required, stars.type) == ("stars", True, discord.AppCommandOptionType.integer)
        assert stars.description == tenv.text(k.COMMAND_STARS)
        assert [c.value for c in stars.choices] == [5, 4, 3, 2, 1]
        assert [c.name for c in stars.choices] == [
            f"{tenv.text(k.rating_key(n, 'label'))} {'★' * n}{'☆' * (5 - n)}" for n in (5, 4, 3, 2, 1)
        ]
        # Every customer text calls it a photo, so Discord shows the option as "photo".
        assert (photo.display_name, photo.required, photo.type) == (
            "photo",
            False,
            discord.AppCommandOptionType.attachment,
        )
        assert photo.description == tenv.text(k.COMMAND_IMAGE)
        assert command.to_dict(tenv.app.bot.tree)["options"][1]["name"] == "photo"
        assert review_command(tenv.app).checks == []  # no staff check: any member may use it
    finally:
        await tenv.close()


async def test_choice_names_are_plain_text(tmp_path: Path) -> None:
    labels = {k.rating_key(5, "label"): "{icon_star} Excellent"}
    tenv = await make_write_env(tmp_path, content=labels)
    try:
        (stars, _image) = review_command(tenv.app).parameters
        assert stars.choices[0].name == "Excellent ★★★★★"  # an icon cannot show in a choice: left out
    finally:
        await tenv.close()


async def test_review_is_not_registered_without_its_texts(tmp_path: Path) -> None:
    for name, content in (("no_text", {k.COMMAND_DESCRIPTION: ""}), ("no_label", {k.rating_key(2, "label"): ""})):
        tenv = await make_test_env(tmp_path / name, content=content)
        try:
            assert tenv.app.bot is not None
            assert tenv.app.bot.tree.get_command("review", guild=tenv.app.guild_object) is None
            assert tenv.app.bot.tree.get_command("reviews", guild=tenv.app.guild_object) is not None
        finally:
            await tenv.close()
    off = await make_test_env(tmp_path / "off", settings=make_settings({"features": {"reviews": False}}))
    try:
        assert off.app.bot is not None
        assert off.app.bot.tree.get_command("review", guild=off.app.guild_object) is None
    finally:
        await off.close()


async def test_review_answers_unavailable_while_reviews_are_not_ready(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        tenv.app.content.replace(file_values={**tenv.content, k.THANKS: ""})
        tenv.app.recompute_readiness()
        opened = await run_review(tenv, tenv.member())
        assert modal_of(opened) is None
        assert _description(reply_of(opened)) == tenv.text(ck.ERROR_UNAVAILABLE)
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Which review: verified or unverified
# --------------------------------------------------------------------------------------------------


async def test_verified_review_of_the_newest_delivered_order(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member(name="ada")
        older = await make_order(tenv, customer)
        newest = await make_order(tenv, customer)
        await make_order(tenv, customer, status="shipped")  # not delivered yet: never reviewed
        opened = await run_review(tenv, customer, stars=4)
        modal = modal_of(opened)
        assert modal is not None and modal.title == tenv.text(k.WRITE_TITLE)
        assert opened.response_kinds() == ["send_modal"]
        sent = await send_form(tenv, customer, opened, "  Fast *delivery* <@1>  ")
        reply = reply_of(sent)
        assert _description(reply) == tenv.text(k.WRITTEN)
        review = await review_of(tenv, newest)
        assert (review.status, review.rating, review.comment, review.source) == (
            repo.PUBLISHED,
            4,
            "Fast *delivery* <@1>",
            repo.SOURCE_COMMAND,
        )
        (post,) = reviews_channel(tenv).messages
        card = only_embed(post)
        assert card.title == published_title(tenv, rating=4)
        assert card.description == "> Fast \\*delivery\\* <@1>"
        number = number_text(tenv, newest.number)
        assert field_pairs(card)[-1] == (
            tenv.text(k.PUBLISHED_VERIFIED_FIELD),
            rendered(tenv, k.PUBLISHED_VERIFIED_VALUE, order_number=number),
        )
        assert channel_by_key(tenv, APPROVAL_CHANNEL).messages == []  # verified, approval off: published at once
        written = next(e for e in await audit_actions(tenv) if e.action == service.WRITTEN_ACTION)
        assert written.actor_id == customer.id and written.details["verified"] is True
        # The next /review reviews the older delivered order, then (none left) an unverified review.
        await write(tenv, customer, stars=5)
        assert (await review_of(tenv, older)).status == repo.PUBLISHED
        third = await write(tenv, customer, stars=3)
        assert _description(reply_of(third)) == tenv.text(k.WRITTEN_PENDING)
        unverified = (await reviews_of(tenv, customer.id))[-1]
        assert (unverified.order_id, unverified.status, unverified.approval) == (None, repo.PENDING_APPROVAL, None)
    finally:
        await tenv.close()


async def test_review_finishes_an_open_review_request_of_that_order(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"prompt_via": "channel"})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await prompt_now(tenv, order)
        assert order.channel is not None
        (request,) = order.channel.messages
        request_review = await review_of(tenv, order)
        await write(tenv, customer, stars=5)
        review = await review_of(tenv, order)
        assert review.id == request_review.id  # the same review, finished with /review
        assert (review.status, review.source) == (repo.PUBLISHED, repo.SOURCE_COMMAND)
        number = number_text(tenv, order.number)
        assert only_embed(request).description == rendered(tenv, k.THANKS, order_number=number)
        assert request.view is None
        late = await click(tenv.app, user=customer, custom_id=views.rate_id(review.id), values=["1"])
        assert reply_embed(late).description == rendered(tenv, k.ALREADY_REVIEWED, order_number=number)
        assert len(reviews_channel(tenv).messages) == 1
    finally:
        await tenv.close()


async def test_a_scheduled_review_request_is_cancelled(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"prompt_delay_minutes": 60})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await service.schedule_review_prompt(tenv.app, order.id)
        await write(tenv, customer)
        scheduled = await task(tenv, service.prompt_dedupe(order.id))
        assert scheduled is not None and scheduled.status == "cancelled"
        tenv.clock.advance(3600)
        await tenv.app.scheduler.run_due()
        assert customer.dms == []
    finally:
        await tenv.close()


async def test_a_finished_or_closed_request_is_not_reviewed_again(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"allow_unverified": False})
    try:
        customer = tenv.member()
        _order, review, dm = await prompted(tenv, customer)
        await click(tenv.app, user=customer, custom_id=views.decline_id(review.id), message=dm, dm=True)
        refused = await run_review(tenv, customer)  # declined (or removed by staff) counts as closed
        assert modal_of(refused) is None
        assert _description(reply_of(refused)) == tenv.text(k.NO_ORDER)
    finally:
        await tenv.close()


async def test_two_forms_at_once_review_one_order_once(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        first, second = await run_review(tenv, customer), await run_review(tenv, customer)
        await asyncio.gather(send_form(tenv, customer, first), send_form(tenv, customer, second))
        written = await reviews_of(tenv, customer.id)
        assert sorted(r.order_id is None for r in written) == [False, True]  # one verified, one unverified
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Unverified reviews: always approved by staff, and the cooldown
# --------------------------------------------------------------------------------------------------


async def test_unverified_review_always_waits_for_staff(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        assert tenv.app.settings.reviews.require_approval is False
        customer, staff = tenv.member(name="ada"), tenv.member(name="mod", staff=True)
        sent = await write(tenv, customer, "[back again]", stars=5)
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        assert reviews_channel(tenv).messages == []
        (review,) = await reviews_of(tenv, customer.id)
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert approval.id == review.approval_message_id
        card = only_embed(approval)
        assert card.title == rendered(
            tenv, k.APPROVAL_TITLE, stars="★★★★★", rating=5, scale=5, user_name="ada", review_number=review.id
        )
        assert field_pairs(card)[1] == (tenv.text(k.APPROVAL_ORDER_FIELD), tenv.text(k.APPROVAL_NO_ORDER))
        assert (tenv.text(k.APPROVAL_CUSTOMER_FIELD), f"<@{customer.id}>") in field_pairs(card)  # staff see who
        await approve(tenv, staff, review, approval)
        (post,) = reviews_channel(tenv).messages
        published = only_embed(post)
        assert published.title == published_title(tenv, rating=5)
        assert all(name != tenv.text(k.PUBLISHED_VERIFIED_FIELD) for name, _ in field_pairs(published))  # no marker
        notice = customer.dms[-1]  # by direct message: there is no order channel
        link = service.message_link(tenv.app, reviews_channel(tenv).id, post.id)
        expected = rendered(tenv, k.notice_key("approved", "description"), review_link=link)
        assert only_embed(notice).description == expected
    finally:
        await tenv.close()


async def test_staff_decide_an_unverified_review_by_its_number(tmp_path: Path) -> None:
    from tests.reviews.util import invoke

    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer)
        (review,) = await reviews_of(tenv, customer.id)
        listing = await invoke(tenv, staff, "list", status="pending_approval")
        assert tenv.app.op("reviews.list.no_order") in (reply_embed(listing).description or "")
        customer.dm_forbidden = True
        rejected = await invoke(tenv, staff, "reject", review=review.id, reason="[off topic]")
        label = service.review_label(tenv.app, review)
        assert label == tenv.app.op("reviews.ref.no_order", review_number=review.id)
        assert reply_embed(rejected).description == "\n".join(
            [tenv.app.op("reviews.decision.rejected", review=label), tenv.app.op("reviews.decision.notice.none")]
        )
        assert (await reviews_of(tenv, customer.id))[0].status == repo.REJECTED
    finally:
        await tenv.close()


async def test_unverified_cooldown(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"unverified_cooldown_days": 30})
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer)
        (first,) = await reviews_of(tenv, customer.id)
        await reject(tenv, staff, first, channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1])  # rejected ones count
        tenv.clock.advance(29 * DAY)
        refused = await run_review(tenv, customer)
        assert modal_of(refused) is None
        until = first.created_at + 30 * DAY
        assert _description(reply_of(refused)) == rendered(tenv, k.COOLDOWN, date=f"<t:{until}:D>")
        # A delivered order can always be reviewed, cooldown or not.
        order = await make_order(tenv, customer)
        await write(tenv, customer)
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
        tenv.clock.advance(DAY)
        again = await write(tenv, customer)
        assert _description(reply_of(again)) == tenv.text(k.WRITTEN_PENDING)
        assert len([r for r in await reviews_of(tenv, customer.id) if r.order_id is None]) == 2
        other = tenv.member(name="other")  # per member
        assert modal_of(await run_review(tenv, other)) is not None
    finally:
        await tenv.close()


async def test_cooldown_checked_again_when_the_form_is_sent(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"unverified_cooldown_days": 0})
    try:
        customer = tenv.member()
        first, second = await run_review(tenv, customer), await run_review(tenv, customer)
        await send_form(tenv, customer, first)
        late = await send_form(tenv, customer, second)
        assert _description(reply_of(late)) == tenv.text(k.LIMIT)  # cooldown 0: one unverified review, ever
        assert len(await reviews_of(tenv, customer.id)) == 1
        tenv.clock.advance(3650 * DAY)
        assert _description(reply_of(await run_review(tenv, customer))) == tenv.text(k.LIMIT)
    finally:
        await tenv.close()


async def test_without_unverified_reviews_only_delivered_orders_are_reviewed(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"allow_unverified": False})
    try:
        customer = tenv.member()
        refused = await run_review(tenv, customer)
        assert modal_of(refused) is None
        assert _description(reply_of(refused)) == tenv.text(k.NO_ORDER)
        await make_order(tenv, customer)
        assert _description(reply_of(await write(tenv, customer))) == tenv.text(k.WRITTEN)
    finally:
        await tenv.close()


async def test_verified_review_waits_while_approval_is_on(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"require_approval": True})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        sent = await write(tenv, customer)
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        review = await review_of(tenv, order)
        assert review.status == repo.PENDING_APPROVAL and reviews_channel(tenv).messages == []
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert field_pairs(only_embed(approval))[1][1] == f"#{number_text(tenv, order.number)}"
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Anonymity
# --------------------------------------------------------------------------------------------------


async def test_published_reviews_never_identify_the_member(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(name="ada_lovelace"), tenv.member(name="mod", staff=True)
        await make_order(tenv, customer)
        await write(tenv, customer, "[verified text]", image=Upload())
        await write(tenv, customer, "[unverified text]", image=Upload())
        (verified, unverified) = await reviews_of(tenv, customer.id)
        # Both have a photo, so both wait for our team first (as shipped).
        for review in (verified, unverified):
            current = await tenv.app.db.run(repo.get_review, review.id)
            assert current is not None and current.approval_message_id is not None
            message = next(
                m for m in channel_by_key(tenv, APPROVAL_CHANNEL).messages if m.id == current.approval_message_id
            )
            await approve(tenv, staff, current, message)
        posts = reviews_channel(tenv).messages
        assert len(posts) == 2
        for post in posts:
            shown = post.text() + post.content
            for identifying in ("ada_lovelace", "ada\\_lovelace", str(customer.id), "Ada_Lovelace"):
                assert identifying not in shown
            assert all("Ada" not in name for name in (f.filename for f in post.files))  # never the upload's name
            card = only_embed(post)
            assert str(customer.id) not in (card.author.icon_url or "") + (card.thumbnail.url or "")
            assert post.allowed_mentions is not None and post.allowed_mentions.users is False
    finally:
        await tenv.close()


async def test_a_published_title_cannot_name_the_member(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        definition = tenv.app.content.registry.get(k.PUBLISHED_TITLE)
        assert definition is not None and "user_name" not in definition.placeholders
        problems = validate_value(definition, "Review by {user_name}")
        assert any("user_name" in problem for problem in problems)
        verified = tenv.app.content.registry.get(k.PUBLISHED_VERIFIED_VALUE)
        assert verified is not None and validate_value(verified, "{user_name} bought order #{order_number}")
    finally:
        await tenv.close()
    named = await make_write_env(tmp_path / "named", content={k.PUBLISHED_TITLE: "Review by {user_name}"})
    try:
        status = named.app.readiness.statuses[k.REVIEWS]
        assert not status.ready  # nothing is published with that title
        assert any(p.key == k.PUBLISHED_TITLE for p in status.problems)
    finally:
        await named.close()


# --------------------------------------------------------------------------------------------------
# The form
# --------------------------------------------------------------------------------------------------


async def test_the_form(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"comment_max_length": 800})
    try:
        opened = await run_review(tenv, tenv.member())
        modal = modal_of(opened)
        assert modal is not None
        text_input = next(i for i in modal.walk_children() if isinstance(i, discord.ui.TextInput))
        assert text_input.custom_id == views.DESCRIPTION_INPUT_ID
        assert text_input.required is True and text_input.max_length == 800
        assert text_input.style is discord.TextStyle.paragraph
        assert text_input.placeholder == tenv.text(k.WRITE_PLACEHOLDER)
    finally:
        await tenv.close()


async def test_form_refusals(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, other = tenv.member(), tenv.member(name="other")
        for _ in range(3):
            await make_order(tenv, customer)  # each /review below has a delivered order to review
        expired = tenv.text(k.FORM_EXPIRED)
        # someone else's form
        opened = await run_review(tenv, customer)
        assert _description(reply_of(await send_form(tenv, other, opened))) == expired
        # sent twice: the second finds nothing
        await send_form(tenv, customer, opened)
        assert _description(reply_of(await send_form(tenv, customer, opened))) == expired
        # too late
        late = await run_review(tenv, customer)
        tenv.clock.advance(service.FORM_TTL_SECONDS + 1)
        assert _description(reply_of(await send_form(tenv, customer, late))) == expired
        # a forged form id
        forged = await submit_modal(
            tenv.app, user=customer, custom_id=views.write_id("x"), fields={views.DESCRIPTION_INPUT_ID: "[x]"}
        )
        assert _description(reply_of(forged)) == expired
        # an empty description
        blank = await send_form(tenv, customer, await run_review(tenv, customer), "   ")
        assert _description(reply_of(blank)) == tenv.text(ck.ERROR_INVALID_INPUT)
        assert len(await reviews_of(tenv, customer.id)) == 1
    finally:
        await tenv.close()


async def test_a_description_that_cannot_be_shown_is_refused(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"comment_max_length": 4000})
    try:
        customer = tenv.member()
        sent = await write(tenv, customer, "*" * 4000)  # escaping doubles it: more than an embed can show
        assert _description(reply_of(sent)) == rendered(tenv, k.TOO_LONG, max=4000)
        assert await reviews_of(tenv, customer.id) == []
        forged = await write(tenv, customer, "x" * 4001)  # longer than the form allows
        assert _description(reply_of(forged)) == rendered(tenv, k.TOO_LONG, max=4000)
        assert await reviews_of(tenv, customer.id) == []
    finally:
        await tenv.close()
