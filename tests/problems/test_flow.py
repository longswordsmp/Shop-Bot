"""The customer flow: #purchase button -> private choice step -> form -> private ticket with the report panel."""

from __future__ import annotations

from pathlib import Path

import discord

from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import service, views
from shopbot.features.server_keys import channel_mentions
from shopbot.features.tickets import content_keys as tk
from shopbot.features.tickets.models import KIND_SUPPORT, OPEN
from shopbot.ui.custom_ids import cid
from tests.fakes import click, submit_modal
from tests.helpers import TestEnv
from tests.problems.util import (
    MULTI_TYPES,
    add_order,
    audit_actions,
    channel,
    continue_id_of,
    last_description,
    make_env,
    nonce_of,
    order_notes,
    order_number,
    panel_message,
    pick,
    private_step,
    render,
    report_about_order,
    report_without_order,
    reports,
    start,
    submit_form,
    task,
    ticket_channel,
    ticket_of,
)


def _overwrite(ch: discord.TextChannel, target_id: int) -> discord.PermissionOverwrite | None:
    for target, overwrite in ch.overwrites.items():
        if target.id == target_id:
            return overwrite
    return None


async def test_report_without_an_order_opens_the_form_right_away(pr: TestEnv) -> None:
    user = pr.member()
    opened = await start(pr, user)
    modal = opened.response.modal
    assert modal is not None  # nothing to choose: no eligible order, one issue type
    assert modal.title == pr.text(k.type_key("general", "form_title"))

    report, submitted = await report_without_order(pr, user, "the  *box* was empty")
    assert report.status == k.OPEN and report.order_id is None and report.issue_type == "general"
    assert report.answers == {"details": "the  *box* was empty"}
    ticket = await ticket_of(pr, report)
    assert ticket.kind == KIND_SUPPORT and ticket.category_id == k.TICKET_CATEGORY and ticket.status == OPEN
    assert ticket.control_message_id is None  # the report panel is not a ticket-control message

    ch = await ticket_channel(pr, report)
    number = service.number_text(pr.app, ticket.number)
    # Private reply (the form came from the public panel: a new private message, the panel is never edited).
    reply = submitted.last_sent
    assert reply is not None and reply.flags.ephemeral
    assert reply.embeds[0].description == render(pr, k.CREATED, channel_mention=ch.mention, number=number)
    assert reply.embeds[1].description == pr.text(k.ATTACHMENTS_NOTE)

    # The ticket: private to the customer and staff, in the configured category, named with the prefix.
    category = pr.app.resources.category(pr.guild, "tickets")  # type: ignore[arg-type]
    assert category is not None and ch.category_id == category.id
    assert ch.name.startswith(pr.text(k.CHANNEL_PREFIX))
    everyone = _overwrite(ch, pr.guild.default_role.id)
    assert everyone is not None and everyone.view_channel is False
    owner = _overwrite(ch, user.id)
    assert owner is not None and owner.view_channel and owner.attach_files  # screenshots/files can be uploaded
    staff = _overwrite(ch, pr.staff_role.id)
    assert staff is not None and staff.view_channel
    other = pr.member(name="other")
    assert not ch.permissions_for(other).view_channel

    # The report panel (pings the customer) and the answers below it, escaped.
    panel = await panel_message(pr, report)
    assert panel is ch.messages[0]
    assert panel.content == f"<@{user.id}>"
    main = panel.embeds[0]
    label = pr.text(k.type_key("general", "label"))
    assert main.title == render(pr, k.TICKET_TITLE, number=number, type_label=label, user_name="customer")
    fields = {f.name: f.value for f in main.fields}
    assert fields[pr.text(k.FIELD_TYPE)] == label
    assert fields[pr.text(k.FIELD_STATUS)] == pr.text(k.status_key(k.OPEN))
    assert pr.text(k.FIELD_ORDER) not in fields  # not about an order
    assert panel.embeds[1].description == pr.text(k.ATTACHMENTS_NOTE)
    assert panel.custom_ids() == [
        views.resolve_id(report.id),
        views.reject_id(report.id),
        cid("tk", "close", ticket.id),
    ]
    details = ch.messages[1]
    assert details.embeds[0].fields[0].name == pr.text(k.form_field_key("general", "details", "label"))
    assert details.embeds[0].fields[0].value == r"the  \*box\* was empty"
    assert len(ch.messages) == 2

    # Staff alert in the staff alert channel (no report details there), audit, no task left behind.
    alerts = channel(pr, "staff_alerts").messages
    assert len(alerts) == 1
    assert alerts[0].embeds[0].title == render(pr, k.STAFF_ALERT_TITLE, number=number, type_label=label)
    assert "box" not in alerts[0].text()
    assert await audit_actions(pr) == ["problems.reported"]
    finish = await task(pr, f"problems.finish:{report.id}")
    assert finish is not None and finish.status == "cancelled"


