"""/content set/show/clear/missing/keys/export."""

from __future__ import annotations

import tomllib
from pathlib import Path

import discord
import pytest

from shopbot.config.content import flatten
from shopbot.db.repos import state as state_repo
from shopbot.db.repos.audit import recent
from shopbot.features import core_keys as ck
from shopbot.features.admin import service
from shopbot.features.admin.state import state_for
from shopbot.ui.limits import validate_message
from tests.admin.util import (
    admin,
    attachment_text,
    audit_actions,
    is_ephemeral,
    restarted_app,
    run_command,
    sent,
)
from tests.fakes import FakeInteraction, click, http_error, modal_input_ids, submit_modal
from tests.helpers import TestEnv, make_test_env


def _text_input(modal: discord.ui.Modal) -> discord.ui.TextInput[discord.ui.Modal]:
    inputs = [item for item in modal.walk_children() if isinstance(item, discord.ui.TextInput)]
    assert len(inputs) == 1
    return inputs[0]


async def open_form(tenv: TestEnv, key: str, user: object | None = None) -> tuple[str, discord.ui.Modal]:
    interaction = await run_command(tenv, "set", group="content", user=user or admin(tenv), key=key)
    modal = interaction.response.modal
    assert modal is not None, interaction.sent and interaction.sent[0].text()
    return modal.custom_id, modal


async def set_value(tenv: TestEnv, key: str, value: str, user: object | None = None) -> FakeInteraction:
    user = user or admin(tenv)
    custom_id, _ = await open_form(tenv, key, user)
    return await submit_modal(tenv.app, user=user, custom_id=custom_id, fields={"value": value})


# --------------------------------------------------------------------------------------------------
# set
# --------------------------------------------------------------------------------------------------


async def test_set_opens_prefilled_form_with_short_token(tenv: TestEnv) -> None:
    custom_id, modal = await open_form(tenv, ck.ERROR_GENERIC)
    assert custom_id.startswith("sb:ad:cset:") and len(custom_id) <= 100
    assert ck.ERROR_GENERIC not in custom_id  # the key travels via the in-memory token
    assert modal_input_ids(modal) == ["value"]
    text_input = _text_input(modal)
    assert text_input.default == tenv.text(ck.ERROR_GENERIC)
    assert text_input.max_length == 4000
    assert text_input.style == discord.TextStyle.paragraph


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (ck.ERROR_GENERIC, "  Leading and trailing spaces are kept.  "),
        (ck.ERROR_GENERIC, "Line one\n\nLine three 👋🏽 with emoji\n"),
        (ck.ERROR_GENERIC, "Quotes \"double\" 'single' and \\backslash\\ and ``` fences"),
        (ck.ERROR_GENERIC, "Text with {braces} that are not placeholders"),
        (ck.ERROR_COOLDOWN, "Wait {retry_after} seconds."),
    ],
)
async def test_set_saves_exact_value_and_reports_it(tenv: TestEnv, key: str, value: str) -> None:
    interaction = await set_value(tenv, key, value)
    assert await tenv.app.db.run(state_repo.overrides_all) == {key: value}
    assert tenv.app.content.raw(key) == value
    assert tenv.app.content.source(key) == "override"
    message = sent(interaction)
    assert is_ephemeral(message)
    assert message.embeds[0].title == f"Saved: {key}"
    if "```" in value:
        assert attachment_text(message) == value
    else:
        assert message.embeds[0].description == f"```\n{value}\n```"
        assert message.files == []
    events = await tenv.app.db.run(recent, tenv.app.env.guild_id, limit=5)
    assert events[0].action == "admin.content_set"
    assert events[0].details == {"key": key, "length": len(value), "feature": "core"}
    assert value not in str(events[0].details)


async def test_set_rejects_too_long_value_and_saves_nothing(tenv: TestEnv) -> None:
    value = "x" * 257  # common.error.title is an embed title (max 256)
    interaction = await set_value(tenv, ck.ERROR_TITLE, value)
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert tenv.app.content.raw(ck.ERROR_TITLE) == tenv.text(ck.ERROR_TITLE)
    message = sent(interaction)
    assert message.embeds[0].title == f"Not saved: {ck.ERROR_TITLE}"
    assert "is 257 characters; Discord allows at most 256 here" in message.text()
    assert message.embeds[0].description == f"```\n{value}\n```"  # shown back so it can be fixed
    assert "admin.content_set" not in await audit_actions(tenv.app)


