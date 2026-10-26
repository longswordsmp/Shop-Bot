"""Entry-channel safety (spec 9: unverified users can access only the configured entry channels): /setup reports
every channel or category it does not manage that @everyone can see - in dry runs too - and never changes them."""

from __future__ import annotations

import discord

from shopbot.features.setup.report import report_messages
from shopbot.features.setup.service import MAX_PUBLIC_UNMANAGED_LINES, channel_kind
from shopbot.ui.limits import validate_message, validate_modal
from shopbot.ui.spec import ModalSpec
from tests.audit_logging.util import add_voice_channel, allow_everyone_to_view, audit_events
from tests.fakes import FakeCategory, FakeTextChannel
from tests.helpers import TestEnv
from tests.setup.util import invoke_setup, setup


def _discord_defaults(tenv: TestEnv) -> list[object]:
    """What Discord creates in a new server: "Text Channels"/#general and "Voice Channels"/General."""
    guild = tenv.guild
    text_category = guild.add_category(name="Text Channels")
    general = guild.add_text_channel(name="general", category=text_category)
    voice_category = guild.add_category(name="Voice Channels")
    voice = add_voice_channel(guild, name="General")
    voice.category_id = voice_category.id
    return [text_category, general, voice_category, voice]


def _lines_about(tenv: TestEnv, lines: list[str], channel: object) -> list[str]:
    mention = f"<#{getattr(channel, 'id', 0)}>"
    return [line for line in lines if mention in line]


async def test_discord_default_channels_are_reported_and_left_alone(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    defaults = _discord_defaults(tenv)
    report = await setup(tenv)
    problems = report.lines["problems"]
    labels = {
        defaults[0]: "Category",
        defaults[1]: "Text channel",
        defaults[2]: "Category",
        defaults[3]: "Voice channel",
    }
    for channel, label in labels.items():
        [line] = _lines_about(tenv, problems, channel)
        assert line.startswith(f"{label} <#{channel.id}>")  # type: ignore[attr-defined]
        assert f'("{channel.name}")' in line  # type: ignore[attr-defined]
    assert tenv.app.op("server.setup.public_unmanaged_hint") in problems
    assert report.public_unmanaged == 4
    # Never changed, never deleted.
    for channel in defaults:
        assert channel.edits == [] and channel.permission_edits == [] and not channel.deleted  # type: ignore[attr-defined]
        assert tenv.guild.get_channel(channel.id) is channel  # type: ignore[attr-defined]
    [event] = await audit_events(tenv.app, "setup.run")
    assert event.details["public_unmanaged"] == 4
    # Reported on every run, until the owner deletes or hides them.
    again = await setup(tenv)
    assert again.public_unmanaged == 4


async def test_dry_runs_report_them_too(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    defaults = _discord_defaults(tenv)
    report = await setup(tenv, dry_run=True)
    assert report.public_unmanaged == 4
    for channel in defaults:
        assert len(_lines_about(tenv, report.lines["problems"], channel)) == 1
    assert await audit_events(tenv.app, "setup.run") == []


async def test_managed_and_hidden_channels_are_not_reported(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    everyone = tenv.guild.default_role
    hidden = tenv.guild.add_text_channel(
        name="private-notes", overwrites={everyone: discord.PermissionOverwrite(view_channel=False)}
    )
    report = await setup(tenv)
    # The public START HERE-style category of the test layout ("support") and its channels are managed: fine.
    assert report.public_unmanaged == 0
    assert tenv.app.op("server.setup.public_unmanaged_hint") not in report.lines["problems"]
    assert _lines_about(tenv, report.lines["problems"], hidden) == []


async def test_private_ticket_channels_are_not_reported(tenv: TestEnv) -> None:
    from tests.helpers import provision_channels
    from tests.tickets.util import open_ticket

    await provision_channels(tenv)
    allow_everyone_to_view(tenv.guild)
    _, _, ticket_channel = await open_ticket(tenv, tenv.member())
    report = await setup(tenv)
    assert _lines_about(tenv, report.lines["problems"], ticket_channel) == []


async def test_an_explicit_everyone_allow_is_reported_without_server_wide_view(tenv: TestEnv) -> None:
    # Here @everyone has no server-wide View Channels permission; one unmanaged channel grants it explicitly.
    everyone = tenv.guild.default_role
    opened = tenv.guild.add_text_channel(
        name="lobby", overwrites={everyone: discord.PermissionOverwrite(view_channel=True)}
    )
    closed = tenv.guild.add_text_channel(name="plain")
    report = await setup(tenv)
    assert len(_lines_about(tenv, report.lines["problems"], opened)) == 1
    assert _lines_about(tenv, report.lines["problems"], closed) == []
    assert report.public_unmanaged == 1


async def test_long_lists_are_capped_and_the_report_stays_within_limits(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    extra = MAX_PUBLIC_UNMANAGED_LINES + 5
    for index in range(extra):
        tenv.guild.add_text_channel(name=f"{'x' * 90}-{index}")
    report = await setup(tenv)
    assert report.public_unmanaged == extra
    listed = [line for line in report.lines["problems"] if line.startswith("Text channel <#")]
    assert len(listed) == MAX_PUBLIC_UNMANAGED_LINES
    assert tenv.app.op("server.setup.public_unmanaged_more", count=5) in report.lines["problems"]
    for spec in report_messages(tenv.app.ui, tenv.app.op, report):
        assert validate_message(spec) == []


async def test_the_setup_reply_shows_the_warning_to_the_admin(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    _discord_defaults(tenv)
    admin = tenv.member(name="admin", admin=True)
    interaction = await invoke_setup(tenv, admin)
    text = "\n".join(message.text() for message in interaction.sent)
    assert "not managed by /setup" in text
    assert "Discord's default #general text channel and General voice channel" in text
    assert all(message.ephemeral for message in interaction.sent)  # type: ignore[attr-defined]


async def test_a_non_admin_cannot_run_setup_or_see_the_warning(tenv: TestEnv) -> None:
    allow_everyone_to_view(tenv.guild)
    _discord_defaults(tenv)
    customer = tenv.member(name="customer")
    interaction = await invoke_setup(tenv, customer)
    text = "\n".join(message.text() for message in interaction.sent)
    assert "not managed by /setup" not in text
    assert await audit_events(tenv.app, "setup.run") == []


def test_the_setup_preview_shows_the_warning_within_limits(tenv: TestEnv) -> None:
    from shopbot.features.setup import MODULE

    assert MODULE.preview is not None
    states = MODULE.preview(tenv.app)
    hint = tenv.app.op("server.setup.public_unmanaged_hint")
    for name in ("report", "report_dry_run"):
        [state] = [s for s in states if s.name == name]
        text = "\n".join(field.value for embed in state.spec.embeds for field in embed.fields)
        assert hint in text and "[sample unmanaged text]" in text
    for state in states:
        if isinstance(state.spec, ModalSpec):  # the /reset confirmation form
            assert validate_modal(state.spec) == [], state.name
        else:
            assert validate_message(state.spec) == [], state.name


def test_channel_kinds() -> None:
    class Forum(discord.ForumChannel):
        def __init__(self) -> None:
            pass

    class Stage(discord.StageChannel):
        def __init__(self) -> None:
            pass

    assert channel_kind(Forum()) == "forum"
    assert channel_kind(Stage()) == "stage"
    assert channel_kind(FakeCategory.__new__(FakeCategory)) == "category"
    assert channel_kind(FakeTextChannel.__new__(FakeTextChannel)) == "text"
    assert channel_kind(object()) == "other"