async def test_report_about_an_order(pr: TestEnv) -> None:
    user = pr.member()
    older = await add_order(pr, user, status="fulfilled")
    newer = await add_order(pr, user, status="paid", total=2500)
    await add_order(pr, user, status="pending_payment")  # not eligible
    await add_order(pr, pr.member(name="someone else"))  # not theirs

    step = private_step(await start(pr, user))
    assert step.embeds[0].description == render(pr, k.CHOOSE_DESCRIPTION, **channel_mentions(pr.app))
    assert step.embeds[1].description == pr.text(k.ATTACHMENTS_NOTE)
    menu = step.components()[0].item
    assert [o.value for o in menu.options] == [str(newer), str(older), views.NO_ORDER]  # newest first
    total = pr.app.ui.money(2500)
    assert total is not None
    expected = render(
        pr,
        k.ORDER_OPTION,
        order_number=await order_number(pr, newer),
        total=total,
        status=pr.text(checkout_keys.status_label_key("paid")),
    )
    assert menu.options[0].label == expected
    assert menu.options[-1].label == pr.text(k.NO_ORDER_OPTION)
    assert menu.placeholder == pr.text(k.ORDER_PLACEHOLDER)
    proceed = step.components()[1].item
    assert proceed.label == pr.text(core.BUTTON_CONTINUE) and proceed.disabled  # nothing chosen yet

    report, _ = await report_about_order(pr, user, newer, "wrong item")
    assert report.order_id == newer
    panel = await panel_message(pr, report)
    fields = {f.name: f.value for f in panel.embeds[0].fields}
    assert fields[pr.text(k.FIELD_ORDER)] == render(
        pr,
        k.ORDER_VALUE,
        order_number=await order_number(pr, newer),
        total=total,
        status=pr.text(checkout_keys.status_label_key("paid")),
    )
    notes = await order_notes(pr, newer)
    ticket = await ticket_of(pr, report)
    assert notes == [
        pr.app.op("problems.order_note.reported", number=service.number_text(pr.app, ticket.number), type="general")
    ]


async def test_picking_marks_the_choice_and_continue_works_repeatedly(pr: TestEnv) -> None:
    user = pr.member()
    order_id = await add_order(pr, user)
    step = private_step(await start(pr, user))
    await pick(pr, user, step, "order", views.NO_ORDER)
    menu = step.components()[0].item
    assert [o.default for o in menu.options] == [False, True]
    proceed = step.components()[1].item
    assert not proceed.disabled
    # The form can be closed and reopened as often as needed (the button fires again).
    for _ in range(2):
        again = await click(pr.app, user=user, custom_id=continue_id_of(step), message=step)
        assert again.response.modal is not None
    await pick(pr, user, step, "order", str(order_id))
    assert [o.default for o in step.components()[0].item.options] == [True, False]
    form = await click(pr.app, user=user, custom_id=continue_id_of(step), message=step)
    assert form.response.modal is not None
    submitted = await submit_form(pr, user, str(form.response.modal.custom_id), message=step)
    # The private step is replaced by the confirmation (menus removed); no extra message is sent.
    (report,) = await reports(pr)
    ch = await ticket_channel(pr, report)
    number = service.number_text(pr.app, (await ticket_of(pr, report)).number)
    assert step.custom_ids() == []
    assert step.embeds[0].description == render(pr, k.CREATED, channel_mention=ch.mention, number=number)
    assert submitted.sent == []


