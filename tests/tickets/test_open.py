from __future__ import annotations

import asyncio
from pathlib import Path

import discord
from discord.utils import escape_markdown

from shopbot.features import core_keys as core
from shopbot.features.tickets import MODULE, service
from shopbot.features.tickets import content_keys as k
from shopbot.ui.custom_ids import cid
from tests.fakes import click, http_error, modal_input_ids, modal_of, submit_modal
from tests.helpers import TestEnv
from tests.tickets.util import (
    advance_past_cooldown,
    audit_actions,
    category,
    channel,
    control_message,
    form_overrides,
    make_env,
    number,
    open_ticket,
    overwrite_for,
    render,
    tickets,
)


def _labels(message) -> list[str]:  # type: ignore[no-untyped-def]
    return [getattr(item, "item", item).label for item in message.components()]


async def test_ping_staff_without_alert_channel_pings_in_welcome(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, form_overrides({"tickets": {"staff_alert_channel": ""}}))
    try:
        customer = tenv.member()
        await submit_modal(
            tenv.app, user=customer, custom_id=cid("tk", "form", "billing"), fields={"details": "need help"}
        )
        [ticket] = await tickets(tenv)
        welcome = control_message(tenv.guild.get_channel(ticket.channel_id), ticket)
        billing_role = tenv.app.resources.role_id("billing_staff")
        assert welcome.content is not None
        assert welcome.content.split() == [
            customer.mention,
            *sorted((f"<@&{tenv.staff_role.id}>", f"<@&{billing_role}>"), key=lambda m: int(m[3:-1])),
        ]
        mentions = welcome.allowed_mentions
        assert mentions is not None and mentions.everyone is False and mentions.roles
    finally:
        await tenv.close()


async def test_open_via_button_creates_private_channel_with_welcome(tk: TestEnv) -> None:
    customer = tk.member(name="Cust_*omer")
    interaction, ticket, ch = await open_ticket(tk, customer)

    assert ticket.status == "open" and ticket.kind == "support" and ticket.number == 1
    assert ticket.category_id == "general" and ticket.user_id == customer.id
    # Location and name: configured prefix + padded number, inside the configured Discord category.
    assert ch.category_id == category(tk, "tickets").id
    assert ch.name == f"{tk.text(k.category_key('general', 'channel_prefix'))}-{number(tk, 1)}"
    assert ch.topic == render(
        tk, k.category_key("general", "channel_topic"), number=number(tk, 1), user_name=escape_markdown("Cust_*omer")
    )
    # Private overwrites: @everyone denied; owner, staff role and bot allowed.
    everyone = overwrite_for(ch, tk.guild.default_role.id)
    assert everyone is not None and everyone.view_channel is False
    for target in (customer.id, tk.staff_role.id, tk.guild.me.id):
        overwrite = overwrite_for(ch, target)
        assert overwrite is not None and overwrite.view_channel and overwrite.send_messages
    assert ch.permissions_for(customer).view_channel
    assert not ch.permissions_for(tk.member(name="stranger")).view_channel
    assert ch.permissions_for(tk.member(name="helper", staff=True)).view_channel

    # Welcome message: configured text with placeholders, pings the customer, close + claim controls.
    welcome = control_message(ch, ticket)
    values = {"user_mention": customer.mention, "user_name": escape_markdown("Cust_*omer"), "number": number(tk, 1)}
    assert welcome.embeds[0].title == render(tk, k.category_key("general", "welcome_title"), **values)
    assert welcome.embeds[0].description == render(tk, k.category_key("general", "welcome_description"), **values)
    assert welcome.content == customer.mention
    assert welcome.custom_ids() == [cid("tk", "close", ticket.id), cid("tk", "claim", ticket.id)]
    assert _labels(welcome) == [tk.text(k.BUTTON_CLOSE), tk.text(k.BUTTON_CLAIM)]

    # Private confirmation after a deferred ("thinking") response.
    assert interaction.response_kinds() == ["defer"]
    reply = interaction.last_sent
    assert reply is not None and reply.ephemeral  # type: ignore[attr-defined]
    assert reply.embeds[0].description == render(tk, k.CREATED, channel_mention=ch.mention, number=number(tk, 1))

    # Staff alert (no ping: ping_staff is off for 'general').
    alerts = channel(tk, "staff_alerts").messages
    assert len(alerts) == 1
    assert alerts[0].embeds[0].description == render(
        tk,
        k.STAFF_ALERT_DESCRIPTION,
        user_mention=customer.mention,
        channel_mention=ch.mention,
        number=number(tk, 1),
        category_label=tk.text(k.category_key("general", "button_label")),
    )
    assert alerts[0].content == ""
    assert "tickets.created" in await audit_actions(tk)


