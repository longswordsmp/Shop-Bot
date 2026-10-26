"""Discount codes: applying one on the review step, claiming a coupon, and ``/discount`` for catalog managers.

The rules (amounts, when a code applies, how uses are counted) are in ``discounts``; the data in ``discount_repo``.
Every change is one database transaction that checks again what it relies on: a code is applied only when it fits the
cart at that moment (``repo.apply_discount``), and the submission checks it once more in the transaction that
inserts the order (``repo.submit_order``).

Customers (review step, ``checkout.discount_codes`` on): Add discount code opens a private form; a code that cannot
be used gets a private reply with the specific reason (``content_keys.discount_error_key``). Guessing codes is
limited: after ``service.DISCOUNT_TRIES`` typed codes that could not be used within
``service.DISCOUNT_TRIES_SECONDS``, the form answers "too many tries" until the oldest one is that old (in memory, per
customer; a restart forgets it). Use coupon applies a coupon the customer claimed the same way (those clicks are not
counted); Remove discount takes the code off. One code per order.

Public API for other features (the ``/announce`` preset builds on it)
--------------------------------------------------------------------
- ``discount_views.claim_button(ui, label_key, code_id, style=...) -> ButtonSpec``: the claim button of a post, its
  label from the content key passed in, its restart-safe custom id ``sb:dc:claim:<code_id>`` (``claim_id``). Pressing
  it records the claim (once per member) and answers privately with the coupon's terms and how to use it; an offer
  that ended (the code ended, expired, was used up or no longer exists, or discount codes are off) answers that it
  has ended.
- ``usable_code(app, text) -> discounts.Code | None``: the live code with this name when it can be used now.
- ``terms_text(app, code) -> str``: the code's terms in plain words for staff (operator text), e.g. "buy one, get one
  free (the cheaper item of every pair is free) · every item · once per customer · until <date>".
- ``core.events.DISCOUNT_CODE_ENDED`` (payload: ``code_id``, ``code``, ``actor_id``): published after ``/discount end``
  committed, so a feature that posted the code (an announcement with a claim button) can mark its post as ended.
- ``core.events.DISCOUNT_OFFER_ENDED`` (payload: ``code_id``): published when a claim button is pressed after its code
  ended or expired (on every such press), so the post can show that its offer has ended at that moment too.
"""

from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING

from shopbot.core.events import DISCOUNT_CODE_ENDED, DISCOUNT_OFFER_ENDED
from shopbot.core.money import MoneyError
from shopbot.features import core_keys as core
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discount_repo, discount_views, repo, service, staff, views
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import models as m
from shopbot.features.server_keys import channel_mentions
from shopbot.logging_setup import log_event
from shopbot.ui.routing import HandlerError
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

log = logging.getLogger(__name__)

AUDIT_CREATED = "checkout.discount_created"
AUDIT_ENDED = "checkout.discount_ended"
DAY = 86_400
MAX_DAYS = 365
_PRODUCT_LIST = re.compile(r"^\s*\d{1,16}(\s*,\s*\d{1,16})*\s*,?\s*$")


def enabled(app: App) -> bool:
    return app.settings.checkout.discount_codes


def _money(app: App) -> discount_views.Money:
    return lambda minor: views.money(app.ui, minor)


async def _product_names(app: App, code: d.Code) -> list[str]:
    """The names of the products the code is limited to, by product number (archived ones too)."""
    names = await app.db.run(discount_repo.product_names, app.env.guild_id, code.product_ids)
    return [names[p] for p in sorted(names)]


# -- customers: the review step ----------------------------------------------------------------------------------


def tries_wait(app: App, user_id: int) -> int:
    """Seconds the customer waits before a typed code is checked again (0: they may try now)."""
    return math.ceil(service.runtime(app).discount_tries.wait(user_id))


async def _apply(app: App, session: m.Session, **target: object) -> m.Session:
    try:
        return await app.db.transaction(
            repo.apply_discount,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            now=app.clock.now(),
            expiry_seconds=app.settings.checkout.session_expiry_minutes * 60,
            **target,  # type: ignore[arg-type]
        )
    except repo.CodeRefused as exc:
        log_event(log, logging.INFO, "checkout_discount_refused", session_id=session.id, reason=exc.check.reason)
        raise
    except repo.WrongStep:
        raise HandlerError(core.ERROR_INVALID_INPUT) from None  # a control of an earlier review


async def apply_typed(app: App, session: m.Session, text: str) -> m.Session:
    """The discount code form: apply the code the customer typed, or raise ``HandlerError`` with the reason it cannot
    be used (each refusal counts toward the limit on guessing codes; while it is reached, nothing is checked)."""
    if not enabled(app):
        raise HandlerError(core.ERROR_UNAVAILABLE)
    if tries_wait(app, session.user_id) > 0:
        raise HandlerError(k.DISCOUNT_TOO_MANY_TRIES)
    try:
        return await _apply(app, session, code=text)
    except repo.CodeRefused as exc:
        service.runtime(app).discount_tries.hit(session.user_id)
        raise service.customer_error(app, exc) from None
    except repo.CheckoutError as exc:
        raise service.customer_error(app, exc) from None


