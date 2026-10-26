"""Gift card payments (``payments.provider = "gift_card"``): the customer's private form, the payment staff's reveal
and decisions, deleting codes kept too long, and the chat guard.

Codes are sealed (``payments.vault``) before they reach the database and opened only for the private reveal reply.
Nothing here logs or audits a code, a PIN or the key; audit entries name the order, the cards and amounts.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Final
from weakref import WeakKeyDictionary

import discord

from shopbot.core.money import MAX_AMOUNT_MINOR, parse_amount
from shopbot.features.checkout import gift_card_repo, gift_card_store, service, staff_views, views
from shopbot.features.checkout import models as m
from shopbot.features.checkout.order_money import currency_digits, order_money
from shopbot.features.checkout.payments import gift_card
from shopbot.features.checkout.payments.vault import CardSecret, Vault, VaultError, load_vault, normalize_code
from shopbot.features.checkout.staff import OpReply
from shopbot.features.tickets import service as tickets_service
from shopbot.features.tickets.models import KIND_ORDER, OPEN
from shopbot.logging_setup import log_event
from shopbot.ui.routing import HandlerError
from shopbot.ui.spec import MessageSpec, ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App
    from shopbot.db.repos.tasks import Task

log = logging.getLogger(__name__)

TASK_ERASE: Final = gift_card_repo.TASK_ERASE
_DAY: Final = 86_400
_VAULTS: WeakKeyDictionary[App, Vault] = WeakKeyDictionary()


def vault(app: App) -> Vault:
    """The vault for this process (``GIFT_CARD_KEY``, else the key file next to the database). Raises VaultError."""
    found = _VAULTS.get(app)
    if found is None:
        found = load_vault(app.env.gift_card_key, app.env.db_path)
        _VAULTS[app] = found
    return found


def check_key(app: App) -> bool:
    """At startup: load the key now (creating the key file on first use) so a problem shows in the log right away,
    not when the first customer pays. Returns whether codes can be stored."""
    if not gift_card.enabled(app.settings):
        return True
    try:
        vault(app)
    except (VaultError, OSError) as exc:
        log_event(
            log,
            logging.ERROR,
            "checkout_gift_card_key_unavailable",
            error=type(exc).__name__,
            hint="set GIFT_CARD_KEY or make the database folder writable; customers cannot submit gift cards",
        )
        return False
    return True


def awaits_decision(order: m.Order) -> bool:
    """A placed gift card order: it is paid (or waits for payment again) through the decision on its cards."""
    return order.payment_provider == gift_card.PROVIDER_ID and order.status == m.PAYMENT_SUBMITTED


def can_handle(app: App, member: discord.Member | None, user_id: int) -> bool:
    """Reveal codes and decide on cards: owners, admins and ``payments.gift_card.staff_roles``."""
    return app.perms.has_role(member, user_id, app.settings.payments.gift_card.staff_roles)


def _remaining(order: m.Order, cards: list[gift_card_store.GiftCard]) -> int:
    counted = sum(card.amount_minor for card in cards if card.status in gift_card_store.ACTIVE)
    return max(0, order.total_minor - counted)


# -- customer ------------------------------------------------------------------------------------------------------


async def form(app: App, order: m.Order) -> ModalSpec:
    """The private form of an order that waits for payment (refused with ERROR_STATE otherwise)."""
    if order.status != m.PENDING or order.payment_provider != gift_card.PROVIDER_ID:
        raise HandlerError(gift_card.ERROR_STATE)
    cards = await app.db.run(gift_card_store.cards, order.id)
    return views.gift_card_modal(app.ui, app.settings, order, remaining_minor=_remaining(order, cards))


async def submit(app: App, order: m.Order, *, code: str, pin: str | None, amount_text: str) -> MessageSpec:
    """Store the customer's card (sealed) and, once the cards cover the total, place the order. Returns the private
    reply; customer mistakes raise HandlerError with the configured wording."""
    settings = app.settings
    if order.payment_provider != gift_card.PROVIDER_ID:
        raise HandlerError(gift_card.ERROR_STATE)
    code = code.strip()
    pin = (pin or "").strip() or None
    if not gift_card.valid_code(code) or (pin is not None and len(pin) > gift_card_store.PIN_MAX):
        raise HandlerError(gift_card.ERROR_CODE, min=gift_card_store.CODE_MIN, max=gift_card_store.CODE_MAX)
    c = settings.checkout
    try:
        amount = parse_amount(
            amount_text.strip(), currency_digits(settings, order.currency), c.decimal_separator, c.thousands_separator
        )
    except ValueError:
        amount = 0
    fmt = order_money(app.ui, settings, order.currency)
    if not 0 < amount <= MAX_AMOUNT_MINOR:
        raise HandlerError(gift_card.ERROR_AMOUNT, example=fmt(order.total_minor))
    try:
        sealer = vault(app)
    except (VaultError, OSError) as exc:
        log_event(log, logging.ERROR, "checkout_gift_card_key_unavailable", error=type(exc).__name__)
        raise HandlerError(gift_card.ERROR_UNAVAILABLE) from None
    now = app.clock.now()
    try:
        result = await app.db.transaction(
            gift_card_repo.submit_card,
            guild_id=app.env.guild_id,
            order_id=order.id,
            user_id=order.user_id,
            amount_minor=amount,
            fingerprint=sealer.fingerprint(code),
            sealed=sealer.seal(CardSecret(code=code, pin=pin)),
            last4=gift_card.last_chars(code),
            max_cards=settings.payments.gift_card.max_cards,
            erase_at=now + settings.payments.gift_card.keep_days * _DAY,
            now=now,
            task_attempts=app.scheduler.max_attempts,
        )
    except gift_card_repo.GiftCardError as exc:
        if exc.reason == "duplicate":
            raise HandlerError(gift_card.ERROR_DUPLICATE) from None
        if exc.reason == "too_many":
            raise HandlerError(gift_card.ERROR_TOO_MANY, max=settings.payments.gift_card.max_cards) from None
        raise HandlerError(gift_card.ERROR_STATE) from None
    await app.audit.record(
        "checkout.gift_card_submitted",
        actor_id=order.user_id,
        target_type="order",
        target_id=order.id,
        details={"number": order.number, "card": result.card_id, "amount_minor": amount},
    )
    if result.moved is not None:
        await service.after_transition(app, result.moved, actor_id=order.user_id)
    else:
        await service.refresh_customer_message(app, order.id)
        await service.refresh_staff_message(app, order.id)
    received = fmt(result.totals.covered_minor)
    if result.moved is not None:
        text = app.ui.text(gift_card.REPLY_COMPLETE, amount=fmt(amount), received=received, order_number=order.number)
        return _reply(app, text, "success", "submission-and-processing")
    remaining = fmt(max(0, order.total_minor - result.totals.covered_minor))
    text = app.ui.text(
        gift_card.REPLY_PARTIAL,
        amount=fmt(amount),
        received=received,
        remaining=remaining,
        total=fmt(order.total_minor),
    )
    return _reply(app, text, "info", "pending-review")


def _reply(app: App, text: str | None, tone: str, banner: Any) -> MessageSpec:
    if not text:
        raise HandlerError()
    return MessageSpec(embeds=(app.ui.embed(description=text, tone=tone),), banner=app.ui.banner(banner))


# -- staff (payment roles) -----------------------------------------------------------------------------------------


async def reveal(app: App, order: m.Order, *, actor_id: int) -> MessageSpec | OpReply:
    """The waiting cards' codes in a private reply (each reveal is counted on the card and audited, without codes)."""
    try:
        sealer = vault(app)
    except (VaultError, OSError) as exc:
        log_event(log, logging.ERROR, "checkout_gift_card_key_unavailable", error=type(exc).__name__)
        return OpReply(app.op("checkout.gift.key_missing"), "danger")
    try:
        current, cards, sealed = await app.db.transaction(
            gift_card_repo.reveal, guild_id=app.env.guild_id, order_id=order.id, actor_id=actor_id, now=app.clock.now()
        )
    except gift_card_repo.GiftCardError as exc:
        return _refused(app, order, exc)
    secrets: dict[int, CardSecret | None] = {}
    for card, token in sealed:
        try:
            secrets[card.id] = sealer.open(token)
        except VaultError:
            secrets[card.id] = None
    waiting = [
        staff_views.RevealedCard(index=index, card=card, secret=secrets.get(card.id))
        for index, card in enumerate(cards, start=1)
        if card.status == gift_card_store.SUBMITTED
    ]
    await app.audit.record(
        "checkout.gift_card_revealed",
        actor_id=actor_id,
        target_type="order",
        target_id=order.id,
        details={"number": order.number, "cards": [item.card.id for item in waiting]},
    )
    await service.refresh_staff_message(app, order.id)
    return staff_views.gift_card_reveal(
        app.op, app.ui, app.settings, current, waiting, keep_days=app.settings.payments.gift_card.keep_days
    )