async def test_select_panel_opens_selected_category(tkf: TestEnv) -> None:
    customer = tkf.member()
    interaction = await click(tkf.app, user=customer, custom_id=cid("tk", "pick"), values=["general"])
    created = await tickets(tkf)
    assert len(created) == 1 and created[0].category_id == "general"
    assert interaction.last_sent is not None
    bad = await click(tkf.app, user=tkf.member(), custom_id=cid("tk", "pick"), values=["nope"])
    assert bad.last_sent is not None and bad.last_sent.embeds[0].description == tkf.text(core.ERROR_NOT_FOUND)


async def test_limit_and_cooldown(tk: TestEnv) -> None:
    customer = tk.member()
    await open_ticket(tk, customer)
    again = await click(tk.app, user=customer, custom_id=cid("tk", "open", "general"))
    assert again.last_sent is not None
    retry = tk.app.settings.tickets.creation_cooldown_seconds
    assert again.last_sent.embeds[0].description == render(tk, core.ERROR_COOLDOWN, retry_after=retry)
    advance_past_cooldown(tk)
    limited = await click(tk.app, user=customer, custom_id=cid("tk", "open", "general"))
    assert limited.last_sent is not None
    assert limited.last_sent.embeds[0].description == render(tk, k.LIMIT_REACHED, limit=1)
    assert len(await tickets(tk)) == 1
    # Another customer is not affected.
    await open_ticket(tk, tk.member(name="other"))
    assert len(await tickets(tk)) == 2


async def test_concurrent_double_click_creates_one_ticket(tk: TestEnv) -> None:
    customer = tk.member()
    custom_id = cid("tk", "open", "general")
    first, second = await asyncio.gather(
        click(tk.app, user=customer, custom_id=custom_id), click(tk.app, user=customer, custom_id=custom_id)
    )
    assert len(await tickets(tk)) == 1
    created_channels = [c for kind, c in tk.guild.audit if kind == "create_text_channel"]
    assert len(created_channels) == 1
    assert first.response.is_done() and second.response.is_done()


async def test_concurrent_service_calls_respect_limit_atomically(tk: TestEnv) -> None:
    customer = tk.member()
    general = tk.app.settings.tickets.category("general")
    assert general is not None
    results = await asyncio.gather(
        service.open_support_ticket(tk.app, member=customer, category=general, answers={}),
        service.open_support_ticket(tk.app, member=customer, category=general, answers={}),
        return_exceptions=True,
    )
    assert sum(isinstance(r, service.CreatedChannel) for r in results) == 1
    assert sum(isinstance(r, service.TicketLimitReached) for r in results) == 1
    assert [t.status for t in await tickets(tk)] == ["open"]
    assert len([c for kind, c in tk.guild.audit if kind == "create_text_channel"]) == 1


async def test_form_flow_shows_modal_and_answers_as_escaped_fields(tkf: TestEnv) -> None:
    customer = tkf.member()
    opened = await click(tkf.app, user=customer, custom_id=cid("tk", "open", "billing"))
    modal = modal_of(opened)
    assert modal is not None
    assert modal.title == tkf.text(k.category_key("billing", "form_title"))
    assert modal.custom_id == cid("tk", "form", "billing")
    assert modal_input_ids(modal) == ["details", "reference"]
    assert await tickets(tkf) == []  # nothing is created before the form is submitted

    answer = "Needs *help* with **this** <@&1> @everyone"
    submitted = await submit_modal(
        tkf.app,
        user=customer,
        custom_id=cid("tk", "form", "billing"),
        fields={"details": answer, "reference": "   "},
    )
    [ticket] = await tickets(tkf)
    assert ticket.form_answers == {"details": answer}
    ch = tkf.guild.get_channel(ticket.channel_id)
    welcome = control_message(ch, ticket)
    fields = welcome.embeds[0].fields
    assert [(f.name, f.value) for f in fields] == [
        (tkf.text(k.form_field_key("billing", "details", "label")), escape_markdown(answer))
    ]
    mentions = welcome.allowed_mentions
    assert mentions is not None and mentions.everyone is False
    assert submitted.last_sent is not None and submitted.last_sent.ephemeral  # type: ignore[attr-defined]
    # ping_staff: the staff alert pings the category's staff roles (global staff + billing staff).
    alert = channel(tkf, "staff_alerts").messages[-1]
    billing_role = tkf.app.resources.role_id("billing_staff")
    assert alert.content == f"<@&{tkf.staff_role.id}> <@&{billing_role}>" or alert.content == (
        f"<@&{billing_role}> <@&{tkf.staff_role.id}>"
    )


