"""/product add image:<file>: the picture is saved to the new product once its form is submitted."""

from __future__ import annotations

from shopbot.features.catalog import images, repo, service
from tests.catalog.test_commands import FORM
from tests.catalog.test_images import FakeUpload, png
from tests.catalog.util import invoke, listing_channel
from tests.fakes import FakeInteraction, modal_of, submit_modal
from tests.helpers import GUILD_ID, TestEnv


def _description(interaction: FakeInteraction) -> str:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].description or ""


async def _add(tenv: TestEnv, upload: FakeUpload | None) -> tuple[FakeInteraction, str]:
    admin = tenv.member(admin=True)
    command = await invoke(tenv, admin, "add", image=upload)
    modal = modal_of(command)
    assert modal is not None
    submitted = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    return submitted, modal.custom_id


async def _product(tenv: TestEnv) -> repo.Product:
    [product] = await tenv.app.db.run(repo.list_products, GUILD_ID)
    return product


async def test_the_picture_given_with_add_is_saved_to_the_new_product(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    upload = FakeUpload(png())
    submitted, custom_id = await _add(tenv, upload)
    assert custom_id.endswith(":1")  # the form knows a picture came with the command
    product = await _product(tenv)
    stored = await tenv.app.db.run(images.get_image, GUILD_ID, product.id)
    assert stored is not None and stored.data == upload.data
    assert tenv.app.op("catalog.cmd.add_image_saved") in _description(submitted)
    # The product's card in #catalog shows the picture.
    card = channel.messages[-1]
    assert [f.filename for f in card.files] == [stored.attachment_name]


async def test_add_without_a_picture_is_unchanged(tenv: TestEnv) -> None:
    submitted, custom_id = await _add(tenv, None)
    assert not custom_id.endswith(":1")
    product = await _product(tenv)
    assert await tenv.app.db.run(images.get_image, GUILD_ID, product.id) is None
    assert tenv.app.op("catalog.cmd.add_image_saved") not in _description(submitted)


async def test_a_refused_picture_still_creates_the_product_and_says_why(tenv: TestEnv) -> None:
    submitted, _ = await _add(tenv, FakeUpload(b"not an image", filename="notes.txt", content_type="text/plain"))
    product = await _product(tenv)
    assert await tenv.app.db.run(images.get_image, GUILD_ID, product.id) is None
    text = _description(submitted)
    assert tenv.app.op("catalog.cmd.image_type") in text
    assert f"/product image product_id:{product.id}" in text


async def test_a_picture_no_longer_held_is_reported(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    command = await invoke(tenv, admin, "add", image=FakeUpload(png()))
    modal = modal_of(command)
    assert modal is not None
    tenv.clock.advance(service.ADD_IMAGE_SECONDS + 1)  # the form stayed open too long (or the bot restarted)
    submitted = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    product = await _product(tenv)
    assert await tenv.app.db.run(images.get_image, GUILD_ID, product.id) is None
    assert tenv.app.op("catalog.cmd.add_image_lost", product_id=product.id) in _description(submitted)


async def test_a_repeated_submission_does_not_save_the_picture_again(tenv: TestEnv) -> None:
    upload = FakeUpload(png())
    admin = tenv.member(admin=True)
    command = await invoke(tenv, admin, "add", image=upload)
    modal = modal_of(command)
    assert modal is not None
    await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    again = await submit_modal(tenv.app, user=admin, custom_id=modal.custom_id, fields=FORM)
    product = await _product(tenv)
    assert upload.reads == 1
    assert again.last_sent is not None
    assert again.last_sent.embeds[0].title == tenv.app.op("catalog.cmd.already_created", product_id=product.id)
