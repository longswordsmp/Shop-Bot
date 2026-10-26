"""Preview states of gift card payments (only while ``payments.provider = "gift_card"``): the order view with cards
received, the private form and replies, the refused-card notification, the chat guard notice, and the staff side
(the record with the payment field, the reveal reply with sample codes, the card problem form). Built by the runtime
view functions from synthetic sample data. Called by ``preview.preview``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from shopbot.config.banner_ids import BannerId
from shopbot.features.checkout import gift_card_store as store
from shopbot.features.checkout import models as m
from shopbot.features.checkout import staff_preview, staff_views, views
from shopbot.features.checkout.payments import gift_card
from shopbot.features.checkout.payments.vault import CardSecret
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.features.checkout.payments import PaymentInstructions

SAMPLE_CODE = "[SAMPLE-CODE-0000-0000]"
SAMPLE_PIN = "[0000]"
SAMPLE_REASON = "[sample reason]"
SAMPLE_STAFF_ID = staff_preview.SAMPLE_STAFF_ID

Add = Callable[[str, str, Callable[[], MessageSpec | ModalSpec | None]], None]


def _card(index: int, order: m.Order, amount: int, status: str, *, stored: bool = True) -> store.GiftCard:
    decided = status != store.SUBMITTED
    return store.GiftCard(
        id=index,
        guild_id=order.guild_id,
        order_id=order.id,
        user_id=order.user_id,
        amount_minor=amount,
        last4=f"{index:04d}",
        status=status,
        reason=SAMPLE_REASON if status == store.REFUSED else None,
        submitted_at=order.created_at,
        reveal_count=1 if status != store.SUBMITTED else 0,
        revealed_by=SAMPLE_STAFF_ID if status != store.SUBMITTED else None,
        revealed_at=order.created_at if status != store.SUBMITTED else None,
        decided_by=SAMPLE_STAFF_ID if decided else None,
        decided_at=order.created_at if decided else None,
        erased_at=order.created_at if decided or not stored else None,
        stored=stored and not decided,
    )


def add_states(
    app: App,
    add: Add,
    pending: m.OrderView,
    placed: m.OrderView,
    instructions: PaymentInstructions | None,
) -> None:
    """``pending``: a sample order waiting for payment; ``placed``: the same order with its cards submitted."""
    ui, settings, op = app.ui, app.settings, app.op
    if not gift_card.enabled(settings):
        return
    order = pending.order
    half = order.total_minor // 2 or 1
    partial_cards = [_card(1, order, half, store.SUBMITTED), _card(2, order, half, store.REFUSED)]
    fields = gift_card.cards_fields(ui, settings, order, partial_cards)
    if instructions is not None:
        with_cards = replace(instructions, fields=fields)
        add(
            "order_gift_cards_received",
            "Workflow message of an order paid in part: the cards received so far and what is still to pay.",
            lambda: views.order_message(ui, settings, pending, instructions=with_cards),
        )
    add(
        "payment_gift_card_form",
        "Private gift card form (Submit gift card): code, optional PIN, the card's amount (prefilled).",
        lambda: views.gift_card_modal(ui, settings, order, remaining_minor=order.total_minor - half),
    )
    fmt = views.order_money(ui, settings, order.currency)

    def reply(key: str, banner: BannerId, **values: str | int) -> MessageSpec:
        text = ui.text(key, **values)
        return MessageSpec(embeds=(ui.embed(description=text, tone="info"),), banner=ui.banner(banner))

    add(
        "reply_gift_card_partial",
        "Private reply after a card that does not cover the total yet.",
        lambda: reply(
            gift_card.REPLY_PARTIAL,
            "pending-review",
            amount=fmt(half),
            received=fmt(half),
            remaining=fmt(order.total_minor - half),
            total=fmt(order.total_minor),
        ),
    )
    add(
        "reply_gift_card_complete",
        "Private reply after the card that completes the payment (the order is placed).",
        lambda: reply(
            gift_card.REPLY_COMPLETE,
            "submission-and-processing",
            amount=fmt(order.total_minor),
            received=fmt(order.total_minor),
            order_number=order.number,
        ),
    )
    refused = [_card(1, order, order.total_minor, store.REFUSED)]
    add(
        "notify_card_problem",
        "Customer notification: our team could not accept a gift card (the order waits for payment again).",
        lambda: views.payment_refused_notification(
            ui,
            settings,
            pending,
            remaining=fmt(order.total_minor),
            fields=gift_card.cards_fields(ui, settings, order, refused),
        ),
    )
    add(
        "notify_gift_card_chat_guard",
        "Sent to a customer whose ticket message was removed because it looked like a gift card code.",
        lambda: MessageSpec(
            embeds=(
                ui.embed(
                    description=ui.text(gift_card.CHAT_GUARD, channel_mention=views.channel_mention(placed.channel_id)),
                    tone="warning",
                ),
            ),
            banner=ui.banner("error"),
        ),
    )
    # -- staff ---------------------------------------------------------------------------------------------------
    cards = (
        _card(1, placed.order, half, store.SUBMITTED),
        _card(2, placed.order, placed.order.total_minor - half, store.SUBMITTED, stored=False),
    )
    revealed = [
        staff_views.RevealedCard(index=1, card=cards[0], secret=CardSecret(SAMPLE_CODE, SAMPLE_PIN)),
        staff_views.RevealedCard(index=2, card=cards[1], secret=None),
    ]
    keep_days = settings.payments.gift_card.keep_days
    data = replace(staff_preview.sample_data(placed.order, claimed=True), gift_cards=cards)
    add(
        "staff_payment_submitted_gift_cards",
        "Live order record of an order paid with gift cards: the Payment field lists the cards (never the codes).",
        lambda: staff_views.staff_message(
            ui, settings, placed, data, staff_confirms_payment=True, credit_refunds=False, tracking=False
        ),
    )
    add(
        "staff_gift_card_reveal",
        "Staff only (payment roles): the private reveal reply with the waiting cards' codes (spoiler-hidden) and a "
        "redeemed / problem button per card.",
        lambda: staff_views.gift_card_reveal(op, ui, settings, placed.order, revealed, keep_days=keep_days),
    )
    add(
        "staff_gift_card_problem_form",
        "Staff only (payment roles): Card problem form (the reason is sent to the customer).",
        lambda: staff_views.gift_card_problem_modal(op, placed.order),
    )
