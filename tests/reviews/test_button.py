"""Write a review: the intro panel's button for members who cannot type /review in the read-only reviews channel. Its
private stars menu, the review form with its photo upload, verified and unverified reviews, the cooldown, and the
refusals."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import images, repo, service, views
from tests.fakes import FakeMessage, modal_of
from tests.reviews.util import make_order, only_embed, rendered, review_of, reviews_channel, select_of
from tests.reviews.write_util import (
    PNG,
    Upload,
    cdn,
    channel_by_key,
    file_names,
    make_write_env,
    pick,
    press_write,
    reply_of,
    reviews_of,
    run_review,
    send_button_form,
    stored_image,
    upload_fields,
    write_with_button,
)
from tests.reviews_approval.util import APPROVAL_CHANNEL, approve

DAY = 86_400


def _description(message: FakeMessage) -> str:
    embeds = getattr(message, "embeds", [])
    assert embeds
    return embeds[0].description or ""


# --------------------------------------------------------------------------------------------------
# The button and its stars menu
# --------------------------------------------------------------------------------------------------


async def test_the_intro_panel_button_answers_privately_with_the_stars_menu(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        await service.publish_intro(tenv.app)
        (panel,) = reviews_channel(tenv).messages
        pressed = await press_write(tenv, tenv.member(), panel=panel)
        menu = reply_of(pressed)
        assert pressed.response_kinds() == ["send_message"]
        assert _description(menu) == tenv.text(k.PICK_DESCRIPTION)
        select = select_of(menu)
        assert select.custom_id == views.pick_id()
        assert select.placeholder == tenv.text(k.PICK_PLACEHOLDER)
        assert [option.value for option in select.options] == ["5", "4", "3", "2", "1"]  # highest first
        assert [option.label for option in select.options] == [
            tenv.text(k.rating_key(n, "label")) for n in (5, 4, 3, 2, 1)
        ]
        assert reviews_channel(tenv).messages == [panel]  # nothing is posted in the channel
    finally:
        await tenv.close()


async def test_stars_open_the_form_with_an_optional_photo_upload(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        menu = reply_of(await press_write(tenv, customer))
        opened = await pick(tenv, customer, menu, 4)
        assert opened.response_kinds() == ["send_modal"]
        modal = modal_of(opened)
        assert modal is not None and modal.title == tenv.text(k.WRITE_TITLE)
        (upload,) = upload_fields(modal)
        assert upload.custom_id == views.PHOTO_INPUT_ID
        assert (upload.required, upload.min_values, upload.max_values) == (False, 0, 1)
        labels = [item for item in modal.walk_children() if isinstance(item, discord.ui.Label)]
        assert [label.text for label in labels] == [tenv.text(k.WRITE_LABEL), tenv.text(k.WRITE_PHOTO_LABEL)]
        assert labels[1].description == tenv.text(k.WRITE_PHOTO_DESCRIPTION)
        # /review with its photo option: the photo is already added, so the form asks only for the description.
        with_photo = modal_of(await run_review(tenv, customer, image=Upload()))
        assert with_photo is not None and upload_fields(with_photo) == []
        without_photo = modal_of(await run_review(tenv, customer))
        assert without_photo is not None and len(upload_fields(without_photo)) == 1
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Verified and unverified reviews
# --------------------------------------------------------------------------------------------------


async def test_a_verified_review_without_a_photo_is_published_at_once(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member(name="ada")
        order = await make_order(tenv, customer)
        sent = await write_with_button(tenv, customer, "[fast delivery]", stars=4)
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN)
        review = await review_of(tenv, order)
        assert (review.status, review.rating, review.comment, review.source) == (
            repo.PUBLISHED,
            4,
            "[fast delivery]",
            repo.SOURCE_COMMAND,
        )
        (post,) = reviews_channel(tenv).messages
        card = only_embed(post)
        assert card.description == "> [fast delivery]"
        assert "ada" not in str(card.to_dict()) and post.files == []  # never the member's name
        assert channel_by_key(tenv, APPROVAL_CHANNEL).messages == []
    finally:
        await tenv.close()


async def test_a_photo_uploaded_in_the_form_is_checked_by_our_team_and_published(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)  # as shipped: photos are checked first
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        order = await make_order(tenv, customer)
        sent = await write_with_button(tenv, customer, "[with a photo]", stars=5, photo=Upload())
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        review = await review_of(tenv, order)
        assert review.status == repo.PENDING_APPROVAL and reviews_channel(tenv).messages == []
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.data == PNG
        name = images.attachment_name(review.id, "image/png")  # never the uploaded file's name
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert file_names(approval) == [name]
        await approve(tenv, staff, review, approval)
        (post,) = reviews_channel(tenv).messages
        assert file_names(post) == [name]
        assert only_embed(post).image.url == f"attachment://{name}"
    finally:
        await tenv.close()


async def test_without_an_order_the_review_is_unverified_and_waits_for_our_team(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        sent = await write_with_button(tenv, customer, "[back again]", stars=5)
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        (review,) = await reviews_of(tenv, customer.id)
        assert (review.order_id, review.status, review.verified) == (None, repo.PENDING_APPROVAL, False)
        assert channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1].id == review.approval_message_id
        assert reviews_channel(tenv).messages == []
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------------


async def test_the_cooldown_answers_instead_of_the_stars_menu(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"unverified_cooldown_days": 30})
    try:
        customer = tenv.member()
        stale_menu = reply_of(await press_write(tenv, customer))  # opened before the first review was sent
        await write_with_button(tenv, customer)
        (first,) = await reviews_of(tenv, customer.id)
        tenv.clock.advance(DAY)
        until = first.created_at + 30 * DAY
        expected = rendered(tenv, k.COOLDOWN, date=f"<t:{until}:D>")
        refused = await press_write(tenv, customer)
        assert _description(reply_of(refused)) == expected
        assert reply_of(refused).components() == []  # no stars menu
        # The menu opened earlier: the stars are checked again, and the menu shows why instead of opening the form.
        late = await pick(tenv, customer, stale_menu, 5)
        assert modal_of(late) is None and late.response_kinds() == ["edit_message"]
        assert _description(stale_menu) == expected and stale_menu.components() == []
        # A delivered order can always be reviewed.
        order = await make_order(tenv, customer)
        assert _description(reply_of(await write_with_button(tenv, customer))) == tenv.text(k.WRITTEN)
        assert (await review_of(tenv, order)).status == repo.PUBLISHED
    finally:
        await tenv.close()


async def test_without_unverified_reviews_a_member_without_an_order_is_told_why(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"allow_unverified": False})
    try:
        refused = await press_write(tenv, tenv.member())
        assert _description(reply_of(refused)) == tenv.text(k.NO_ORDER)
        assert reply_of(refused).components() == []
    finally:
        await tenv.close()


async def test_a_refused_photo_shows_the_text_back_and_writes_nothing(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await make_order(tenv, customer)
        limit = images.megabytes(service.photo_limit(tenv.app))
        for photo in (
            Upload(content_type="application/pdf", filename="menu.pdf"),
            Upload(content_type="image/heic", filename="IMG_0001.HEIC"),
            Upload(size=service.photo_limit(tenv.app) + 1),
            Upload(url="https://example.com/photo.png"),  # not a Discord upload
        ):
            tenv.app.router.reset_limits()  # many clicks in a row: not the rate limit under test
            sent = await write_with_button(tenv, customer, "[my *long* review]", photo=photo)
            card = only_embed(reply_of(sent))
            assert card.description == rendered(tenv, k.IMAGE_PROBLEM, limit=limit), photo
            assert [(f.name, f.value) for f in card.fields] == [
                (tenv.text(k.IMAGE_PROBLEM_TEXT_FIELD), "[my \\*long\\* review]")
            ]
        assert cdn(tenv).requests == []  # refused before downloading
        cdn(tenv).files[Upload().url] = b"%PDF-1.7 declared as a picture"
        tenv.app.router.reset_limits()
        sent = await write_with_button(tenv, customer, "[my review]", photo=Upload())
        assert only_embed(reply_of(sent)).fields[0].value == "[my review]"
        assert await reviews_of(tenv, customer.id) == []  # nothing was written
    finally:
        await tenv.close()


async def test_the_form_belongs_to_the_member_who_opened_it(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, other = tenv.member(), tenv.member(name="other")
        opened = await pick(tenv, customer, reply_of(await press_write(tenv, customer)), 5)
        sent = await send_button_form(tenv, other, opened)
        assert _description(reply_of(sent)) == tenv.text(k.FORM_EXPIRED)
        assert await reviews_of(tenv, other.id) == [] and await reviews_of(tenv, customer.id) == []
        again = await send_button_form(tenv, customer, opened)
        assert _description(reply_of(again)) == tenv.text(k.WRITTEN_PENDING)
        assert _description(reply_of(await send_button_form(tenv, customer, opened))) == tenv.text(k.FORM_EXPIRED)
    finally:
        await tenv.close()