async def test_several_types_and_a_type_without_form(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, MULTI_TYPES)
    try:
        user = tenv.member()
        step = private_step(await start(tenv, user))
        # No eligible order: the order is chosen automatically ('not about an order'); only the type menu shows.
        assert [c for c in step.custom_ids() if c.startswith("sb:pr:order:")] == []
        menu = step.components()[0].item
        assert [o.value for o in menu.options] == ["general", "billing", "other"]
        assert menu.options[1].label == tenv.text(k.type_key("billing", "label"))
        assert menu.options[1].description == tenv.text(k.type_key("billing", "description"))
        assert menu.placeholder == tenv.text(k.TYPE_PLACEHOLDER)

        await pick(tenv, user, step, "type", "billing")
        proceed = await click(tenv.app, user=user, custom_id=continue_id_of(step), message=step)
        modal = proceed.response.modal
        assert modal is not None and modal.title == tenv.text(k.type_key("billing", "form_title"))
        # Required field missing -> invalid input, nothing created.
        bad = await submit_form(tenv, user, str(modal.custom_id), {"what": "  ", "extra": "x"}, message=step)
        assert last_description(bad) == tenv.text(core.ERROR_INVALID_INPUT)
        assert await reports(tenv) == []
        await submit_form(tenv, user, str(modal.custom_id), {"what": "double charge", "extra": ""}, message=step)
        (report,) = await reports(tenv)
        assert report.issue_type == "billing" and report.answers == {"what": "double charge"}

        # A type without a form creates the report from Continue directly.
        other = tenv.member(name="other")
        step = private_step(await start(tenv, other))
        await pick(tenv, other, step, "type", "other")
        created = await click(tenv.app, user=other, custom_id=continue_id_of(step), message=step)
        assert created.response.modal is None
        assert len(await reports(tenv)) == 2
        assert step.embeds[0].description is not None and step.custom_ids() == []
        report = (await reports(tenv))[-1]
        ch = await ticket_channel(tenv, report)
        assert len(ch.messages) == 1  # the panel only: no answers to show
    finally:
        await tenv.close()


async def test_without_allow_without_order(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"problems": {"allow_without_order": False}})
    try:
        user = tenv.member()
        none = await start(tenv, user)
        assert last_description(none) == render(tenv, k.NO_ORDERS, **channel_mentions(tenv.app))
        assert none.response.modal is None
        order_id = await add_order(tenv, user)
        # A single eligible order is chosen automatically: the form opens right away.
        opened = await start(tenv, user)
        modal = opened.response.modal
        assert modal is not None and f":{order_id}:general" in str(modal.custom_id)
        # The 'not about an order' choice cannot be forged.
        forged = str(modal.custom_id).replace(f":{order_id}:", ":0:")
        refused = await submit_form(tenv, user, forged)
        assert last_description(refused) == tenv.text(core.ERROR_INVALID_INPUT)
        await submit_form(tenv, user, str(modal.custom_id), message=opened.message)
        (report,) = await reports(tenv)
        assert report.order_id == order_id
    finally:
        await tenv.close()


async def test_order_menu_holds_at_most_25_options(pr: TestEnv) -> None:
    user = pr.member()
    ids = [await add_order(pr, user, status="fulfilled") for _ in range(30)]
    step = private_step(await start(pr, user))
    menu = step.components()[0].item
    assert len(menu.options) == 25
    assert [o.value for o in menu.options[:24]] == [str(i) for i in reversed(ids[-24:])]
    assert menu.options[-1].value == views.NO_ORDER