async def decide(
    app: App, order: m.Order, *, actor_id: int, redeemed: bool, reason: str | None, card_id: int | None = None
) -> OpReply:
    """Staff redeemed the waiting cards (or card ``card_id``) or refused them with ``reason`` for the customer."""
    c = app.settings.checkout
    now = app.clock.now()
    try:
        result = await app.db.transaction(
            gift_card_repo.decide_cards,
            guild_id=app.env.guild_id,
            order_id=order.id,
            card_ids=None if card_id is None else (card_id,),
            redeemed=redeemed,
            reason=(reason or "").strip()[: gift_card_store.REASON_MAX] or None,
            actor_id=actor_id,
            expiry_at=now + c.payment_expiry_minutes * 60 if c.payment_expiry_minutes > 0 else None,
            now=now,
            task_attempts=app.scheduler.max_attempts,
        )
    except gift_card_repo.GiftCardError as exc:
        return _refused(app, order, exc)
    await app.audit.record(
        "checkout.gift_card_redeemed" if redeemed else "checkout.gift_card_refused",
        actor_id=actor_id,
        target_type="order",
        target_id=order.id,
        details={"number": order.number, "cards": result.changed},
    )
    if result.moved is not None:
        await service.after_transition(app, result.moved, actor_id=actor_id)
    else:
        await service.refresh_staff_message(app, order.id)
    key = "checkout.gift.redeemed" if redeemed else "checkout.gift.refused"
    lines = [app.op(key, number=order.number, count=result.changed), _outcome(app, result)]
    return OpReply(" ".join(line for line in lines if line), "success")