async def test_form_validation_rejects_invalid_answers(tkf: TestEnv) -> None:
    customer = tkf.member()
    form_id = cid("tk", "form", "billing")
    missing = await submit_modal(tkf.app, user=customer, custom_id=form_id, fields={"details": "  "})
    too_short = await submit_modal(tkf.app, user=customer, custom_id=form_id, fields={"details": "ab"})
    too_long = await submit_modal(tkf.app, user=customer, custom_id=form_id, fields={"details": "x" * 4001})
    for interaction in (missing, too_short, too_long):
        assert interaction.last_sent is not None
        assert interaction.last_sent.embeds[0].description == tkf.text(core.ERROR_INVALID_INPUT)
    assert await tickets(tkf) == []


async def test_form_limit_is_rechecked_on_submit(tkf: TestEnv) -> None:
    customer = tkf.member()
    form_id = cid("tk", "form", "billing")
    for _ in range(2):  # billing allows 2 open tickets per user
        await submit_modal(tkf.app, user=customer, custom_id=form_id, fields={"details": "need help"})
        advance_past_cooldown(tkf)
    third = await submit_modal(tkf.app, user=customer, custom_id=form_id, fields={"details": "need help"})
    assert third.last_sent is not None
    assert third.last_sent.embeds[0].description == render(tkf, k.LIMIT_REACHED, limit=2)
    assert len(await tickets(tkf)) == 2


async def test_long_answers_are_split_without_losing_text(tkf: TestEnv) -> None:
    customer = tkf.member()
    answer = "\n".join(f"line {i} " + "y" * 60 for i in range(60))[:4000]
    await submit_modal(tkf.app, user=customer, custom_id=cid("tk", "form", "billing"), fields={"details": answer})
    [ticket] = await tickets(tkf)
    ch = tkf.guild.get_channel(ticket.channel_id)
    values = [f.value for m in ch.messages for e in m.embeds for f in e.fields]
    assert all(len(v) <= 1024 for v in values)
    assert "".join(v.replace("\n", "") for v in values) == answer.strip().replace("\n", "")


async def test_answers_that_do_not_fit_continue_in_extra_messages(tkf: TestEnv) -> None:
    customer = tkf.member()
    answer = "*" * 4000  # escaping doubles it to 8000 characters: more than one message can hold
    await submit_modal(tkf.app, user=customer, custom_id=cid("tk", "form", "billing"), fields={"details": answer})
    [ticket] = await tickets(tkf)
    assert ticket.status == "open"
    ch = tkf.guild.get_channel(ticket.channel_id)
    with_fields = [m for m in ch.messages if m.embeds and m.embeds[0].fields]
    assert len(with_fields) == 2
    assert with_fields[0].id == ticket.control_message_id and with_fields[1].view is None
    assert "".join(f.value for m in with_fields for f in m.embeds[0].fields) == escape_markdown(answer)
    for message in with_fields:
        assert sum(len(f.name) + len(f.value) for f in message.embeds[0].fields) <= 6000


async def test_forbidden_channel_creation_marks_failed_and_answers_generic_error(tk: TestEnv) -> None:
    tk.guild.fail_channel_create = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    [ticket] = await tickets(tk)
    assert ticket.status == "failed" and ticket.channel_id is None
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tk.text(core.ERROR_GENERIC)
    assert "tickets.create_failed" in await audit_actions(tk)
    assert channel(tk, "staff_alerts").messages == []
    # The failed attempt does not count toward the limit (and no cooldown was started).
    tk.guild.fail_channel_create = None
    await open_ticket(tk, tk.member(name="second"))


async def test_welcome_send_failure_removes_channel(tk: TestEnv) -> None:
    original = tk.guild.create_text_channel

    async def create_then_break(name, **kwargs):  # type: ignore[no-untyped-def]
        created = await original(name, **kwargs)
        created.fail_send = http_error(discord.Forbidden, 403, 50013, "Missing Permissions")
        return created

    tk.guild.create_text_channel = create_then_break  # type: ignore[method-assign]
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    [ticket] = await tickets(tk)
    assert ticket.status == "failed"
    created = [c for kind, c in tk.guild.audit if kind == "create_text_channel"]
    assert len(created) == 1 and created[0].deleted
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tk.text(core.ERROR_GENERIC)


