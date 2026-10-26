"""Every UI state of the Report a Problem workflow, built by the runtime view functions from synthetic sample data
(``[sample member]``, ``[sample answer]``, fixed sample ids). States whose text is not configured yet are left out
(the content checklist reports those keys). States that depend on settings (the order menu, several issue types,
the issue forms) are built for the configured settings.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.config.settings import ProblemType
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as checkout_keys
from shopbot.features.checkout import payment_methods as checkout_payment_methods
from shopbot.features.checkout import views as checkout_views
from shopbot.features.problems import content_keys as k
from shopbot.features.problems import views
from shopbot.features.tickets import views as ticket_views
from shopbot.ui.spec import MessageSpec, ModalSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.ui.style import Ui

SAMPLE_USER_ID = 100_000_000_000_000_001
SAMPLE_STAFF_ID = 100_000_000_000_000_002
SAMPLE_CHANNEL_ID = 100_000_000_000_000_003
SAMPLE_ROLE_ID = 100_000_000_000_000_004
SAMPLE_REPORT_ID = 1
SAMPLE_TICKET_ID = 1
SAMPLE_TICKET_NUMBER = 1
SAMPLE_NONCE = "0123456789abcdef"
SAMPLE_USER_NAME = "[sample member]"
SAMPLE_NOTE = "[sample staff note]"
SAMPLE_REASON = "[sample staff reason]"
SAMPLE_ANSWER = "[sample answer]"
SAMPLE_ORDER_NUMBER = 1001
SAMPLE_TOTAL_MINOR = 12_345


def sample_text(unit: str, length: int) -> str:
    """Synthetic text exactly ``length`` characters long (the longest a form or the staff reason accepts)."""
    piece = f"{unit} "
    return (piece * (length // len(piece) + 1))[:length]


def _status_label(ui: Ui, status: str) -> str:
    text = ui.text(checkout_keys.stage_key(status))
    if text is None:
        raise MissingContent(checkout_keys.stage_key(status))
    return text


def sample_orders(app: App, count: int) -> list[views.OrderInfo]:
    """``count`` synthetic orders (newest first) in the first eligible status."""
    ui = app.ui
    statuses = app.settings.problems.eligible_statuses or ("paid",)
    total = ui.money(SAMPLE_TOTAL_MINOR)
    if total is None:
        raise MissingContent(core.MONEY_FORMAT)
    status = _status_label(ui, statuses[0])
    return [
        views.OrderInfo(order_id=index, number=str(SAMPLE_ORDER_NUMBER + index), total=total, status=status)
        for index in range(count, 0, -1)
    ]


def sample_answers(problem_type: ProblemType, *, longest: bool = False) -> dict[str, str]:
    return {
        field.id: sample_text(SAMPLE_ANSWER, field.max_length) if longest else f"{SAMPLE_ANSWER} {index}"
        for index, field in enumerate(problem_type.form, start=1)
    }


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    settings = app.settings
    p = settings.problems
    states: list[PreviewState] = []
    number = ticket_views.number_text(settings.tickets, SAMPLE_TICKET_NUMBER)

    def add(name: str, note: str, build: Callable[[], MessageSpec | ModalSpec]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        states.append(PreviewState(feature=k.FEATURE, name=name, note=note, spec=spec))

    first_type = p.types[0] if p.types else None

    add(
        "purchase_panel",
        "The #purchase panel with the Report a Problem button next to the Start button (shown once this feature is "
        "ready; /setup updates the panel).",
        lambda: checkout_views.panel(
            ui,
            methods_list=checkout_payment_methods.methods_list(ui, settings),
            report_button=views.report_button(ui),
        ),
    )

    # -- the private choice step --------------------------------------------------------------------------------
    def choice(*, chosen: bool) -> views.Choice:
        show_orders = k.orders_possible(settings)
        allow_none = p.allow_without_order and show_orders
        orders = sample_orders(app, views.MENU_OPTIONS - (1 if allow_none else 0)) if show_orders else []
        show_types = k.type_choice(settings)
        order_sel = str(orders[0].order_id) if orders else views.NO_ORDER
        type_sel = first_type.id if first_type is not None else views.NOT_CHOSEN
        return views.Choice(
            nonce=SAMPLE_NONCE,
            orders=tuple(orders),
            allow_none=allow_none,
            show_orders=show_orders,
            show_types=show_types,
            order_sel=order_sel if chosen or not show_orders else views.NOT_CHOSEN,
            type_sel=type_sel if chosen or not show_types else views.NOT_CHOSEN,
        )

    if k.choice_possible(settings):
        add(
            "choose",
            "Private step after the button: the customer's eligible orders (a full menu, newest first, plus 'not "
            "about an order') and/or the issue types; Continue is disabled until everything is chosen.",
            lambda: views.choose_message(ui, p, choice(chosen=False)),
        )
        add(
            "choose.complete",
            "The private step once an order and an issue type are chosen (Continue opens the form).",
            lambda: views.choose_message(ui, p, choice(chosen=True)),
        )
    for problem_type in p.types:
        if problem_type.form:
            add(
                f"form.{problem_type.id}",
                f"Form of the '{problem_type.id}' issue type (opens from Continue, or right away when there is "
                "nothing to choose).",
                partial(views.form_modal, ui, problem_type, nonce=SAMPLE_NONCE, order_sel=views.NO_ORDER),
            )
    add(
        "created",
        "Private confirmation after the report's ticket was created (replaces the private step).",
        lambda: views.created_reply(ui, channel_id=SAMPLE_CHANNEL_ID, number=number),
    )
    add(
        "limit_reached",
        "Private reply when the customer already has the maximum of open reports (links the newest).",
        lambda: views.limit_reply(ui, limit=p.max_open_per_user, channel_id=SAMPLE_CHANNEL_ID),
    )
    if not p.allow_without_order:
        add(
            "no_orders",
            "Private reply when the customer has no order a report may refer to.",
            lambda: views.no_orders_reply(ui),
        )

    # -- the report's ticket ---------------------------------------------------------------------------------
    def order() -> views.OrderInfo | None:
        return sample_orders(app, 1)[0] if k.orders_possible(settings) else None

    def panel(
        status: str, *, reason: str | None = None, ticket_open: bool = True, with_order: bool = True
    ) -> MessageSpec:
        data = views.PanelData(
            report_id=SAMPLE_REPORT_ID,
            ticket_id=SAMPLE_TICKET_ID,
            number=number,
            user_id=SAMPLE_USER_ID,
            user_name=SAMPLE_USER_NAME,
            type_id=first_type.id if first_type is not None else "",
            order=order() if with_order else None,
            status=status,
            reason=reason,
            decided_by=SAMPLE_STAFF_ID if status != k.OPEN else None,
            ticket_open=ticket_open,
        )
        return views.ticket_panel(ui, p, data, ping_role_ids=(SAMPLE_ROLE_ID,) if p.ping_staff else ())

    if first_type is not None:
        add(
            "ticket.open",
            "Report panel posted in the new private ticket (pings the customer): report summary, status, the staff "
            "buttons Resolve/Reject and the ticket's Close button.",
            lambda: panel(k.OPEN),
        )
        add(
            "ticket.open_without_order",
            "Report panel of a report that is not about an order.",
            lambda: panel(k.OPEN, with_order=False),
        )
        add(
            "ticket.resolved",
            "Report panel after staff resolved the report with a note (the ticket is still open).",
            lambda: panel(k.RESOLVED, reason=SAMPLE_NOTE),
        )
        add(
            "ticket.rejected",
            "Report panel after staff rejected the report with the longest reason, once the ticket was closed.",
            lambda: panel(k.REJECTED, reason=sample_text(SAMPLE_REASON, k.REASON_MAX), ticket_open=False),
        )
    for problem_type in p.types:
        if not problem_type.form:
            continue
        add(
            f"details.{problem_type.id}",
            f"The customer's answers to the '{problem_type.id}' form as a card posted below the report panel: the "
            "heading, then each question in bold with the answer under it.",
            partial(_first, views.details_messages, ui, problem_type, sample_answers(problem_type)),
        )
        for index, spec in enumerate(_longest(ui, problem_type), start=1):
            add(
                f"details.{problem_type.id}.longest.{index}",
                f"Answers to the '{problem_type.id}' form at their maximum lengths (message {index}; a long card "
                "continues in its next embed and further messages, nothing is cut, no banner).",
                partial(_same, spec),
            )

    # -- decisions, notifications, staff ------------------------------------------------------------------------
    def notification(status: str, reason: str) -> MessageSpec:
        return views.decision_notification(
            ui,
            p,
            status=status,
            number=number,
            type_id=first_type.id if first_type is not None else "",
            order=order(),
            reason=reason,
            channel_id=SAMPLE_CHANNEL_ID,
        )

    if first_type is not None:
        for status, reason in ((k.RESOLVED, SAMPLE_NOTE), (k.REJECTED, sample_text(SAMPLE_REASON, k.REASON_MAX))):
            add(
                f"notify.{status}",
                f"Private message to the customer when the report was {status} (direct message; posted in the "
                "ticket when the report panel cannot be updated).",
                partial(notification, status, reason),
            )
        add(
            "staff_alert",
            "Alert in the staff alert channel for a new report (tickets.staff_alert_channel).",
            lambda: views.staff_alert(
                ui,
                p,
                number=number,
                type_id=first_type.id,
                user_id=SAMPLE_USER_ID,
                channel_id=SAMPLE_CHANNEL_ID,
                order=order(),
                ping_role_ids=(SAMPLE_ROLE_ID,) if p.ping_staff else (),
            ),
        )
    add(
        "staff.resolve_form",
        "Staff only: form opened by Resolve (optional note to the customer).",
        lambda: views.resolve_modal(app.op, report_id=SAMPLE_REPORT_ID, number=number),
    )
    add(
        "staff.reject_form",
        "Staff only: form opened by Reject (reason, required when problems.require_reason_on_reject = true).",
        lambda: views.reject_modal(
            app.op, report_id=SAMPLE_REPORT_ID, number=number, required=p.require_reason_on_reject
        ),
    )
    add(
        "staff.decided",
        "Staff only: private reply after a decision.",
        lambda: views.operator_reply(
            ui,
            app.op("problems.op.decided_title", number=number, status=app.op("problems.op.status.resolved")),
            app.op("problems.op.decided", delivery=app.op("problems.op.delivery.dm")),
            tone="success",
        ),
    )
    add(
        "staff.already_decided",
        "Staff only: private reply when the report was already decided (double or concurrent decision).",
        lambda: views.operator_reply(
            ui,
            app.op("problems.op.already_decided_title", number=number, status=app.op("problems.op.status.rejected")),
            app.op("problems.op.already_decided"),
            tone="warning",
        ),
    )
    add(
        "staff.reason_required",
        "Staff only: private reply when Reject was submitted without the required reason.",
        lambda: views.operator_reply(
            ui,
            app.op("problems.op.reason_required_title"),
            app.op("problems.op.reason_required"),
            tone="warning",
            banner="error",
        ),
    )
    return states


def _first(build: Callable[..., list[MessageSpec]], *args: object) -> MessageSpec:
    messages = build(*args)
    if not messages:
        raise MissingContent(k.ATTACHMENTS_NOTE)  # nothing to show (no answers): not a state
    return messages[0]


def _same(spec: MessageSpec) -> MessageSpec:
    return spec


def _longest(ui: Ui, problem_type: ProblemType) -> list[MessageSpec]:
    try:
        return views.details_messages(ui, problem_type, sample_answers(problem_type, longest=True))
    except MissingContent:
        return []