def _outcome(app: App, result: gift_card_repo.Decided) -> str:
    order = result.order
    if order.status == m.PAID:
        unused = result.totals.redeemed_minor - order.total_minor
        paid = app.op("checkout.gift.outcome.paid")
        if unused > 0:
            amount = order_money(app.ui, app.settings, order.currency)(unused)
            credit = app.op("checkout.gift.outcome.credit", amount=amount)
            return f"{paid} {credit}"
        return paid
    if order.status == m.PENDING:
        return app.op("checkout.gift.outcome.pending")
    return app.op("checkout.gift.outcome.waiting")


def _refused(app: App, order: m.Order, exc: gift_card_repo.GiftCardError) -> OpReply:
    current = exc.order or order
    if exc.reason == "decided":
        return OpReply(app.op("checkout.gift.decided"), "warning")
    return OpReply(app.op("checkout.gift.not_waiting", number=order.number, status=current.status), "warning")


# -- codes kept too long ---------------------------------------------------------------------------------------------


async def erase_task(app: App, task: Task) -> None:
    """``checkout.gift_card_erase``: delete a code staff did not decide on within ``keep_days``."""
    card_id = int(task.payload.get("card_id", 0))
    erased = await app.db.transaction(gift_card_repo.erase_card, card_id, app.clock.now())
    if not erased:
        return
    card = await app.db.run(gift_card_store.get_card, card_id)
    log_event(log, logging.INFO, "checkout_gift_card_erased", card_id=card_id)
    if card is not None:
        await app.audit.record(
            "checkout.gift_card_erased", actor_id=None, target_type="order", target_id=card.order_id, details={}
        )
        await service.refresh_staff_message(app, card.order_id)


# -- chat guard --------------------------------------------------------------------------------------------------

