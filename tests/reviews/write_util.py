"""Helpers for the /review and Write a review tests: an environment with unverified reviews on and the shipped approval
channel, the command run like Discord runs it, the button and its stars menu clicked through the router, the form
submitted through the router (with a photo uploaded in it), uploaded photos and their downloads."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord

from shopbot.commands import on_tree_error
from shopbot.context import App
from shopbot.features.reviews import images, repo, views
from shopbot.features.reviews.preview import sample_photo_png
from tests.fakes import (
    FakeInteraction,
    FakeMember,
    FakeMessage,
    FakeTextChannel,
    click,
    http_error,
    modal_of,
    submit_modal,
)
from tests.helpers import TestEnv, deep_merge
from tests.reviews.util import make_env
from tests.reviews_approval.util import approval_overrides

PNG = sample_photo_png(40, 30)
CDN_URL = "https://cdn.discordapp.com/attachments/1/2/Ada_Lovelace_at_home.png"


@dataclass
class Upload:
    """What the ``image`` option of /review provides (``discord.Attachment``). Its file name names the member: it must
    never be shown."""

    filename: str = "Ada_Lovelace_at_home.png"
    url: str = CDN_URL
    size: int = len(PNG)
    content_type: str | None = "image/png"


class FakeCdn:
    """Stands in for ``bot.http.get_from_cdn`` (downloads of uploaded attachments)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {CDN_URL: PNG}
        self.fail: BaseException | None = None
        self.requests: list[str] = []

    async def get_from_cdn(self, url: str) -> bytes:
        self.requests.append(url)
        if self.fail is not None:
            raise self.fail
        if url not in self.files:
            raise http_error(discord.NotFound, 404, 0, "asset not found")
        return self.files[url]


def write_overrides(reviews: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """Approval channel ``staff_chat`` (as shipped), unverified reviews on, approval off, photos checked first (as
    shipped)."""
    merged = deep_merge(
        {"require_approval": False, "allow_unverified": True, "photo_requires_approval": True}, reviews or {}
    )
    return approval_overrides(deep_merge({"reviews": merged}, extra))


async def make_write_env(tmp_path: Path, reviews: dict[str, Any] | None = None, **kwargs: Any) -> TestEnv:
    tenv = await make_env(tmp_path, write_overrides(reviews), **kwargs)
    tenv.bot.http = FakeCdn()  # type: ignore[assignment]
    return tenv


def cdn(tenv: TestEnv) -> FakeCdn:
    http = tenv.bot.http
    assert isinstance(http, FakeCdn)
    return http


def review_command(app: App) -> discord.app_commands.Command[Any, Any, Any]:
    assert app.bot is not None
    command = app.bot.tree.get_command("review", guild=app.guild_object)
    assert isinstance(command, discord.app_commands.Command), command
    return command


async def run_review(
    tenv: TestEnv, user: FakeMember, *, stars: int = 5, image: Upload | None = None, app: App | None = None
) -> FakeInteraction:
    """/review like Discord runs it: checks first (errors go to the tree handler), then the callback."""
    app = app or tenv.app
    command = review_command(app)
    interaction = FakeInteraction(
        client=app.bot, user=user, guild=tenv.guild, type=discord.InteractionType.application_command
    )
    try:
        allowed = await command._check_can_run(interaction)
    except discord.app_commands.AppCommandError as exc:
        await on_tree_error(interaction, exc)  # type: ignore[arg-type]
        return interaction
    assert allowed
    await command.callback(interaction, stars=stars, image=image)
    return interaction


async def send_form(
    tenv: TestEnv, user: FakeMember, opened: FakeInteraction, text: str = "[great service]", *, app: App | None = None
) -> FakeInteraction:
    """Submit the form /review opened (through the router, like Discord's form submission)."""
    modal = modal_of(opened)
    assert modal is not None, "the form did not open"
    return await submit_modal(
        app or tenv.app, user=user, custom_id=modal.custom_id, fields={views.DESCRIPTION_INPUT_ID: text}
    )


async def press_write(tenv: TestEnv, user: FakeMember, *, panel: FakeMessage | None = None) -> FakeInteraction:
    """Press Write a review (on the intro panel when given); its private answer is ``last_sent``."""
    return await click(tenv.app, user=user, custom_id=views.start_id(), message=panel)


async def pick(tenv: TestEnv, user: FakeMember, menu: FakeMessage, stars: int = 5) -> FakeInteraction:
    """Pick stars in the private menu Write a review answered with (the form opens, or the menu shows why not)."""
    return await click(tenv.app, user=user, custom_id=views.pick_id(), message=menu, values=[str(stars)])


async def send_button_form(
    tenv: TestEnv, user: FakeMember, opened: FakeInteraction, text: str = "[great service]", *, photo: Any = None
) -> FakeInteraction:
    """Submit the form the stars menu opened, with ``photo`` uploaded in it (like Discord's file upload field)."""
    modal = modal_of(opened)
    assert modal is not None, "the form did not open"
    uploads = {views.PHOTO_INPUT_ID: [photo]} if photo is not None else None
    return await submit_modal(
        tenv.app, user=user, custom_id=modal.custom_id, fields={views.DESCRIPTION_INPUT_ID: text}, uploads=uploads
    )


async def write_with_button(
    tenv: TestEnv, user: FakeMember, text: str = "[great service]", *, stars: int = 5, photo: Any = None
) -> FakeInteraction:
    """Write a review → stars → the form; returns the form's interaction (its private reply is ``last_sent``)."""
    menu = (await press_write(tenv, user)).last_sent
    assert menu is not None, "no stars menu"
    opened = await pick(tenv, user, menu, stars)
    return await send_button_form(tenv, user, opened, text, photo=photo)


def upload_fields(modal: discord.ui.Modal) -> list[discord.ui.FileUpload[Any]]:
    return [item for item in modal.walk_children() if isinstance(item, discord.ui.FileUpload)]


async def write(
    tenv: TestEnv, user: FakeMember, text: str = "[great service]", *, stars: int = 5, image: Upload | None = None
) -> FakeInteraction:
    """/review and its form; returns the form's interaction (its private reply is ``last_sent``)."""
    opened = await run_review(tenv, user, stars=stars, image=image)
    return await send_form(tenv, user, opened, text)


def reply_of(interaction: FakeInteraction) -> FakeMessage:
    message = interaction.last_sent
    assert message is not None, "no reply"
    assert getattr(message, "ephemeral", False), "the reply is not private"
    return message


async def reviews_of(tenv: TestEnv, user_id: int) -> list[repo.Review]:
    def rows(conn: sqlite3.Connection) -> list[repo.Review]:
        ids = [r[0] for r in conn.execute("SELECT id FROM reviews WHERE user_id = ? ORDER BY id", (user_id,))]
        return [review for review_id in ids if (review := repo.get_review(conn, review_id)) is not None]

    return await tenv.app.db.run(rows)


async def stored_image(tenv: TestEnv, review_id: int) -> images.ReviewImage | None:
    return await tenv.app.db.run(images.get_image, review_id)


def channel_by_key(tenv: TestEnv, key: str) -> FakeTextChannel:
    channel = tenv.guild.get_channel(tenv.app.resources.channel_id(key))
    assert isinstance(channel, FakeTextChannel)
    return channel


def file_names(message: FakeMessage) -> list[str]:
    return [f.filename for f in message.files]