async def test_an_order_of_someone_else_or_not_eligible_is_refused(pr: TestEnv) -> None:
    user = pr.member()
    mine = await add_order(pr, user)
    theirs = await add_order(pr, pr.member(name="other"))
    step = private_step(await start(pr, user))
    picked = await pick(pr, user, step, "order", str(theirs))
    assert last_description(picked) == pr.text(core.ERROR_INVALID_INPUT)
    assert [o.default for o in step.components()[0].item.options] == [False, False]  # re-rendered, nothing chosen
    # A forged form id with someone else's order is refused atomically on creation.
    nonce = nonce_of(continue_id_of(step))
    forged = await submit_form(pr, user, views.form_id(nonce, str(theirs), "general"))
    assert last_description(forged) == pr.text(core.ERROR_INVALID_INPUT)
    # An order that stopped being eligible meanwhile is refused too.
    await pick(pr, user, step, "order", str(mine))
    await pr.app.db.transaction(
        lambda conn: conn.execute("UPDATE orders SET status = 'refunded' WHERE id = ?", (mine,))
    )
    late = await submit_form(pr, user, views.form_id(nonce, str(mine), "general"))
    assert last_description(late) == pr.text(core.ERROR_INVALID_INPUT)
    assert await reports(pr) == []


async def test_the_same_form_submitted_twice_creates_one_report(pr: TestEnv) -> None:
    user = pr.member()
    opened = await start(pr, user)
    assert opened.response.modal is not None
    custom_id = str(opened.response.modal.custom_id)
    first = await submit_form(pr, user, custom_id)
    second = await submit_form(pr, user, custom_id)
    (report,) = await reports(pr)
    ch = await ticket_channel(pr, report)
    assert last_description(first) == last_description(second)  # same confirmation, same ticket
    assert ch.mention in (last_description(second) or "")
    assert len([c for c in pr.guild.text_channels if c.name.startswith(pr.text(k.CHANNEL_PREFIX))]) == 1


async def test_open_report_limit(pr: TestEnv) -> None:
    user = pr.member()
    report, _ = await report_without_order(pr, user)
    ch = await ticket_channel(pr, report)
    pr.clock.advance(pr.app.settings.tickets.creation_cooldown_seconds + 1)
    refused = await start(pr, user)
    assert refused.response.modal is None
    assert last_description(refused) == render(pr, k.LIMIT_REACHED, limit=1, channel_mention=ch.mention)
    # A forged form submission is refused in the creation transaction as well.
    forged = await submit_form(pr, user, views.form_id("f" * 16, views.NO_ORDER, "general"))
    assert last_description(forged) == render(pr, k.LIMIT_REACHED, limit=1, channel_mention=ch.mention)
    assert len(await reports(pr)) == 1


async def test_limit_counts_only_undecided_reports_with_an_open_ticket(pr: TestEnv) -> None:
    from shopbot.features.tickets import service as tickets_service

    user = pr.member()
    report, _ = await report_without_order(pr, user)
    pr.clock.advance(60)
    await tickets_service.close_ticket(pr.app, report.ticket_id, actor_id=user.id)  # closed without a decision
    opened = await start(pr, user)
    assert opened.response.modal is not None  # a new report is possible again


async def test_creation_cooldown(pr: TestEnv) -> None:
    user = pr.member()
    report, _ = await report_without_order(pr, user)
    await pr.app.db.transaction(
        lambda conn: conn.execute("UPDATE problem_reports SET status = 'resolved', decided_by = 1, decided_at = 1")
    )
    del report
    soon = await start(pr, user)
    assert soon.response.modal is None
    assert last_description(soon) is not None and soon.last_sent is not None
    assert soon.last_sent.embeds[0].title == pr.text(core.ERROR_TITLE)
    pr.clock.advance(pr.app.settings.tickets.creation_cooldown_seconds + 1)
    assert (await start(pr, user)).response.modal is not None