# Links, mentions, channel references, custom emojis and timestamps carry long numbers that are not codes.
_IGNORED = re.compile(r"https?://\S+|<a?:\w+:\d+>|<[@#][!&]?\d+>|<t:\d+(?::\w)?>")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_UPS_TRACKING = re.compile(r"1Z[0-9A-Z]{16}")
_PUNCTUATION = ".,!?;:()[]{}\"'"
_MIN_CHARS, _MAX_CHARS, _MIN_DIGITS, _MIN_LETTERS = 12, 32, 4, 4
# Card numbers have 13-19 digits (and a Luhn check digit); other digit runs are tracking or ID numbers.
_CARD_NUMBER_DIGITS = range(13, 20)


def looks_like_code(text: str) -> bool:
    """Whether a chat message seems to contain a gift card code or a card number: 12-32 letters and digits with at
    least 4 of each, or a card number (13-19 digits that pass the Luhn check), typed as one run, in dash-separated
    blocks, or in space-separated blocks as printed on a card (capitals and digits of one length, two or more of
    them with digits). Links, mentions, dates, phone, tracking and ID numbers and ordinary sentences with an order or
    card number do not match."""
    text = _IGNORED.sub(" ", text)
    for token in _TOKEN.findall(text):
        blocks = token.split("-")
        if len(blocks) > 1 and token.replace("-", "").isdigit() and any(len(block) < 3 for block in blocks):
            continue  # a date or a phone number
        if _code_like(normalize_code(token)):
            return True
    run: list[str] = []
    for word in [w.strip(_PUNCTUATION) for w in text.split()] + [""]:
        grouped = 3 <= len(word) <= 8 and word.isalnum() and (word.isupper() or word.isdigit())
        if grouped and (not run or len(word) == len(run[0])):
            run.append(word)
            continue
        with_digits = sum(any(ch.isdigit() for ch in block) for block in run)
        if len(run) >= 2 and with_digits >= 2 and _code_like(normalize_code("".join(run))):
            return True
        run = [word] if grouped else []
    return False


def _code_like(chars: str) -> bool:
    """``chars``: letters and digits only, upper case."""
    if not _MIN_CHARS <= len(chars) <= _MAX_CHARS or _UPS_TRACKING.fullmatch(chars):
        return False
    digits = sum(ch.isdigit() for ch in chars)
    if digits == len(chars):
        return len(chars) in _CARD_NUMBER_DIGITS and _luhn(chars)
    return digits >= _MIN_DIGITS and len(chars) - digits >= _MIN_LETTERS


def _luhn(digits: str) -> bool:
    """The check digit card numbers carry (most IDs and tracking numbers fail it)."""
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (2 if position % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


async def on_message_edit(app: App, before: discord.Message, after: discord.Message) -> None:
    """A message edited to contain a code is removed like a new one."""
    if before.content == after.content and before is not after:
        return
    await on_message(app, after)


async def on_message(app: App, message: discord.Message) -> None:
    """Delete a customer's message in their open order ticket when it looks like a gift card code, and tell them
    privately to use the form (``payments.gift_card.chat_guard``). Staff messages are never touched."""
    if not gift_card.enabled(app.settings) or not app.settings.payments.gift_card.chat_guard:
        return
    author = message.author
    if getattr(author, "bot", False) or not looks_like_code(message.content or ""):
        return
    channel_id = getattr(message.channel, "id", None)
    if channel_id is None:
        return
    ticket = await tickets_service.get_ticket_by_channel(app, int(channel_id))
    if ticket is None or ticket.kind != KIND_ORDER or ticket.status != OPEN or ticket.user_id != author.id:
        return
    try:
        await message.delete()
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        log_event(
            log, logging.WARNING, "checkout_gift_card_guard_failed", channel_id=channel_id, error=type(exc).__name__
        )
        return
    await app.audit.record(
        "checkout.gift_card_message_removed", actor_id=None, target_type="ticket", target_id=ticket.id, details={}
    )
    text = app.ui.text(gift_card.CHAT_GUARD, channel_mention=views.channel_mention(int(channel_id)))
    if not text:
        return
    await app.notifier.notify_user(
        user_id=author.id,
        spec=MessageSpec(
            embeds=(app.ui.embed(description=text, tone="warning"),), banner=app.ui.banner("general-response")
        ),
        kind="checkout.gift_card_chat_guard",
        dedupe_key=f"checkout.gift_card_chat_guard:{message.id}",
        fallback_channel_id=int(channel_id),
    )
