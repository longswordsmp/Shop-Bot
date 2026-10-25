"""Uploaded product images: validation, storage, attachment on listing messages and private browsing, edits that
keep the attachment, new images re-rendering, clearing, restarts, lazy loading, and the generic file support."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.events import PRODUCT_CHANGED
from shopbot.core.panels import spec_hash
from shopbot.features.catalog import MODULE, images, repo, service, views
from shopbot.features.catalog import content_keys as keys
from shopbot.ui.discord_render import RenderError, edit_kwargs, send_kwargs
from shopbot.ui.limits import FILES_TOTAL_BYTES, validate_message
from shopbot.ui.spec import EmbedSpec, FileSpec, MessageSpec
from tests.catalog.util import add_product, audit_actions, get, invoke, listing_channel, restarted_app
from tests.fakes import FakeMessage, click, http_error
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env


def png(shade: int = 0x80, size: int = 4) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes([shade]) * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


class FakeUpload:
    """Stands in for ``discord.Attachment`` (filename, size, content_type, read())."""

    def __init__(
        self,
        data: bytes,
        *,
        filename: str = "photo.png",
        content_type: str | None = "image/png",
        size: int | None = None,
        fail: BaseException | None = None,
    ) -> None:
        self.data = data
        self.filename = filename
        self.content_type = content_type
        self.size = len(data) if size is None else size
        self.fail = fail
        self.reads = 0

    async def read(self) -> bytes:
        self.reads += 1
        if self.fail is not None:
            raise self.fail
        return self.data


def _file_names(message: FakeMessage) -> list[str]:
    return [f.filename for f in message.files]


def _file_bytes(message: FakeMessage) -> list[bytes]:
    out = []
    for item in message.files:
        item.fp.seek(0)
        out.append(item.fp.read())
    return out


async def _stored(tenv: TestEnv, product_id: int) -> images.ProductImage | None:
    return await tenv.app.db.run(images.get_image, GUILD_ID, product_id)


async def _upload(tenv: TestEnv, product_id: int, upload: FakeUpload) -> Any:
    return await invoke(tenv, tenv.member(admin=True), "image", product_id=product_id, file=upload)


def _title(interaction: Any) -> str | None:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].title


def _description(interaction: Any) -> str | None:
    assert interaction.last_sent is not None
    return interaction.last_sent.embeds[0].description


# --------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(png(), "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
async def test_accepted_image_types_are_detected_from_the_bytes(data: bytes, content_type: str) -> None:
    upload = FakeUpload(data, content_type=None)  # Discord did not declare a type: the bytes decide
    image = await images.read_upload(upload)
    assert image.content_type == content_type and image.sha256 == hashlib.sha256(data).hexdigest()
    assert image.data == data and image.size == len(data)


async def test_rejections_before_and_after_download() -> None:
    text = FakeUpload(b"hello", filename="notes.txt", content_type="text/plain")
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(text)
    assert exc.value.reason == "type" and text.reads == 0  # refused without downloading

    huge = FakeUpload(png(), size=images.MAX_IMAGE_BYTES + 1)
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(huge)
    assert exc.value.reason == "size" and huge.reads == 0

    jpg_label = FakeUpload(JPEG, content_type="image/jpg")  # an unusual image label: the bytes decide
    assert (await images.read_upload(jpg_label)).content_type == "image/jpeg"

    svg = FakeUpload(b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml")
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(svg)
    assert exc.value.reason == "type" and svg.reads == 1  # downloaded, then refused by its bytes

    lying = FakeUpload(b"MZ\x90\x00 not an image", content_type="image/png")  # declared image, bytes are not
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(lying)
    assert exc.value.reason == "type" and lying.reads == 1

    grown = FakeUpload(b"\x89PNG\r\n\x1a\n" + b"\x00" * images.MAX_IMAGE_BYTES, size=10)  # size declared too small
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(grown)
    assert exc.value.reason == "size"

    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(FakeUpload(b"", size=0))
    assert exc.value.reason == "empty"

    broken = FakeUpload(png(), fail=http_error(discord.NotFound, 404, 0, "gone"))
    with pytest.raises(images.ImageRejected) as exc:
        await images.read_upload(broken)
    assert exc.value.reason == "download"


async def test_upload_errors_are_reported_and_change_nothing(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    op = tenv.app.op
    cases = [
        (FakeUpload(b"plain", content_type="text/plain"), op("catalog.cmd.image_type")),
        (FakeUpload(png(), size=images.MAX_IMAGE_BYTES + 1), op("catalog.cmd.image_size", max_mb=8)),
        (FakeUpload(png(), fail=http_error(discord.HTTPException, 500, 0, "x")), op("catalog.cmd.image_download")),
    ]
    for upload, reason in cases:
        result = await _upload(tenv, product.id, upload)
        assert _title(result) == op("catalog.cmd.invalid") and _description(result) == reason
    both = await invoke(
        tenv,
        tenv.member(admin=True),
        "image",
        product_id=product.id,
        url="https://example.com/a.png",
        file=FakeUpload(png()),
    )
    assert _description(both) == op("catalog.cmd.image_both")
    assert await _stored(tenv, product.id) is None
    assert (await get(tenv, product.id)).version == product.version
    assert await audit_actions(tenv, "catalog.product_image") == []


async def test_upload_for_unknown_or_archived_product_is_refused_without_download(tenv: TestEnv) -> None:
    upload = FakeUpload(png())
    missing = await _upload(tenv, 9999, upload)
    assert _title(missing) == tenv.app.op("catalog.cmd.not_found", product_id=9999)
    product = await add_product(tenv, "[p]")
    await tenv.app.db.transaction(repo.update_product, product.id, product.version, now=1, archived=True)
    archived = await _upload(tenv, product.id, upload)
    assert _title(archived) == tenv.app.op("catalog.cmd.archived_product", product_id=product.id)
    assert upload.reads == 0


async def test_non_managers_cannot_upload(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    upload = FakeUpload(png())
    denied = await invoke(tenv, tenv.member(staff=True), "image", product_id=product.id, file=upload)
    assert denied.last_sent is not None and upload.reads == 0
    assert await _stored(tenv, product.id) is None


# --------------------------------------------------------------------------------------------------
# Storage and the listing
# --------------------------------------------------------------------------------------------------


async def test_upload_is_stored_and_attached_to_the_listing_message(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, "[p]", image_url="https://example.com/old.png")
    events: list[dict[str, Any]] = []

    async def on_change(**payload: Any) -> None:
        events.append(payload)

    tenv.app.events.subscribe(PRODUCT_CHANGED, on_change)
    data = png(0x40)
    result = await _upload(tenv, product.id, FakeUpload(data, filename="My Photo (1).png"))
    assert _title(result) == tenv.app.op("catalog.cmd.image_set", product_id=product.id)

    stored = await _stored(tenv, product.id)
    assert stored is not None and stored.data == data and stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest() and stored.content_type == "image/png"
    assert stored.filename == "My Photo (1).png" and stored.uploaded_by is not None
    after = await get(tenv, product.id)
    assert after.image_url is None and after.version == product.version + 1  # the upload replaces the link

    _, card = channel.messages
    expected = f"product-{product.id}-{stored.sha256[:16]}.png"
    assert _file_names(card) == [expected] and _file_bytes(card) == [data]
    assert card.embeds[0].image.url == f"attachment://{expected}"
    details = [e.details for e in await audit_actions(tenv, "catalog.product_image")]
    assert (
        details[-1]["upload"]["sha256"] == stored.sha256
        and details[-1]["image_url"][0] == "https://example.com/old.png"
    )
    assert events and events[-1]["action"] == "image"


async def test_edits_keep_the_attachment_and_a_new_image_rerenders(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, "[p]", stock=5)
    await _upload(tenv, product.id, FakeUpload(png(0x10)))
    _, card = channel.messages
    first_name = _file_names(card)[0]

    # A stock change edits the message: the image is attached again (Discord would drop it otherwise).
    await tenv.app.db.transaction(repo.reserve, product.id, 2, 9)
    await service.request_listing_refresh(tenv.app)
    edit = card.edits[-1]
    assert [f.filename for f in edit["attachments"]] == [first_name]
    assert card.embeds[0].image.url == f"attachment://{first_name}"
    assert _file_bytes(card) == [png(0x10)]

    # The same image again changes nothing (no version bump, no edit).
    edits = len(card.edits)
    version = (await get(tenv, product.id)).version
    same = await _upload(tenv, product.id, FakeUpload(png(0x10)))
    assert _title(same) == tenv.app.op("catalog.cmd.image_set", product_id=product.id)
    assert (await get(tenv, product.id)).version == version and len(card.edits) == edits

    # A different image gets a new attachment name and replaces the old attachment.
    await _upload(tenv, product.id, FakeUpload(png(0xEE)))
    second_name = _file_names(card)[0]
    assert second_name != first_name and card.embeds[0].image.url == f"attachment://{second_name}"
    assert _file_bytes(card) == [png(0xEE)]


async def test_clearing_and_replacing_with_a_link(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, "[p]")
    await _upload(tenv, product.id, FakeUpload(png()))
    _, card = channel.messages
    linked = await invoke(
        tenv, tenv.member(admin=True), "image", product_id=product.id, url="https://img.example.com/x.png"
    )
    assert _title(linked) == tenv.app.op("catalog.cmd.image_set", product_id=product.id)
    assert await _stored(tenv, product.id) is None
    assert card.files == [] and card.embeds[0].image.url == "https://img.example.com/x.png"

    await _upload(tenv, product.id, FakeUpload(png()))
    cleared = await invoke(tenv, tenv.member(admin=True), "image", product_id=product.id)
    assert _title(cleared) == tenv.app.op("catalog.cmd.image_cleared", product_id=product.id)
    assert await _stored(tenv, product.id) is None and (await get(tenv, product.id)).image_url is None
    assert card.edits[-1]["attachments"] == [] and card.files == [] and card.embeds[0].image.url is None


async def test_images_display_after_a_restart(tenv: TestEnv) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, "[p]")
    data = png(0x33)
    await _upload(tenv, product.id, FakeUpload(data))
    await channel.messages[1].delete()  # deleted while the bot was offline

    app = restarted_app(tenv)
    await app.resources.refresh()
    await MODULE.on_ready(app)  # type: ignore[misc]
    await app.scheduler.run_due()
    _, card = channel.messages
    assert _file_bytes(card) == [data]  # the bytes come from the database
    assert card.embeds[0].image.url == f"attachment://{_file_names(card)[0]}"

    # A later edit by the restarted process attaches the image again.
    await app.db.transaction(repo.set_stock, product.id, 3, 10)
    await service.request_listing_refresh(app)
    assert [f.filename for f in card.edits[-1]["attachments"]] == _file_names(card)


async def test_unchanged_messages_never_load_image_bytes(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    await listing_channel(tenv)
    for index in range(3):
        product = await add_product(tenv, f"[p{index}]")
        await _upload(tenv, product.id, FakeUpload(png(index * 40 + 1)))
    loads: list[list[str]] = []
    original = images.data_by_sha

    def counting(conn: Any, guild_id: int, digests: Any) -> dict[str, bytes]:
        wanted = list(digests)
        loads.append(wanted)
        return original(conn, guild_id, wanted)

    monkeypatch.setattr(images, "data_by_sha", counting)
    report = await service.request_listing_refresh(tenv.app)
    assert report.ok and loads == []


async def test_image_changed_during_refresh_is_retried(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    channel = await listing_channel(tenv)
    product = await add_product(tenv, "[p]")
    await _upload(tenv, product.id, FakeUpload(png()))
    await channel.messages[1].delete()
    monkeypatch.setattr(images, "data_by_sha", lambda conn, guild_id, digests: {})
    report = await service.request_listing_refresh(tenv.app)
    assert not report.ok and report.retry
    assert report.lines == (tenv.app.op("catalog.refresh.image_changed"),)


async def test_archiving_deletes_the_stored_image(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    await _upload(tenv, product.id, FakeUpload(png()))
    await invoke(tenv, tenv.member(admin=True), "archive", product_id=product.id, confirm=True)
    assert await _stored(tenv, product.id) is None


async def test_listing_groups_images_within_upload_limits(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"catalog": {"products_per_listing_message": 10}}))
    try:
        products = [await add_product(tenv, f"[p{i}]") for i in range(4)]
        size = 4 * 1024 * 1024
        refs = {
            p.id: images.ProductImage(
                product_id=p.id,
                guild_id=GUILD_ID,
                filename="x.png",
                content_type="image/png",
                size=size,
                sha256=f"{p.id:064x}",
                uploaded_by=None,
                created_at=0,
            )
            for p in products
        }
        specs = views.listing_messages(tenv.app.ui, tenv.app.settings, products, refs)
        assert [len(s.embeds) for s in specs] == [1, 2, 2]  # 3 x 4 MiB would exceed the 10 MiB upload limit
        for spec in specs:
            assert validate_message(spec) == []
            assert sum(f.size for f in spec.files or ()) <= FILES_TOTAL_BYTES
        assert all(f.data is None for s in specs for f in s.files or ())  # references only: loaded when sent
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Private browsing and staff views
# --------------------------------------------------------------------------------------------------


async def test_browse_attaches_the_image_and_paging_removes_it(tenv: TestEnv) -> None:
    with_image = await add_product(tenv, "[a]")
    await add_product(tenv, "[b]")
    await _upload(tenv, with_image.id, FakeUpload(png(0x22)))
    opened = await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:0")
    private = opened.last_sent
    assert private is not None and _file_bytes(private) == [png(0x22)]
    assert private.embeds[0].image.url == f"attachment://{_file_names(private)[0]}"
    private.flags = discord.MessageFlags(ephemeral=True)
    await click(tenv.app, user=tenv.member(), custom_id="sb:ct:browse:1", message=private)
    assert private.files == [] and private.embeds[0].image.url is None


async def test_product_view_shows_the_upload_to_staff(tenv: TestEnv) -> None:
    product = await add_product(tenv, "[p]")
    data = png()
    await _upload(tenv, product.id, FakeUpload(data, filename="shot.png"))
    viewed = await invoke(tenv, tenv.member(admin=True), "view", product_id=product.id)
    details, card = viewed.sent[-2:]
    image_field = next(f for f in details.embeds[0].fields if f.name == tenv.app.op("catalog.view.image"))
    assert image_field.value == tenv.app.op(
        "catalog.view.image_uploaded", filename="shot.png", kilobytes=1, type="image/png"
    )
    assert details.embeds[0].thumbnail.url == f"attachment://{_file_names(details)[0]}"
    assert _file_bytes(details) == [data] and _file_bytes(card) == [data]
    assert card.embeds[0].image.url == f"attachment://{_file_names(card)[0]}"


# --------------------------------------------------------------------------------------------------
# Generic file support (ui.spec / ui.limits / ui.discord_render / core.panels)
# --------------------------------------------------------------------------------------------------


def _embed(image: str | None = None) -> EmbedSpec:
    return EmbedSpec(title="[t]", image_url=image)


def test_limits_check_files_and_attachment_references() -> None:
    ok = MessageSpec(embeds=(_embed("attachment://a.png"),), files=(FileSpec.of("a.png", b"x"),))
    assert validate_message(ok) == []
    dangling = MessageSpec(embeds=(_embed("attachment://missing.png"),), files=())
    assert any("is not a file of this message" in str(v) for v in validate_message(dangling))
    unmanaged = MessageSpec(embeds=(_embed("attachment://a.png"),))
    assert validate_message(unmanaged)
    many = MessageSpec(embeds=(_embed(),), files=tuple(FileSpec.of(f"f{i}.png", b"x") for i in range(11)))
    assert any("exceeds 10" in str(v) for v in validate_message(many))
    big = FileSpec(filename="big.png", sha256="0" * 64, size=FILES_TOTAL_BYTES + 1)
    assert any("bytes of files" in str(v) for v in validate_message(MessageSpec(embeds=(_embed(),), files=(big,))))
    dup = MessageSpec(embeds=(_embed(),), files=(FileSpec.of("a.png", b"x"), FileSpec.of("a.png", b"y")))
    assert any("duplicate file name" in str(v) for v in validate_message(dup))
    bad_name = MessageSpec(embeds=(_embed(),), files=(FileSpec.of("a b.png", b"x"),))
    assert any("filename" in str(v) for v in validate_message(bad_name))
    mismatch = MessageSpec(embeds=(_embed(),), files=(replace(FileSpec.of("a.png", b"x"), size=5),))
    assert any("declares" in str(v) for v in validate_message(mismatch))


def test_render_sends_and_replaces_files_only_when_managed() -> None:
    files = (FileSpec.of("a.png", b"abc"),)
    sent = send_kwargs(MessageSpec(embeds=(_embed("attachment://a.png"),), files=files))
    assert [f.filename for f in sent["files"]] == ["a.png"]
    assert "files" not in send_kwargs(MessageSpec(embeds=(_embed(),)))
    assert "attachments" not in edit_kwargs(MessageSpec(embeds=(_embed(),)))  # unmanaged: attachments untouched
    assert edit_kwargs(MessageSpec(embeds=(_embed(),), files=()))["attachments"] == []
    edited = edit_kwargs(MessageSpec(embeds=(_embed("attachment://a.png"),), files=files))
    assert [f.filename for f in edited["attachments"]] == ["a.png"]
    reference = (FileSpec(filename="a.png", sha256=files[0].sha256, size=3),)
    with pytest.raises(RenderError):
        send_kwargs(MessageSpec(embeds=(_embed("attachment://a.png"),), files=reference))


def test_spec_hash_counts_files_by_digest_not_bytes() -> None:
    loaded = MessageSpec(embeds=(_embed("attachment://a.png"),), files=(FileSpec.of("a.png", b"abc"),))
    reference = replace(loaded, files=(replace(loaded.files[0], data=None),))  # type: ignore[index]
    assert spec_hash(loaded) == spec_hash(reference)
    assert spec_hash(loaded) != spec_hash(replace(loaded, files=(FileSpec.of("a.png", b"abd"),)))
    assert spec_hash(loaded) != spec_hash(replace(loaded, files=()))
    # A message that does not manage files hashes exactly as before file support existed.
    legacy = MessageSpec(embeds=(_embed(),))
    data = asdict(legacy)
    del data["files"], data["banner"]
    expected = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    assert spec_hash(legacy) == expected


def test_listing_card_without_upload_keeps_its_link(tenv: TestEnv) -> None:
    product = repo.Product(
        id=1,
        guild_id=GUILD_ID,
        name="[p]",
        description="",
        price_minor=100,
        image_url="https://example.com/p.png",
        stock_on_hand=None,
        reserved=0,
        max_per_order=None,
        active=True,
        archived=False,
        sort_order=0,
        version=1,
        created_at=0,
        updated_at=0,
    )
    assert images.embed_image(product, None) == ("https://example.com/p.png", [])
    embed = views.product_embed(tenv.app.ui, product)
    assert embed.image_url == "https://example.com/p.png"
    assert tenv.app.content.registry.get(keys.BUTTON_BUY) is not None