async def test_daily_ticket_cap_applies(tmp_path: Path) -> None:
    tenv = await make_env(
        tmp_path, {"tickets": {"max_created_per_user_per_day": 1}, "problems": {"max_open_per_user": 5}}
    )
    try:
        user = tenv.member()
        await report_without_order(tenv, user)
        tenv.clock.advance(tenv.app.settings.tickets.creation_cooldown_seconds + 1)
        opened = await start(tenv, user)
        assert opened.response.modal is not None
        refused = await submit_form(tenv, user, str(opened.response.modal.custom_id))
        assert last_description(refused) == render(tenv, tk.LIMIT_REACHED, limit=1)
        assert len(await reports(tenv)) == 1
    finally:
        await tenv.close()


async def test_invalid_form_input_is_refused(pr: TestEnv) -> None:
    user = pr.member()
    opened = await start(pr, user)
    assert opened.response.modal is not None
    custom_id = str(opened.response.modal.custom_id)
    for value in ("", "  ", "ab", "x" * 1001):
        refused = await submit_form(pr, user, custom_id, {"details": value})
        assert last_description(refused) == pr.text(core.ERROR_INVALID_INPUT), value
    unknown_type = await submit_form(pr, user, views.form_id(nonce_of(custom_id), views.NO_ORDER, "nope"))
    assert last_description(unknown_type) == pr.text(core.ERROR_NOT_FOUND)
    bad_nonce = await submit_modal(
        pr.app, user=user, custom_id=cid("pr", "form", "NOTANONCE", "0", "general"), fields={"details": "abcd"}
    )
    assert last_description(bad_nonce) == pr.text(core.ERROR_NOT_FOUND)
    assert await reports(pr) == []


async def test_staff_ping_with_the_alert_or_on_the_panel(tmp_path: Path) -> None:
    for alert_channel in ("staff_alerts", ""):
        tenv = await make_env(
            tmp_path / (alert_channel or "panel"),
            {"problems": {"ping_staff": True}, "tickets": {"staff_alert_channel": alert_channel}},
        )
        try:
            report, _ = await report_without_order(tenv, tenv.member())
            panel = await panel_message(tenv, report)
            role = f"<@&{tenv.staff_role.id}>"
            if alert_channel:
                assert role not in panel.content
                assert channel(tenv, "staff_alerts").messages[0].content == role
            else:
                assert role in panel.content
                assert channel(tenv, "staff_alerts").messages == []
        finally:
            await tenv.close()


async def test_concurrent_reports_respect_the_open_limit(pr: TestEnv) -> None:
    import asyncio

    user = pr.member()
    forms = [views.form_id(nonce * 16, views.NO_ORDER, "general") for nonce in "abc"]
    await asyncio.gather(*(submit_form(pr, user, form) for form in forms))
    assert len(await reports(pr)) == 1
    assert len([c for c in pr.guild.text_channels if c.name.startswith(pr.text(k.CHANNEL_PREFIX))]) == 1


async def test_a_dismissed_private_step_still_gets_the_confirmation(pr: TestEnv) -> None:
    from tests.fakes import http_error

    user = pr.member()
    await add_order(pr, user)
    step = private_step(await start(pr, user))
    await pick(pr, user, step, "order", views.NO_ORDER)
    form = await click(pr.app, user=user, custom_id=continue_id_of(step), message=step)
    assert form.response.modal is not None

    async def gone(**kwargs: object) -> None:
        raise http_error(discord.NotFound, 404, 10008, "Unknown Message")

    step.edit = gone  # type: ignore[method-assign]
    submitted = await submit_form(pr, user, str(form.response.modal.custom_id), message=step)
    (report,) = await reports(pr)
    ch = await ticket_channel(pr, report)
    assert submitted.last_sent is not None and submitted.last_sent.flags.ephemeral
    assert ch.mention in (last_description(submitted) or "")