async def test_set_rejects_unknown_placeholders(tenv: TestEnv) -> None:
    interaction = await set_value(tenv, ck.ERROR_COOLDOWN, "Wait {seconds}")
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert "uses placeholders ['seconds']" in sent(interaction).text()


async def test_set_rejects_empty_value(tenv: TestEnv) -> None:
    interaction = await set_value(tenv, ck.ERROR_GENERIC, "")
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert "the value is empty" in sent(interaction).text()


async def test_set_rejects_blank_button_label(tenv: TestEnv) -> None:
    interaction = await set_value(tenv, ck.BUTTON_BACK, "   ")
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert "contains only whitespace" in sent(interaction).text()


async def test_set_unknown_key_suggests_close_matches(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "set", group="content", user=admin(tenv), key="common.error.generik")
    assert interaction.response.modal is None
    message = sent(interaction)
    assert message.embeds[0].title == "Unknown content key `common.error.generik`."
    assert f"`{ck.ERROR_GENERIC}`" in (message.embeds[0].description or "")


async def test_duplicate_submission_does_not_apply_twice(tenv: TestEnv) -> None:
    user = admin(tenv)
    custom_id, _ = await open_form(tenv, ck.ERROR_GENERIC, user)
    first = await submit_modal(tenv.app, user=user, custom_id=custom_id, fields={"value": "first"})
    second = await submit_modal(tenv.app, user=user, custom_id=custom_id, fields={"value": "second"})
    assert sent(first).embeds[0].title == f"Saved: {ck.ERROR_GENERIC}"
    assert sent(second).embeds[0].title.startswith("This form is no longer valid")
    assert tenv.app.content.raw(ck.ERROR_GENERIC) == "first"
    assert (await audit_actions(tenv.app)).count("admin.content_set") == 1


async def test_form_of_another_admin_cannot_be_used(tenv: TestEnv) -> None:
    custom_id, _ = await open_form(tenv, ck.ERROR_GENERIC, admin(tenv))
    other = admin(tenv)
    interaction = await submit_modal(tenv.app, user=other, custom_id=custom_id, fields={"value": "x"})
    assert sent(interaction).embeds[0].title.startswith("This form is no longer valid")
    assert await tenv.app.db.run(state_repo.overrides_all) == {}


async def test_form_expires(tenv: TestEnv) -> None:
    user = admin(tenv)
    custom_id, _ = await open_form(tenv, ck.ERROR_GENERIC, user)
    tenv.clock.advance(3601)
    interaction = await submit_modal(tenv.app, user=user, custom_id=custom_id, fields={"value": "late"})
    assert sent(interaction).embeds[0].title.startswith("This form is no longer valid")
    assert await tenv.app.db.run(state_repo.overrides_all) == {}


async def test_restart_between_open_and_submit_asks_to_run_again(tenv: TestEnv) -> None:
    user = admin(tenv)
    custom_id, _ = await open_form(tenv, ck.ERROR_GENERIC, user)
    app = restarted_app(tenv)
    interaction = await submit_modal(app, user=user, custom_id=custom_id, fields={"value": "after restart"})
    assert sent(interaction).embeds[0].title == (
        "This form is no longer valid (the bot restarted or it expired). Run /content set again."
    )
    assert await app.db.run(state_repo.overrides_all) == {}


async def test_saved_override_survives_restart(tenv: TestEnv) -> None:
    value = "Persisted 🎉\nexactly"
    await set_value(tenv, ck.ERROR_GENERIC, value)
    app = restarted_app(tenv)
    await app.load_overrides()
    assert app.content.raw(ck.ERROR_GENERIC) == value
    assert app.content.source(ck.ERROR_GENERIC) == "override"