async def apply_coupon(app: App, session: m.Session, code_id: int) -> m.Session:
    """Use coupon: apply a coupon the customer claimed (``HandlerError`` with the reason when it does not apply)."""
    if not enabled(app):
        raise HandlerError(core.ERROR_UNAVAILABLE)
    try:
        return await _apply(app, session, code_id=code_id)
    except repo.CheckoutError as exc:
        raise service.customer_error(app, exc) from None


async def remove(app: App, session: m.Session) -> m.Session:
    """Remove discount: the order no longer uses a code."""
    try:
        return await app.db.transaction(
            repo.update_session,
            guild_id=app.env.guild_id,
            session_id=session.id,
            user_id=session.user_id,
            now=app.clock.now(),
            expiry_seconds=app.settings.checkout.session_expiry_minutes * 60,
            discount_code_id=None,
        )
    except repo.CheckoutError as exc:
        raise service.customer_error(app, exc) from None


# -- customers: claiming a coupon ---------------------------------------------------------------------------------


async def claim(app: App, user_id: int, code_id: int) -> MessageSpec:
    """A claim button (``sb:dc:claim:<code_id>``): record the member's claim (once) and return the private reply with
    the coupon's terms; the offer-ended reply when discount codes are off or the code ended, expired or was used up,
    or no longer exists (``/reset`` deleted the shop's codes and kept the announcement); the already-used text when it
    is once per customer and they placed an order with it."""
    ended = discount_views.offer_ended_reply(app.ui)  # built first: nothing is recorded if its text is missing
    if not enabled(app):
        return ended
    now = app.clock.now()
    guild_id = app.env.guild_id
    code = await app.db.run(discount_repo.get_code, guild_id, code_id)
    if code is None:
        return ended
    if not code.usable(now):
        # Final (ended or expired): the post that offers the coupon can show that its offer has ended too.
        await app.events.publish(DISCOUNT_OFFER_ENDED, code_id=code.id)
        return ended
    usage = await app.db.run(discount_repo.usage, code.id, user_id)
    if code.max_uses is not None and usage.uses >= code.max_uses:
        return ended
    if code.once_per_customer and usage.customer_uses > 0:
        raise HandlerError(k.discount_error_key(d.ALREADY_USED), code=views.escape(code.code))
    reply = discount_views.claim_reply(
        app.ui, app.settings, code, await _product_names(app, code), links=channel_mentions(app)
    )
    created = await app.db.transaction(
        discount_repo.claim, guild_id=guild_id, code_id=code.id, user_id=user_id, now=now
    )
    if created:
        log_event(log, logging.INFO, "checkout_discount_claimed", code_id=code.id, user_id=user_id)
    return reply


# -- other features -----------------------------------------------------------------------------------------------


async def usable_code(app: App, text: str) -> d.Code | None:
    """The live code with this name (normalized) when it can be used now: not ended, not expired."""
    return await app.db.run(discount_repo.usable_code, app.env.guild_id, text, app.clock.now())


async def find_code(app: App, text: str) -> d.Code | None:
    """The code with this name (normalized): the live one (also when expired), else the one that ended last."""
    return await app.db.run(discount_repo.find_code, app.env.guild_id, d.normalize(text))


async def terms_text(app: App, code: d.Code) -> str:
    """The code's terms in plain words for staff (operator text; products by name)."""
    return discount_views.terms_text(app.op, _money(app), code, await _product_names(app, code), now=app.clock.now())


# -- staff: /discount ----------------------------------------------------------------------------------------------