async def test_missing_content_feature_not_ready_answers_unavailable(tk: TestEnv) -> None:
    values = dict(tk.content)
    del values[k.category_key("general", "welcome_title")]
    tk.app.content.replace(file_values=values)
    tk.app.recompute_readiness()
    assert not tk.app.ready("tickets")
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tk.text(core.ERROR_UNAVAILABLE)
    assert await tickets(tk) == []


async def test_content_removed_at_runtime_never_sends_incomplete_ticket(tk: TestEnv) -> None:
    values = dict(tk.content)
    del values[k.category_key("general", "welcome_title")]
    tk.app.content.replace(file_values=values)  # readiness not recomputed: simulates a race
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    [ticket] = await tickets(tk)
    assert ticket.status == "failed"
    assert [c for kind, c in tk.guild.audit if kind == "create_text_channel"] == []
    assert interaction.last_sent is not None
    assert interaction.last_sent.embeds[0].description == tk.text(core.ERROR_UNAVAILABLE)


async def test_no_content_at_all_is_silent(tk: TestEnv) -> None:
    tk.app.content.replace(file_values={})
    interaction = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "general"))
    assert interaction.sent == []
    assert interaction.response_kinds() == ["defer"]


async def test_unknown_category_and_foreign_guild(tk: TestEnv) -> None:
    unknown = await click(tk.app, user=tk.member(), custom_id=cid("tk", "open", "nope"))
    assert unknown.last_sent is not None
    assert unknown.last_sent.embeds[0].description == tk.text(core.ERROR_NOT_FOUND)
    assert await tickets(tk) == []


async def test_select_panel_is_reset_after_every_pick(tmp_path: Path) -> None:
    """A Discord select keeps showing the picked option; the panel's menu is re-rendered after each pick
    so the same category can be picked again."""
    tenv = await make_env(tmp_path, form_overrides({"tickets": {"panel_style": "select"}}))
    try:
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        panel = channel(tenv, "ticket_panel").messages[0]
        embeds = list(panel.embeds)
        customer = tenv.member()
        pick = cid("tk", "pick")

        # No form: the pick is acknowledged by re-rendering the menu; the answer follows privately.
        picked = await click(tenv.app, user=customer, custom_id=pick, values=["general"], message=panel)
        assert picked.response_kinds() == ["edit_message"]
        assert set(picked.response.calls[0][1]) == {"view"}  # only the components; the embed is untouched
        assert panel.embeds == embeds and panel.custom_ids() == [pick]
        assert not any(option.default for option in panel.components()[0].item.options)
        [ticket] = await tickets(tenv)
        reply = picked.last_sent
        assert reply is not None and reply.ephemeral  # type: ignore[attr-defined]
        assert reply.embeds[0].description == render(
            tenv, k.CREATED, channel_mention=f"<#{ticket.channel_id}>", number=number(tenv, 1)
        )

        # Form: the form opens, then the menu is reset.
        advance_past_cooldown(tenv)
        edits = len(panel.edits)
        form = await click(tenv.app, user=customer, custom_id=pick, values=["billing"], message=panel)
        assert form.response_kinds() == ["send_modal"] and modal_of(form) is not None
        assert len(panel.edits) == edits + 1 and set(panel.edits[-1]) == {"view"}

        # Refused pick (limit reached): the configured error is shown and the menu is reset as well.
        edits = len(panel.edits)
        refused = await click(tenv.app, user=customer, custom_id=pick, values=["general"], message=panel)
        assert refused.last_sent is not None
        assert refused.last_sent.embeds[0].description == render(tenv, k.LIMIT_REACHED, limit=1)
        assert len(panel.edits) == edits + 1 and panel.custom_ids() == [pick]
        assert len(await tickets(tenv)) == 1
    finally:
        await tenv.close()


async def test_select_pick_still_answers_when_the_panel_cannot_be_rendered(tmp_path: Path) -> None:
    tenv = await make_env(tmp_path, {"tickets": {"panel_style": "select"}})
    try:
        assert MODULE.publish is not None
        await MODULE.publish(tenv.app)
        panel = channel(tenv, "ticket_panel").messages[0]
        values = dict(tenv.content)
        del values[k.PANEL_DESCRIPTION]  # removed at runtime: the panel cannot be rebuilt right now
        tenv.app.content.replace(file_values=values)
        picked = await click(
            tenv.app, user=tenv.member(), custom_id=cid("tk", "pick"), values=["general"], message=panel
        )
        assert picked.response_kinds() == ["defer"] and panel.edits == []
        assert picked.last_sent is not None and picked.last_sent.ephemeral  # type: ignore[attr-defined]
        assert [t.status for t in await tickets(tenv)] == ["open"]
    finally:
        await tenv.close()