async def test_set_recomputes_readiness_and_reports_the_change(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    try:
        values = {k: v for k, v in tenv.content.items() if k != ck.ERROR_GENERIC}
        tenv.app.content.replace(file_values=values)
        tenv.app.recompute_readiness()
        assert not tenv.app.ready("core") and not tenv.app.ready("tickets")
        interaction = await set_value(tenv, ck.ERROR_GENERIC, "Something went wrong.")
        assert tenv.app.ready("core") and tenv.app.ready("tickets")
        text = sent(interaction).text()
        assert "`core`: not ready (1 problems) → ready" in text
        assert "`tickets`: not ready (1 problems) → ready" in text
    finally:
        await tenv.close()


async def test_confirmation_failure_still_saves_once(tenv: TestEnv) -> None:
    user = admin(tenv)
    custom_id, _ = await open_form(tenv, ck.ERROR_GENERIC, user)
    interaction = FakeInteraction(
        client=tenv.bot,
        user=user,
        guild=tenv.guild,
        type=discord.InteractionType.modal_submit,
    )

    async def broken_send(*args: object, **kwargs: object) -> None:
        raise http_error(discord.HTTPException, 500, 0, "server error")

    interaction.response.send_message = broken_send  # type: ignore[method-assign]
    await tenv.app.router.dispatch(interaction, custom_id, kind="modal", fields={"value": "saved anyway"})
    assert tenv.app.content.raw(ck.ERROR_GENERIC) == "saved anyway"
    assert (await audit_actions(tenv.app)).count("admin.content_set") == 1


# --------------------------------------------------------------------------------------------------
# show / clear (override precedence)
# --------------------------------------------------------------------------------------------------


async def test_show_reports_value_source_and_constraints(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "show", group="content", user=admin(tenv), key=ck.ERROR_COOLDOWN)
    message = sent(interaction)
    assert message.embeds[0].title == f"Content: {ck.ERROR_COOLDOWN}"
    assert message.embeds[0].description == f"```\n{tenv.text(ck.ERROR_COOLDOWN)}\n```"
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields["Source"] == "content.toml"
    assert fields["Kind"] == "description"
    assert fields["Max length"] == "4096 characters"
    assert fields["Placeholders"] == "`{retry_after}`"
    assert fields["Required"] == "yes"
    assert "Shown when someone acts too quickly" in fields["Where it appears"]


async def test_show_unset_key(tenv: TestEnv) -> None:
    values = {k: v for k, v in tenv.content.items() if k != ck.ERROR_BUSY}
    tenv.app.content.replace(file_values=values)
    interaction = await run_command(tenv, "show", group="content", user=admin(tenv), key=ck.ERROR_BUSY)
    message = sent(interaction)
    assert message.embeds[0].description == "This key is not configured."
    assert {f.name: f.value for f in message.embeds[0].fields}["Source"] == "not configured"


async def test_override_takes_precedence_and_clear_restores_file_value(tenv: TestEnv) -> None:
    key = ck.ERROR_GENERIC
    await set_value(tenv, key, "override text")
    assert tenv.app.text(key) == "override text"
    shown = await run_command(tenv, "show", group="content", user=admin(tenv), key=key)
    assert {f.name: f.value for f in sent(shown).embeds[0].fields}["Source"] == "override (/content set)"

    cleared = await run_command(tenv, "clear", group="content", user=admin(tenv), key=key)
    message = sent(cleared)
    assert message.embeds[0].title == f"Override removed: {key}"
    assert "The value now comes from: content.toml" in message.text()
    assert tenv.app.text(key) == tenv.text(key)
    assert await tenv.app.db.run(state_repo.overrides_all) == {}
    assert (await audit_actions(tenv.app)).count("admin.content_clear") == 1

    again = await run_command(tenv, "clear", group="content", user=admin(tenv), key=key)
    assert sent(again).embeds[0].title == f"There is no /content set override for `{key}`; nothing changed."
    assert (await audit_actions(tenv.app)).count("admin.content_clear") == 1


async def test_clear_makes_feature_not_ready_when_no_file_value(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path)
    try:
        values = {k: v for k, v in tenv.content.items() if k != ck.ERROR_GENERIC}
        tenv.app.content.replace(file_values=values)
        await set_value(tenv, ck.ERROR_GENERIC, "configured in Discord")
        assert tenv.app.ready("core")
        cleared = await run_command(tenv, "clear", group="content", user=admin(tenv), key=ck.ERROR_GENERIC)
        assert not tenv.app.ready("core")
        assert "`core`: ready → not ready (1 problems)" in sent(cleared).text()
        assert "The value now comes from: not configured" in sent(cleared).text()
    finally:
        await tenv.close()


async def test_clear_removes_stale_override_for_unregistered_key(tenv: TestEnv) -> None:
    await tenv.app.db.transaction(state_repo.override_set, "tickets.removed.key", "old", None, 1)
    interaction = await run_command(tenv, "clear", group="content", user=admin(tenv), key="tickets.removed.key")
    assert sent(interaction).embeds[0].title == "Override removed: tickets.removed.key"
    assert await tenv.app.db.run(state_repo.overrides_all) == {}


async def test_clear_unknown_key(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "clear", group="content", user=admin(tenv), key="nope.nothing")
    assert sent(interaction).embeds[0].title == "Unknown content key `nope.nothing`."


# --------------------------------------------------------------------------------------------------
# missing / keys
# --------------------------------------------------------------------------------------------------


async def test_missing_lists_required_keys_by_feature_with_reasons(tenv: TestEnv) -> None:
    values = dict(tenv.content)
    del values[ck.ERROR_GENERIC]
    values[ck.ERROR_TITLE] = "x" * 300
    tenv.app.content.replace(file_values=values)
    tenv.app.recompute_readiness()
    interaction = await run_command(tenv, "missing", group="content", user=admin(tenv))
    message = sent(interaction)
    text = message.text()
    assert "Feature: core" in text
    assert f"`{ck.ERROR_GENERIC}` — not configured" in text
    assert f"`{ck.ERROR_TITLE}` — invalid: is 300 characters" in text


async def test_missing_when_everything_configured(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "missing", group="content", user=admin(tenv))
    assert "All required content is configured and valid." in sent(interaction).text()
    assert sent(interaction).custom_ids() == []


async def test_missing_paginates_with_buttons(tenv: TestEnv) -> None:
    from shopbot.config.readiness import Problem

    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    tickets = tenv.app.readiness.statuses["tickets"]
    tickets.problems = [
        *tickets.problems,
        *(Problem("tickets", "missing_content", "not configured", f"tickets.synthetic.k{i:03d}") for i in range(150)),
    ]
    user = admin(tenv)
    interaction = await run_command(tenv, "missing", group="content", user=user)
    message = sent(interaction)
    assert "sb:ad:miss:-:2" in message.custom_ids()
    pages = int((message.embeds[0].footer.text or "").rsplit(" ", 1)[-1])
    assert pages >= 2
    seen = set(message.text().splitlines())
    for page in range(2, pages + 1):
        await click(tenv.app, user=user, custom_id=f"sb:ad:miss:-:{page}", message=message)
        assert message.embeds[0].footer.text == f"Page {page} of {pages}"
        seen |= set(message.text().splitlines())
    for problem in tenv.app.readiness.problems(enabled_only=True):
        if problem.key is not None and problem.kind == "missing_content":
            assert any(f"`{problem.key}`" in line for line in seen), problem.key


async def test_missing_for_one_feature_and_unknown_feature(tenv: TestEnv) -> None:
    tenv.app.content.replace(file_values={})
    tenv.app.recompute_readiness()
    interaction = await run_command(tenv, "missing", group="content", user=admin(tenv), feature="core")
    assert "Feature: core" in sent(interaction).text()
    assert "Feature: tickets" not in sent(interaction).text()
    unknown = await run_command(tenv, "missing", group="content", user=admin(tenv), feature="nope")
    assert sent(unknown).embeds[0].title == "Unknown feature `nope`."


async def test_keys_lists_every_key_with_status(tenv: TestEnv) -> None:
    values = dict(tenv.content)
    del values[ck.ERROR_GENERIC]
    del values[ck.ERROR_BUSY]
    tenv.app.content.replace(file_values=values)
    tenv.app.content.set_override(ck.ERROR_TITLE, "override")
    tenv.app.recompute_readiness()
    interaction = await run_command(tenv, "keys", group="content", user=admin(tenv), feature="core")
    text = sent(interaction).text()
    assert f"❌ `{ck.ERROR_GENERIC}` — description, required, not configured" in text
    assert f"▫️ `{ck.ERROR_BUSY}` — description, optional, not configured" in text
    assert f"✅ `{ck.ERROR_TITLE}` — title, optional, override (/content set)" in text
    assert f"✅ `{ck.ERROR_COOLDOWN}` — description, required, content.toml" in text
    registry_core = [d.key for d in tenv.app.content.registry.for_feature("core")]
    assert all(f"`{key}`" in text for key in registry_core)


async def test_keys_pages_via_option_and_buttons(tenv: TestEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from shopbot.features.admin import views

    monkeypatch.setattr(views, "PAGE_BUDGET", 300)  # small pages so the current registry spans several
    user = admin(tenv)
    first = await run_command(tenv, "keys", group="content", user=user)
    message = sent(first)
    footer = message.embeds[0].footer.text or ""
    pages = int(footer.rsplit(" ", 1)[-1])
    assert pages >= 2
    last = await run_command(tenv, "keys", group="content", user=user, page=999)
    assert sent(last).embeds[0].footer.text == f"Page {pages} of {pages}"
    await click(tenv.app, user=user, custom_id="sb:ad:keys:-:2", message=message)
    assert message.embeds[0].footer.text == f"Page 2 of {pages}"
    rows = service.key_rows(tenv.app, None)
    total = sum(len(items) for _, items in rows)
    assert total == len(tenv.app.content.registry.all())


# --------------------------------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------------------------------


async def test_export_roundtrips_effective_values(tenv: TestEnv) -> None:
    tricky = "Override wins: \"quotes\" 'single' \\ \n\tnew line ''' and ``` 👋🏽  "
    await set_value(tenv, ck.ERROR_GENERIC, tricky)
    values = dict(tenv.content)
    del values[ck.ERROR_BUSY]
    tenv.app.content.replace(file_values=values)
    interaction = await run_command(tenv, "export", group="content", user=admin(tenv))
    message = sent(interaction)
    assert is_ephemeral(message)
    assert message.files[0].filename.startswith("content-") and message.files[0].filename.endswith(".toml")
    loaded, errors = flatten(tomllib.loads(attachment_text(message)))
    assert errors == []
    expected = {d.key: tenv.app.content.raw(d.key) or "" for d in tenv.app.content.registry.all()}
    assert loaded == expected
    assert loaded[ck.ERROR_GENERIC] == tricky
    assert loaded[ck.ERROR_BUSY] == ""
    total = len(expected)
    assert f"{total} keys, {total - 1} configured (1 set with /content set)" in message.text()


async def test_export_never_contains_operator_or_unknown_wording(tenv: TestEnv) -> None:
    interaction = await run_command(tenv, "export", group="content", user=admin(tenv))
    loaded, _ = flatten(tomllib.loads(attachment_text(sent(interaction))))
    assert set(loaded) == {d.key for d in tenv.app.content.registry.all()}
    assert all(value == tenv.content[key] for key, value in loaded.items())


async def test_pending_store_is_bounded(tenv: TestEnv) -> None:
    pending = state_for(tenv.app).pending
    user = admin(tenv)
    for _ in range(250):
        pending.create(ck.ERROR_GENERIC, user.id, tenv.clock.now())
    assert len(pending) == 200


async def test_all_content_replies_respect_limits(tenv: TestEnv) -> None:
    """Every reply above went through send_kwargs (which validates); double-check the largest views."""
    from shopbot.features.admin import views

    long_value = "y" * 4000
    reply = views.content_saved(tenv.app.ui, tenv.app.op, ck.ERROR_GENERIC, long_value, [])
    assert validate_message(reply.spec) == [] and reply.attachment is None
    info = service.key_info(tenv.app, ck.ERROR_GENERIC)
    assert info is not None
    reply = views.content_show(tenv.app.ui, tenv.app.op, info)
    assert validate_message(reply.spec) == []


async def test_show_follows_fallback_for_the_effective_value(tenv: TestEnv) -> None:
    from shopbot.config.content_keys import Kind, Registry
    from shopbot.config.content_keys import ck as content_key

    extra = content_key("zz.alt.title", Kind.TITLE, "core", "Test", "Synthetic key.", fallback=ck.ERROR_TITLE)
    tenv.app.content.replace(registry=Registry([*tenv.app.content.registry.all(), extra]))
    interaction = await run_command(tenv, "show", group="content", user=admin(tenv), key="zz.alt.title")
    message = sent(interaction)
    assert message.embeds[0].description == f"```\n{tenv.text(ck.ERROR_TITLE)}\n```"
    fields = {f.name: f.value for f in message.embeds[0].fields}
    assert fields["Source"] == f"not configured; uses the fallback `{ck.ERROR_TITLE}`"
    assert fields["Fallback"] == f"`{ck.ERROR_TITLE}`"
    keys = await run_command(tenv, "keys", group="content", user=admin(tenv), feature="core")
    assert "✅ `zz.alt.title` — title, optional, not configured; uses the fallback" in sent(keys).text()