class Refused(Exception):
    """A /discount request that cannot be done: ``text`` is the operator reply (nothing was changed)."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def _amount(app: App, text: str, key: str) -> int:
    currency = app.settings.checkout.currency
    try:
        return staff.parse_staff_amount(app, text, currency)
    except MoneyError as exc:
        raise Refused(app.op(key, reason=str(exc), example=staff.example_amount(app, currency))) from None


def parse_value(app: App, kind: str, value: str | None) -> int:
    """The /discount create value as stored: a percent from 1 to 100 in basis points, an amount in the shop currency
    in minor units, nothing for buy one get one free. Raises ``Refused``."""
    text = (value or "").strip()
    if kind == d.BOGO:
        if text:
            raise Refused(app.op("checkout.discounts.bogo_value"))
        return 0
    if not text:
        example = staff.example_amount(app, app.settings.checkout.currency)
        raise Refused(app.op("checkout.discounts.value_missing", example=example))
    if kind == d.PERCENT:
        digits = text.removesuffix("%").strip()
        basis_points = int(digits) * 100 if digits.isdigit() else 0
        if not d.PERCENT_MIN_BP <= basis_points <= d.PERCENT_MAX_BP:
            raise Refused(app.op("checkout.discounts.percent_invalid"))
        return basis_points
    minor = _amount(app, text, "checkout.discounts.amount_invalid")
    if minor <= 0:
        raise Refused(app.op("checkout.discounts.amount_zero"))
    return minor


def parse_products(app: App, text: str | None) -> list[int]:
    """Product numbers separated by commas ("12, 15"); empty = every item. Raises ``Refused``."""
    if not text or not text.strip():
        return []
    if not _PRODUCT_LIST.match(text):
        raise Refused(app.op("checkout.discounts.products_invalid"))
    return sorted({int(part) for part in text.split(",") if part.strip()})


async def create(
    app: App,
    *,
    actor_id: int,
    code: str,
    kind: str,
    value: str | None,
    products: str | None,
    min_subtotal: str | None,
    max_uses: int | None,
    expires_in_days: int | None,
    once_per_customer: bool,
) -> MessageSpec:
    """``/discount create``: validate everything, create the code, audit it and reply with its terms (operator
    text). Raises ``Refused``."""
    if not enabled(app):
        raise Refused(app.op("checkout.discounts.off"))
    name = d.normalize(code)
    if not d.valid_format(name):
        raise Refused(app.op("checkout.discounts.code_invalid"))
    if kind not in d.KINDS:
        raise ValueError(f"unknown discount kind {kind!r}")  # the command offers only the three choices
    stored = parse_value(app, kind, value)
    product_ids = parse_products(app, products)
    minimum = _amount(app, min_subtotal, "checkout.discounts.minimum_invalid") if min_subtotal else 0
    if expires_in_days is not None and not 1 <= expires_in_days <= MAX_DAYS:
        raise Refused(app.op("checkout.discounts.days_invalid", max=MAX_DAYS))
    now = app.clock.now()
    try:
        created = await app.db.transaction(
            discount_repo.create_code,
            guild_id=app.env.guild_id,
            code=name,
            kind=kind,
            value=stored,
            min_subtotal_minor=minimum,
            max_uses=max_uses,
            once_per_customer=once_per_customer,
            expires_at=now + expires_in_days * DAY if expires_in_days else None,
            product_ids=product_ids,
            actor_id=actor_id,
            now=now,
        )
    except discount_repo.CodeTaken:
        raise Refused(app.op("checkout.discounts.code_taken", code=name)) from None
    except discount_repo.UnknownProducts as exc:
        ids = ", ".join(str(p) for p in exc.product_ids)
        raise Refused(app.op("checkout.discounts.products_unknown", ids=ids)) from None
    await app.audit.record(
        AUDIT_CREATED,
        actor_id=actor_id,
        target_type="discount_code",
        target_id=created.id,
        details={
            "code": created.code,
            "kind": created.kind,
            "value": created.value,
            "products": product_ids,
            "min_subtotal_minor": minimum,
            "max_uses": max_uses,
            "once_per_customer": once_per_customer,
            "expires_at": created.expires_at,
        },
    )
    return discount_views.created_reply(
        app.ui, app.op, _money(app), created, await _product_names(app, created), now=now
    )


async def list_specs(app: App) -> list[MessageSpec]:
    """``/discount list``: the codes that can be used now, then the latest ended ones (operator text)."""
    if not enabled(app):
        raise Refused(app.op("checkout.discounts.off"))
    now = app.clock.now()
    usable, ended = await app.db.run(discount_repo.list_codes, app.env.guild_id, now)
    return discount_views.list_messages(app.ui, app.op, _money(app), usable, ended, now=now)


async def end(app: App, *, actor_id: int, code: str) -> staff.OpReply:
    """``/discount end``: end the live code with this name (orders placed with it keep their discount), audit it and
    publish ``DISCOUNT_CODE_ENDED``."""
    if not enabled(app):
        raise Refused(app.op("checkout.discounts.off"))
    name = d.normalize(code)
    ended = await app.db.transaction(
        discount_repo.end_code, guild_id=app.env.guild_id, code=name, actor_id=actor_id, now=app.clock.now()
    )
    if ended is None:
        raise Refused(app.op("checkout.discounts.not_found", code=name or "-"))
    await app.audit.record(
        AUDIT_ENDED, actor_id=actor_id, target_type="discount_code", target_id=ended.id, details={"code": ended.code}
    )
    await app.events.publish(DISCOUNT_CODE_ENDED, code_id=ended.id, code=ended.code, actor_id=actor_id)
    return staff.OpReply(app.op("checkout.discounts.ended", code=ended.code), "success")
