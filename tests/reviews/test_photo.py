"""The photo of a /review: checks before and after downloading, durable storage through approval and publication
(Discord's links expire, the stored bytes do not), the banner placement, release once nothing posts it any more,
restart safety, and /reset."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import aiohttp

from shopbot.features.reviews import content_keys as k
from shopbot.features.reviews import images, repo, service
from shopbot.features.setup import reset_store
from tests.fakes import FakeMessage, FakeTextChannel, modal_of
from tests.helpers import TestEnv, banner_names, make_banner_env, provision_channels
from tests.reviews.util import invoke, make_order, only_embed, rendered, restarted_app, review_of, reviews_channel
from tests.reviews.write_util import (
    CDN_URL,
    PNG,
    FakeCdn,
    Upload,
    cdn,
    channel_by_key,
    file_names,
    make_write_env,
    reply_of,
    reviews_of,
    run_review,
    send_form,
    stored_image,
    write,
    write_overrides,
)
from tests.reviews_approval.util import APPROVAL_CHANNEL, approve, reject, status_value


def _photo_name(review: repo.Review) -> str:
    return images.attachment_name(review.id, "image/png")


def _image_problem(tenv: TestEnv) -> str:
    limit = images.megabytes(service.photo_limit(tenv.app))
    return rendered(tenv, k.IMAGE_PROBLEM, limit=limit)


def _description(message: FakeMessage) -> str:
    return message.embeds[0].description or ""


# --------------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------------


async def test_a_photo_that_is_not_a_picture_or_too_large_is_refused_before_the_form(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        limit = service.photo_limit(tenv.app)
        for upload in (
            Upload(content_type="application/pdf", filename="menu.pdf"),
            Upload(content_type=None, filename="notes.txt"),
            # Pictures in other formats are refused before the form too, so no description is typed in vain.
            Upload(content_type="image/heic", filename="IMG_0001.HEIC"),
            Upload(content_type="image/svg+xml", filename="logo.svg"),
            Upload(content_type="image/avif", filename="photo.avif"),
            Upload(content_type=None, filename="photo.bmp"),
            Upload(size=limit + 1),
            Upload(size=0),
            Upload(url="https://example.com/photo.png"),  # not a Discord upload
        ):
            refused = await run_review(tenv, customer, image=upload)
            assert modal_of(refused) is None, upload
            assert _description(reply_of(refused)) == _image_problem(tenv), upload
        assert cdn(tenv).requests == []  # nothing was downloaded
        accepted = await run_review(tenv, customer, image=Upload(content_type=None, filename="photo.JPG"))
        assert modal_of(accepted) is not None  # declared by its name only: decided from the bytes later
        for alias in ("image/jpg", "image/pjpeg", "image/webp; charset=binary"):
            assert modal_of(await run_review(tenv, customer, image=Upload(content_type=alias))) is not None, alias
        assert await reviews_of(tenv, customer.id) == []
    finally:
        await tenv.close()


async def test_the_photo_is_checked_again_after_downloading(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await make_order(tenv, customer)
        files = cdn(tenv)
        files.files[CDN_URL] = b"%PDF-1.7 not a picture"
        sent = await write(tenv, customer, image=Upload())
        assert _description(reply_of(sent)) == _image_problem(tenv)
        files.files[CDN_URL] = PNG + b"\0" * service.photo_limit(tenv.app)  # larger than declared and allowed
        assert _description(reply_of(await write(tenv, customer, image=Upload()))) == _image_problem(tenv)
        files.fail = aiohttp.ClientConnectionError()
        assert _description(reply_of(await write(tenv, customer, image=Upload()))) == _image_problem(tenv)
        del files.files[CDN_URL]
        files.fail = None
        assert _description(reply_of(await write(tenv, customer, image=Upload()))) == _image_problem(tenv)
        assert await reviews_of(tenv, customer.id) == []  # nothing was written
    finally:
        await tenv.close()


def test_the_limit_is_shown_in_whole_megabytes() -> None:
    assert images.megabytes(10 * 1024 * 1024 - 700_000) == "9"  # 9.3 MB: never more than the real limit
    assert images.megabytes(8 * 1024 * 1024) == "8"
    assert images.megabytes(512 * 1024) == "0.5"


def test_the_limit_leaves_room_for_the_largest_banner() -> None:
    class Ui:
        def __init__(self, sizes: dict[str, int]) -> None:
            self.sizes = sizes

        def banner(self, banner_id: str) -> Any:
            size = self.sizes.get(banner_id)
            return None if size is None else type("Banner", (), {"size": size})()

    class App:
        guild = type("Guild", (), {"filesize_limit": 25 * 1024 * 1024})()
        ui = Ui({"reviews": 300_000, "pending-review": 500_000})

    limit = images.image_limit(App(), service.PHOTO_BANNERS)  # type: ignore[arg-type]
    assert limit == images.MAX_STORED_BYTES - 500_000
    App.guild = type("Guild", (), {"filesize_limit": 8 * 1024 * 1024})()
    assert images.image_limit(App(), service.PHOTO_BANNERS) == 8 * 1024 * 1024 - 500_000  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------------
# Storage through approval and publication
# --------------------------------------------------------------------------------------------------


async def test_photo_is_stored_and_shown_through_approval_and_publication(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer, "[with a photo]", image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.data == PNG and not stored.released
        assert (stored.content_type, stored.size) == ("image/png", len(PNG))
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        name = _photo_name(review)
        assert name in file_names(approval)
        assert only_embed(approval).image.url == f"attachment://{name}"
        # Days later Discord's link has expired: the bot works from its own copy, also after a restart.
        cdn(tenv).files.clear()
        app = await restarted_app(tenv)
        await approve(tenv, staff, review, approval, app=app)
        (post,) = reviews_channel(tenv).messages
        assert file_names(post) == [name]
        assert only_embed(post).image.url == f"attachment://{name}"
        assert next(f for f in post.files if f.filename == name).fp.getvalue() == PNG  # type: ignore[attr-defined]
        assert name in file_names(approval)  # the decided approval message still shows it
        released = await stored_image(tenv, review.id)
        assert released is not None and released.released and released.data is None
        assert (released.sha256, released.size) == (stored.sha256, stored.size)  # what it was stays known
        # Removing the review later: the approval message loses the photo with the review.
        await invoke(tenv, staff, "remove", review=review.id, app=app)
        assert reviews_channel(tenv).messages == []
        assert name not in file_names(approval)
    finally:
        await tenv.close()


async def test_a_rejected_review_releases_its_photo(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        await reject(tenv, staff, review, approval, "[blurry]")
        assert _photo_name(review) in file_names(approval)  # shown with the decision
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.released
        assert reviews_channel(tenv).messages == []
    finally:
        await tenv.close()


async def test_removing_a_pending_review_releases_its_photo(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        await invoke(tenv, staff, "remove", review=review.id)
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.released
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert _photo_name(review) not in file_names(approval)
    finally:
        await tenv.close()


async def test_a_verified_review_with_a_photo_waits_for_our_team_first(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)  # as shipped: approval off, photo_requires_approval on
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        order = await make_order(tenv, customer)
        sent = await write(tenv, customer, image=Upload())
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        review = await review_of(tenv, order)
        assert review.status == repo.PENDING_APPROVAL and reviews_channel(tenv).messages == []
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert file_names(approval) == [_photo_name(review)]
        await approve(tenv, staff, review, approval)
        (post,) = reviews_channel(tenv).messages
        assert file_names(post) == [_photo_name(review)]
        # Without a photo the same member's next delivered order is published at once.
        second = await make_order(tenv, customer)
        assert _description(reply_of(await write(tenv, customer))) == tenv.text(k.WRITTEN)
        assert (await review_of(tenv, second)).status == repo.PUBLISHED
    finally:
        await tenv.close()


async def test_without_the_photo_check_a_verified_review_with_a_photo_is_published_at_once(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"photo_requires_approval": False})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await write(tenv, customer, image=Upload())
        review = await review_of(tenv, order)
        (post,) = reviews_channel(tenv).messages
        assert file_names(post) == [_photo_name(review)]
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.released
        written = next(e for e in (await _audit(tenv)) if e.action == service.WRITTEN_ACTION)
        assert written.details["photo"] is True
    finally:
        await tenv.close()


async def _audit(tenv: TestEnv) -> list[Any]:
    from tests.reviews.util import audit_actions

    return await audit_actions(tenv)


async def test_the_banner_shows_above_the_photo(tmp_path: Path) -> None:
    tenv = await make_banner_env(tmp_path, write_overrides({"photo_requires_approval": False}))
    try:
        await provision_channels(tenv)
        tenv.bot.http = FakeCdn()  # type: ignore[assignment]
        customer = tenv.member()
        await make_order(tenv, customer)
        await write(tenv, customer, image=Upload())
        await write(tenv, customer)  # no order left, no photo: an unverified review
        (post,) = reviews_channel(tenv).messages
        (review, unverified) = await reviews_of(tenv, customer.id)
        banners = banner_names(post)
        assert len(banners) == 1 and banners[0].startswith("banner-reviews-")
        assert file_names(post) == [banners[0], _photo_name(review)]  # the banner first, then the photo
        assert only_embed(post).image.url == f"attachment://{_photo_name(review)}"  # the photo closes the card
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        assert approval.id == unverified.approval_message_id
        assert only_embed(approval).image.url.startswith("attachment://banner-pending-review-")  # no photo
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Restarts
# --------------------------------------------------------------------------------------------------


def _lose_answer(channel: FakeTextChannel) -> None:
    """The next send creates the message, but the answer is lost (the connection dropped)."""
    send = channel.send
    left = [1]

    async def flaky(content: str | None = None, **kwargs: Any) -> FakeMessage:
        if left[0] <= 0:
            return await send(content, **kwargs)
        left[0] -= 1
        await send(content, **kwargs)
        raise aiohttp.ServerDisconnectedError()

    channel.send = flaky  # type: ignore[method-assign]


async def test_restart_after_a_lost_answer_adopts_the_post_with_its_photo(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"photo_requires_approval": False})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        channel = reviews_channel(tenv)
        _lose_answer(channel)
        await write(tenv, customer, image=Upload())
        (post,) = channel.messages
        pending = await review_of(tenv, order)
        assert pending.submitted and pending.published_message_id is None
        stored = await stored_image(tenv, pending.id)
        assert stored is not None and not stored.released  # still needed: the publication is not recorded

        def lose_retry(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE scheduled_tasks SET status = 'failed' WHERE kind = 'reviews.publish'")

        await tenv.app.db.transaction(lose_retry)  # the process stopped before retrying
        app = await restarted_app(tenv)
        await app.scheduler.run_due()
        assert channel.messages == [post]  # adopted, not posted twice
        published = await review_of(tenv, order, app)
        assert (published.status, published.published_message_id) == (repo.PUBLISHED, post.id)
        released = await stored_image(tenv, pending.id)
        assert released is not None and released.released
    finally:
        await tenv.close()


async def test_restart_while_publishing_is_retried_posts_the_stored_photo(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"photo_requires_approval": False})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        channel = reviews_channel(tenv)
        channel.fail_send = aiohttp.ClientConnectionError()
        await write(tenv, customer, image=Upload())
        assert channel.messages == []
        channel.fail_send = None
        cdn(tenv).files.clear()  # the upload's link expired meanwhile
        app = await restarted_app(tenv)
        await app.scheduler.run_due()
        (post,) = channel.messages
        review = await review_of(tenv, order, app)
        assert review.status == repo.PUBLISHED and file_names(post) == [_photo_name(review)]
    finally:
        await tenv.close()


async def test_photos_left_stored_are_released_at_startup(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path, {"photo_requires_approval": False})
    try:
        customer = tenv.member()
        order = await make_order(tenv, customer)
        await write(tenv, customer, image=Upload())
        review = await review_of(tenv, order)

        def keep(conn: sqlite3.Connection) -> None:  # the process stopped right after recording the publication
            conn.execute("UPDATE review_images SET data = ?, released_at = NULL", (PNG,))

        await tenv.app.db.transaction(keep)
        assert await tenv.app.db.run(images.releasable_ids, tenv.guild.id) == [review.id]
        await restarted_app(tenv)
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.released
    finally:
        await tenv.close()


async def test_a_decision_whose_follow_up_has_not_run_keeps_its_photo_until_it_has(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer, staff = tenv.member(), tenv.member(name="mod", staff=True)
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        # The rejection is committed, then the process stops before its follow-up (approval message, notice) runs.
        decided = await service.decide(tenv.app, review.id, approve=False, actor_id=staff.id, reason="[blurry]")
        assert decided.outcome is repo.Outcome.OK
        assert await tenv.app.db.run(images.releasable_ids, tenv.guild.id) == []  # left to the follow-up
        app = await restarted_app(tenv)  # the startup sweep runs here
        stored = await stored_image(tenv, review.id)
        assert stored is not None and not stored.released
        tenv.clock.advance(3600)
        await app.scheduler.run_due()  # the durable follow-up: the decision is shown with the photo
        assert _photo_name(review) in file_names(approval)
        assert only_embed(approval).fields[-2].value == status_value(tenv, k.STATE_REJECTED, staff.id)
        released = await stored_image(tenv, review.id)
        assert released is not None and released.released  # and only then released
    finally:
        await tenv.close()


async def test_setup_keeps_the_photo_of_a_pending_approval_message_without_uploading_it_again(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await write(tenv, customer, image=Upload())
        (review,) = await reviews_of(tenv, customer.id)
        approval = channel_by_key(tenv, APPROVAL_CHANNEL).messages[-1]
        (shown,) = approval.attachments
        for _ in range(2):  # /setup edits every pending approval message to the current text
            assert await service.requeue_approvals(tenv.app, sync_messages=True) == (1, 0)
        edits = [edit for edit in approval.edits if "attachments" in edit]
        assert edits and all(edit["attachments"] == [shown] for edit in edits)  # kept, never uploaded again
        assert approval.attachments == [shown] and only_embed(approval).image.url == f"attachment://{shown.filename}"
        assert shown.filename == _photo_name(review)
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# /reset
# --------------------------------------------------------------------------------------------------


async def test_reset_wipes_review_photos_with_the_reviews(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        await write(tenv, customer, image=Upload())
        assert reset_store.WIPED_TABLES.index("review_images") < reset_store.WIPED_TABLES.index("reviews")
        counts = await tenv.app.db.transaction(reset_store.wipe_shop_data, tenv.clock.now())
        assert (counts["review_images"], counts["reviews"]) == (1, 1)
        assert await tenv.app.db.run(lambda conn: conn.execute("PRAGMA foreign_key_check").fetchall()) == []
    finally:
        await tenv.close()


async def test_open_form_survives_a_restart(tmp_path: Path) -> None:
    tenv = await make_write_env(tmp_path)
    try:
        customer = tenv.member()
        opened = await run_review(tenv, customer, image=Upload())
        app = await restarted_app(tenv)
        app.bot.http = FakeCdn()  # type: ignore[union-attr]
        sent = await send_form(tenv, customer, opened, app=app)
        assert _description(reply_of(sent)) == tenv.text(k.WRITTEN_PENDING)
        (review,) = await reviews_of(tenv, customer.id)
        stored = await stored_image(tenv, review.id)
        assert stored is not None and stored.data == PNG
    finally:
        await tenv.close()
