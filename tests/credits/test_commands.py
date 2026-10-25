"""/credits commands: permissions, adjustments, notices, audit, staff history paging."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
import pytest

from shopbot.core.money import format_amount
from shopbot.features import core_keys as ck
from shopbot.features.credits import repo, service, views
from shopbot.features.credits.content_keys import NOTICE_DESCRIPTION, NOTICE_TITLE
from shopbot.ui.limits import validate_message
from tests.credits.util import (
    audit_actions,
    balance,
    buttons,
    credits_command,
    entries,
    grant,
    invoke,
    make_order,
    reply_embed,
    restarted_app,
)
from tests.fakes import FakeMember, FakeUser, click, http_error
from tests.helpers import GUILD_ID, TestEnv, make_settings, make_test_env


def _usd(minor: int) -> str:
    return f"{format_amount(minor, 2)} USD"


def _rendered(tenv: TestEnv, key: str, **values: str) -> str:
    text = tenv.text(key)
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def _money(tenv: TestEnv, minor: int) -> str:
    text = tenv.app.ui.money(minor)
    assert text is not None
    return text


# --------------------------------------------------------------------------------------------------
# Metadata and permissions
# --------------------------------------------------------------------------------------------------


async def test_group_metadata(tenv: TestEnv) -> None:
    assert tenv.app.bot is not None
    group = tenv.app.bot.tree.get_command("credits", guild=tenv.app.guild_object)
    assert isinstance(group, discord.app_commands.Group)
    assert group.default_permissions == discord.Permissions(manage_guild=True)
    assert group.guild_only is True
    assert {c.name for c in group.commands} == {"balance", "history", "adjust"}
    for command in group.commands:
        assert isinstance(command, discord.app_commands.Command)
        assert command.description and all(p.description for p in command.parameters)
        assert len(command.checks) == 1
        member_option = next(p for p in command.parameters if p.name == "member")
        assert member_option.type is discord.AppCommandOptionType.user  # members and users who left
    adjust = credits_command(tenv.app, "adjust")
    action = next(p for p in adjust.parameters if p.name == "action")
    assert [c.value for c in action.choices] == ["add", "remove"]
    note = next(p for p in adjust.parameters if p.name == "note")
    assert note.max_value == repo.NOTE_MAX_LENGTH and note.required


async def test_customer_is_refused_everything(tenv: TestEnv) -> None:
    customer, target = tenv.member(), tenv.member(name="target")
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("balance", {"member": target}),
        ("history", {"member": target}),
        ("adjust", {"member": target, "action": "add", "amount": "5", "note": "[n]"}),
    ]
    for name, kwargs in attempts:
        interaction = await invoke(tenv, customer, name, **kwargs)
        assert reply_embed(interaction).description == tenv.text(ck.ERROR_PERMISSION)
        assert interaction.last_sent is not None and interaction.last_sent.ephemeral  # type: ignore[attr-defined]
    assert await balance(tenv, target.id) == 0
    assert await audit_actions(tenv) == []


async def test_staff_can_view_but_not_adjust(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    await grant(tenv, target.id, 1_234)
    shown = await invoke(tenv, staff, "balance", member=target)
    embed = reply_embed(shown)
    assert embed.title == tenv.app.op("credits.cmd.balance_title")
    assert embed.description == tenv.app.op("credits.cmd.balance", member=target.mention, balance=_usd(1_234))
    history = await invoke(tenv, staff, "history", member=target)
    assert reply_embed(history).title == tenv.app.op("credits.history.title")
    denied = await invoke(tenv, staff, "adjust", member=target, action="add", amount="5", note="[n]")
    assert reply_embed(denied).description == tenv.text(ck.ERROR_PERMISSION)
    assert await balance(tenv, target.id) == 1_234


async def test_checkout_staff_role_counts_as_staff_for_lookups(tmp_path: Path) -> None:
    settings = make_settings(
        {"server": {"roles": [{"key": "staff"}, {"key": "cashier"}]}, "checkout": {"staff_roles": ["cashier"]}}
    )
    tenv = await make_test_env(tmp_path, settings=settings)
    try:
        role = tenv.guild.add_role(name="cashier", position=6)
        await tenv.app.resources.remember("role", "cashier", role.id)
        cashier = tenv.guild.add_member(name="cashier", roles=[role])
        target = tenv.member(name="target")
        shown = await invoke(tenv, cashier, "balance", member=target)
        assert reply_embed(shown).title == tenv.app.op("credits.cmd.balance_title")
        denied = await invoke(tenv, cashier, "adjust", member=target, action="add", amount="1", note="[n]")
        assert reply_embed(denied).description == tenv.text(ck.ERROR_PERMISSION)
    finally:
        await tenv.close()


async def test_commands_refuse_when_credits_not_ready(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"checkout": {"currency": ""}}))
    try:
        assert not tenv.app.ready("credits")
        admin, target = tenv.member(admin=True), tenv.member(name="target")
        for name, kwargs in (
            ("balance", {"member": target}),
            ("history", {"member": target}),
            ("adjust", {"member": target, "action": "add", "amount": "5", "note": "[n]"}),
        ):
            interaction = await invoke(tenv, admin, name, **kwargs)
            assert reply_embed(interaction).title == tenv.app.op("command.feature_disabled", feature="credits")
        assert await balance(tenv, target.id) == 0
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Adjustments
# --------------------------------------------------------------------------------------------------


async def test_admin_adds_credit_with_audit_and_notice(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="1,234.50", note="[goodwill]")
    assert interaction.response_kinds() == ["defer"]
    rows = await entries(tenv, target.id)
    assert len(rows) == 1
    entry = rows[0]
    assert (entry.delta_minor, entry.kind, entry.actor_id, entry.note) == (
        123_450,
        repo.KIND_ADMIN_ADJUST,
        admin.id,
        "[goodwill]",
    )
    embed = reply_embed(interaction)
    assert embed.title == tenv.app.op("credits.cmd.added_title")
    assert embed.description == "\n".join(
        [
            tenv.app.op(
                "credits.cmd.adjusted",
                member=target.mention,
                delta="+" + _usd(123_450),
                balance=_usd(123_450),
                entry_id=entry.id,
            ),
            tenv.app.op("credits.notify.sent"),
        ]
    )
    events = await audit_actions(tenv)
    assert [e.action for e in events] == ["credits.adjust"]
    assert events[0].actor_id == admin.id and events[0].target_id == str(target.id)
    assert events[0].details == {
        "entry_id": entry.id,
        "delta_minor": 123_450,
        "balance_minor": 123_450,
        "currency": "USD",
        "note": "[goodwill]",
    }
    assert len(target.dms) == 1
    notice = target.dms[0].embeds[0]
    assert notice.title == _rendered(tenv, NOTICE_TITLE, amount=_money(tenv, 123_450), balance=_money(tenv, 123_450))
    assert notice.description == _rendered(
        tenv, NOTICE_DESCRIPTION, amount=_money(tenv, 123_450), balance=_money(tenv, 123_450)
    )
    assert "[goodwill]" not in target.dms[0].text()  # the note is internal


async def test_admin_removes_credit(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    await grant(tenv, target.id, 1_000)
    interaction = await invoke(tenv, admin, "adjust", member=target, action="remove", amount="2.5", note="[fix]")
    embed = reply_embed(interaction)
    assert embed.title == tenv.app.op("credits.cmd.removed_title")
    assert await balance(tenv, target.id) == 750
    latest = (await entries(tenv, target.id))[0]
    assert latest.delta_minor == -250
    assert tenv.app.op(
        "credits.cmd.adjusted", member=target.mention, delta=_usd(-250), balance=_usd(750), entry_id=latest.id
    ) in (embed.description or "")
    notice = target.dms[0].embeds[0]
    assert notice.description == _rendered(
        tenv, NOTICE_DESCRIPTION, amount=_money(tenv, -250), balance=_money(tenv, 750)
    )


async def test_removing_more_than_the_balance_is_rejected(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    await grant(tenv, target.id, 500)
    interaction = await invoke(tenv, admin, "adjust", member=target, action="remove", amount="5.01", note="[n]")
    embed = reply_embed(interaction)
    assert embed.title == tenv.app.op("credits.cmd.rejected_title")
    assert embed.description == tenv.app.op(
        "credits.cmd.insufficient", amount=_usd(501), member=target.mention, balance=_usd(500)
    )
    assert await balance(tenv, target.id) == 500
    assert await audit_actions(tenv) == []
    assert target.dms == []


@pytest.mark.parametrize(
    ("amount", "note", "key"),
    [
        ("abc", "[n]", "credits.cmd.invalid_amount"),
        ("1.234", "[n]", "credits.cmd.invalid_amount"),
        ("-5", "[n]", "credits.cmd.invalid_amount"),
        ("1,00", "[n]", "credits.cmd.invalid_amount"),
        ("10000000001", "[n]", "credits.cmd.invalid_amount"),
        ("0", "[n]", "credits.cmd.zero_amount"),
        ("0.00", "[n]", "credits.cmd.zero_amount"),
        ("5", "   ", "credits.cmd.invalid_note"),
    ],
)
async def test_invalid_adjustments_are_rejected(tenv: TestEnv, amount: str, note: str, key: str) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount=amount, note=note)
    embed = reply_embed(interaction)
    assert embed.title == tenv.app.op("credits.cmd.rejected_title")
    # The parser's reason varies; everything around it is the operator template.
    expected = tenv.app.op(key, reason="\0", example="12.50", max=repo.NOTE_MAX_LENGTH)
    prefix, _, suffix = expected.partition("\0")
    assert embed.description is not None
    assert embed.description.startswith(prefix) and embed.description.endswith(suffix)
    assert await entries(tenv, target.id) == []
    assert await audit_actions(tenv) == []


async def test_invalid_amount_reply_shows_the_configured_format(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="x", note="[n]")
    assert (reply_embed(interaction).description or "").endswith("12.50")


async def test_bots_cannot_hold_credit(tenv: TestEnv) -> None:
    admin = tenv.member(admin=True)
    bot_member = tenv.guild.add_member(name="other-bot", bot=True)
    interaction = await invoke(tenv, admin, "adjust", member=bot_member, action="add", amount="5", note="[n]")
    assert reply_embed(interaction).description == tenv.app.op("credits.cmd.bot_member")
    assert await entries(tenv, bot_member.id) == []


async def test_concurrent_removals_never_overdraw(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    await grant(tenv, target.id, 1_000)
    results = await asyncio.gather(
        *(
            service.adjust(
                tenv.app,
                actor_id=admin.id,
                user_id=target.id,
                is_bot=False,
                action="remove",
                amount_text="6",
                note="[n]",
            )
            for _ in range(3)
        )
    )
    assert [r.entry is not None for r in results].count(True) == 1
    assert await balance(tenv, target.id) == 400


async def test_zero_decimal_currency(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"checkout": {"currency": "JPY"}}))
    try:
        admin, target = tenv.member(admin=True), tenv.member(name="target")
        rejected = await invoke(tenv, admin, "adjust", member=target, action="add", amount="1.5", note="[n]")
        assert reply_embed(rejected).title == tenv.app.op("credits.cmd.rejected_title")
        await invoke(tenv, admin, "adjust", member=target, action="add", amount="1,500", note="[n]")
        assert await balance(tenv, target.id) == 1_500
    finally:
        await tenv.close()


# --------------------------------------------------------------------------------------------------
# Member notice
# --------------------------------------------------------------------------------------------------


async def test_no_notice_when_not_configured(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={NOTICE_TITLE: "", NOTICE_DESCRIPTION: ""})
    try:
        assert tenv.app.ready("credits")
        admin, target = tenv.member(admin=True), tenv.member(name="target")
        interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
        assert target.dms == []
        assert tenv.app.op("credits.notify.not_configured") in (reply_embed(interaction).description or "")
        assert await balance(tenv, target.id) == 500
    finally:
        await tenv.close()


async def test_notice_with_only_a_description_has_no_title(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, content={NOTICE_TITLE: ""})
    try:
        admin, target = tenv.member(admin=True), tenv.member(name="target")
        await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
        notice = target.dms[0].embeds[0]
        assert notice.title is None
        assert notice.description == _rendered(
            tenv, NOTICE_DESCRIPTION, amount=_money(tenv, 500), balance=_money(tenv, 500)
        )
    finally:
        await tenv.close()


async def test_no_notice_when_dm_notifications_are_off(tmp_path: Path) -> None:
    tenv = await make_test_env(tmp_path, settings=make_settings({"features": {"dm_notifications": False}}))
    try:
        admin, target = tenv.member(admin=True), tenv.member(name="target")
        interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
        assert target.dms == []
        assert tenv.app.op("credits.notify.dm_disabled") in (reply_embed(interaction).description or "")
    finally:
        await tenv.close()


async def test_closed_dms_are_reported_and_the_adjustment_stands(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    target.dm_forbidden = True
    interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
    assert tenv.app.op("credits.notify.failed") in (reply_embed(interaction).description or "")
    assert await balance(tenv, target.id) == 500
    assert [e.action for e in await audit_actions(tenv)] == ["credits.adjust"]


async def test_notifier_crash_is_reported_and_the_adjustment_stands(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(**kwargs: Any) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(tenv.app.notifier, "notify_user", broken)
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    interaction = await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
    description = reply_embed(interaction).description or ""
    assert description.splitlines()[-1].startswith(tenv.app.op("credits.notify.error", error_id="").split(".")[0])
    assert "boom" not in description
    assert await balance(tenv, target.id) == 500


async def test_notice_is_sent_at_most_once_per_entry(tenv: TestEnv) -> None:
    admin, target = tenv.member(admin=True), tenv.member(name="target")
    await invoke(tenv, admin, "adjust", member=target, action="add", amount="5", note="[n]")
    entry = (await entries(tenv, target.id))[0]
    again = await service.notify_adjustment(tenv.app, entry=entry, balance_minor=500)
    assert again == tenv.app.op("credits.notify.duplicate")
    assert len(target.dms) == 1


# --------------------------------------------------------------------------------------------------
# Staff history
# --------------------------------------------------------------------------------------------------


async def test_history_lists_entries_with_details(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    await grant(tenv, target.id, 2_000, actor_id=staff.id, note="*bold* [reason]")
    order_id, number = await make_order(tenv, target.id)
    await tenv.app.db.transaction(
        repo.debit_for_order,
        guild_id=GUILD_ID,
        user_id=target.id,
        order_id=order_id,
        amount_minor=500,
        now=tenv.clock.now(),
    )
    interaction = await invoke(tenv, staff, "history", member=target)
    embed = reply_embed(interaction)
    assert embed.description == tenv.app.op(
        "credits.history.summary", member=target.mention, balance=_usd(1_500), count=2
    )
    debit_field, grant_field = embed.fields
    assert debit_field.name == tenv.app.op("credits.history.entry", entry_id=2, delta=_usd(-500))
    assert tenv.app.op("credits.kind.order_debit") in (debit_field.value or "")
    assert tenv.app.op("credits.history.order", order_number=number) in (debit_field.value or "")
    assert tenv.app.op("credits.history.by_system") in (debit_field.value or "")
    assert grant_field.name == tenv.app.op("credits.history.entry", entry_id=1, delta="+" + _usd(2_000))
    assert f"<@{staff.id}>" in (grant_field.value or "")
    assert "\\*bold\\*" in (grant_field.value or "")  # staff-typed note is escaped
    assert interaction.last_sent is not None and interaction.last_sent.components() == []


async def test_history_of_member_without_entries(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    embed = reply_embed(await invoke(tenv, staff, "history", member=target))
    assert embed.description == "\n".join(
        [
            tenv.app.op("credits.history.summary", member=target.mention, balance=_usd(0), count=0),
            tenv.app.op("credits.history.empty"),
        ]
    )
    assert embed.fields == []


async def test_history_paging_buttons(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    total = views.HISTORY_PAGE_SIZE * 2 + 3
    for amount in range(1, total + 1):
        await grant(tenv, target.id, amount)
    interaction = await invoke(tenv, staff, "history", member=target, page=2)
    message = interaction.last_sent
    assert message is not None
    assert message.embeds[0].footer.text == tenv.app.op("credits.history.page", page=2, pages=3)
    assert buttons(message) == [
        (f"sb:cr:hpage:{target.id}:0", tenv.app.op("credits.history.previous"), False),
        (f"sb:cr:hpage:{target.id}:2", tenv.app.op("credits.history.next"), False),
    ]
    clicked = await click(tenv.app, user=staff, custom_id=f"sb:cr:hpage:{target.id}:2", message=message)
    assert clicked.response_kinds() == ["edit_message"]
    assert message.embeds[0].footer.text == tenv.app.op("credits.history.page", page=3, pages=3)
    assert len(message.embeds[0].fields) == 3
    assert buttons(message)[1][2] is True  # next disabled on the last page
    # Out-of-range pages are clamped to the last page.
    await click(tenv.app, user=staff, custom_id=f"sb:cr:hpage:{target.id}:99", message=message)
    assert message.embeds[0].footer.text == tenv.app.op("credits.history.page", page=3, pages=3)
    # Command page beyond the end is clamped too.
    far = await invoke(tenv, staff, "history", member=target, page=50)
    assert reply_embed(far).footer.text == tenv.app.op("credits.history.page", page=3, pages=3)


async def test_history_buttons_are_staff_only(tenv: TestEnv) -> None:
    customer, target = tenv.member(), tenv.member(name="target")
    await grant(tenv, target.id, 5)
    interaction = await click(tenv.app, user=customer, custom_id=f"sb:cr:hpage:{target.id}:0")
    assert len(interaction.sent) == 1
    embed = reply_embed(interaction)
    assert embed.description == tenv.text(ck.ERROR_PERMISSION)
    assert embed.fields == []


async def test_forged_history_button_ids_answer_not_found(tenv: TestEnv) -> None:
    staff = tenv.member(staff=True)
    for custom_id in ("sb:cr:hpage", "sb:cr:hpage:abc:0", "sb:cr:hpage:1:-1"):
        interaction = await click(tenv.app, user=staff, custom_id=custom_id)
        assert reply_embed(interaction).description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_history_page_edit_failure_is_answered(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    for amount in range(1, views.HISTORY_PAGE_SIZE + 3):
        await grant(tenv, target.id, amount)
    interaction = await invoke(tenv, staff, "history", member=target)
    message = interaction.last_sent
    assert message is not None
    message.deleted = True  # editing now fails with NotFound
    clicked = await click(tenv.app, user=staff, custom_id=f"sb:cr:hpage:{target.id}:1", message=message)
    assert reply_embed(clicked).description == tenv.text(ck.ERROR_NOT_FOUND)


async def test_history_buttons_work_after_restart(tenv: TestEnv) -> None:
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    for amount in range(1, views.HISTORY_PAGE_SIZE + 3):
        await grant(tenv, target.id, amount)
    interaction = await invoke(tenv, staff, "history", member=target)
    message = interaction.last_sent
    assert message is not None
    app = await restarted_app(tenv)
    clicked = await click(app, user=staff, custom_id=f"sb:cr:hpage:{target.id}:1", message=message)
    assert clicked.response_kinds() == ["edit_message"]
    assert message.embeds[0].footer.text == tenv.app.op("credits.history.page", page=2, pages=2)


def test_worst_case_history_page_fits_discord_limits() -> None:
    """A full page with the longest escaped notes, largest amounts and every detail line stays within limits."""
    from shopbot.config.content import ContentStore
    from shopbot.config.content_keys import Registry
    from shopbot.config.operator_text import render_operator
    from shopbot.core.clock import FakeClock
    from shopbot.features.credits.text import OPERATOR_TEXT
    from shopbot.ui.style import Ui

    settings = make_settings()
    ui = Ui(lambda: settings, ContentStore(Registry()), FakeClock())

    def op(key: str, **values: object) -> str:
        return render_operator(OPERATOR_TEXT[key], dict(values))

    worst = tuple(
        repo.Entry(
            id=10**9 + index,
            guild_id=GUILD_ID,
            user_id=10**18,
            delta_minor=-(10**12),
            kind=repo.KIND_ORDER_RETURN,
            order_id=10**9,
            actor_id=10**18 + index,
            note="*" * repo.NOTE_MAX_LENGTH,
            created_at=4_000_000_000,
            order_number=10**9,
        )
        for index in range(views.HISTORY_PAGE_SIZE)
    )
    page = views.HistoryPage(user_id=10**18, page=5, pages=10**5, total=10**6, balance_minor=10**15, entries=worst)
    spec = views.history_view(ui, op, lambda minor: f"{format_amount(minor, 4)} XXX", page)
    assert validate_message(spec) == []


async def test_balance_lookup_for_member_not_in_the_member_cache(tenv: TestEnv) -> None:
    """Ledger rows are keyed by user ID only, so lookups do not depend on the member cache."""
    staff = tenv.member(staff=True)
    target = FakeMember(tenv.guild, name="left")
    await grant(tenv, target.id, 77)
    shown = await invoke(tenv, staff, "balance", member=target)
    assert reply_embed(shown).description == tenv.app.op("credits.cmd.balance", member=target.mention, balance=_usd(77))


async def test_reply_failure_propagates_to_the_tree_error_handler(
    tenv: TestEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Discord error while replying is not swallowed: discord.py hands it to on_tree_error (logged + reported)."""
    staff, target = tenv.member(staff=True), tenv.member(name="target")
    command = credits_command(tenv.app, "balance")
    from tests.fakes import FakeInteraction

    interaction = FakeInteraction(
        client=tenv.bot, user=staff, guild=tenv.guild, type=discord.InteractionType.application_command
    )

    async def failing_send(*args: Any, **kwargs: Any) -> Any:
        raise http_error(discord.HTTPException, 500, 0, "server error")

    monkeypatch.setattr(interaction.followup, "send", failing_send)
    with pytest.raises(discord.HTTPException):
        await command.callback(interaction, member=target)
    assert await balance(tenv, target.id) == 0


async def test_admin_can_adjust_a_user_who_left(tenv: TestEnv) -> None:
    admin, departed = tenv.member(admin=True), FakeUser(name="departed")
    await grant(tenv, departed.id, 900)
    interaction = await invoke(tenv, admin, "adjust", member=departed, action="remove", amount="9", note="[closing]")
    embed = reply_embed(interaction)
    assert embed.title == tenv.app.op("credits.cmd.removed_title")
    assert tenv.app.op("credits.notify.failed") in (embed.description or "")  # not reachable by the bot
    assert await balance(tenv, departed.id) == 0
    history = await invoke(tenv, tenv.member(staff=True), "history", member=departed)
    assert reply_embed(history).description == tenv.app.op(
        "credits.history.summary", member=departed.mention, balance=_usd(0), count=2
    )
