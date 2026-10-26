"""Pure builders for discount codes outside the review step: the claim button of an announcement, the private replies
to it (the claimed coupon with its terms and how to use it; the offer that ended), and the staff side of ``/discount``
(operator text: the terms in plain words, the code list).

Like ``views``, nothing here calls Discord or the database; missing required content raises ``MissingContent``. The
review step's discount buttons, form and summary lines are in ``views`` (with the rest of the workflow message).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from shopbot.config.content import MissingContent
from shopbot.config.settings import Settings
from shopbot.features.checkout import content_keys as k
from shopbot.features.checkout import discounts as d
from shopbot.features.checkout import views
from shopbot.features.checkout.discount_repo import CodeStats
from shopbot.features.server_keys import channel_name_key
from shopbot.ui.custom_ids import cid
from shopbot.ui.spec import ButtonSpec, ButtonStyle, MessageSpec
from shopbot.ui.style import Ui, chunk_lines

OperatorText = views.OperatorText
# Writes an amount in minor units (the shop currency).
Money = Callable[[int], str]

# The claim button's route (restart-safe: the custom id carries only the code's id; the handler reloads the code).
CLAIM_ROUTE = "dc"
CLAIM_ACTION = "claim"
# Characters of one /discount list message (an operator card's description holds 4096).
LIST_CHUNK = 3900


def claim_id(code_id: int) -> str:
    """``sb:dc:claim:<code_id>``: claims the code's coupon for the member who presses it."""
    return cid(CLAIM_ROUTE, CLAIM_ACTION, code_id)


def claim_button(ui: Ui, label_key: str, code_id: int, *, style: ButtonStyle = "success") -> ButtonSpec:
    """The claim button of a post (e.g. an announcement): its label from the content key ``label_key`` (it may start
    with an icon), its custom id from ``claim_id``. Raises ``MissingContent`` when the label is not set."""
    button = ui.button(label_key, claim_id(code_id), style=style)
    if button is None:
        raise MissingContent(label_key)
    return button


def percent_text(basis_points: int) -> str:
    """A percentage in basis points as people write it: 1000 -> "10", 1250 -> "12.5"."""
    whole, rest = divmod(basis_points, 100)
    return str(whole) if not rest else f"{whole}.{rest:02d}".rstrip("0")


def _names(names: Sequence[str]) -> str:
    return ", ".join(views.escape(name) for name in names)


# -- customers: the claimed coupon ----------------------------------------------------------------------------------


def terms_lines(ui: Ui, money: Money, code: d.Code, product_names: Sequence[str]) -> list[str]:
    """The coupon's terms in plain words, one per line: what it gives (percent, amount, or buy one get one free: the
    cheaper item of every pair is free), the items it counts when limited, the minimum, the end date, once per
    customer."""
    if code.kind == d.PERCENT:
        lines = [views._text(ui, k.TERMS_PERCENT, percent=percent_text(code.value))]
    elif code.kind == d.AMOUNT:
        lines = [views._text(ui, k.TERMS_AMOUNT, amount=money(code.value))]
    else:
        lines = [views._text(ui, k.TERMS_BOGO)]
    if code.limited and product_names:
        lines.append(views._text(ui, k.TERMS_PRODUCTS, products=_names(product_names)))
    if code.min_subtotal_minor > 0:
        lines.append(views._text(ui, k.TERMS_MINIMUM, minimum=money(code.min_subtotal_minor)))
    if code.expires_at is not None:
        lines.append(views._text(ui, k.TERMS_EXPIRES, date=f"<t:{code.expires_at}:f>"))
    if code.once_per_customer:
        lines.append(views._text(ui, k.TERMS_ONCE))
    return lines


def catalog_values(ui: Ui, settings: Settings, links: Mapping[str, str] | None) -> dict[str, str | int]:
    """The value of ``{channel_catalog}`` (``content_keys.catalog_placeholder``): the catalog channel as a clickable
    link, or its name while it is not known (before /setup created it)."""
    names = k.catalog_placeholder(settings)
    if not names:
        return {}
    link = (links or {}).get(names[0])
    if not link:
        name = views._optional(ui, channel_name_key(settings.catalog.listing_channel))
        link = f"#{name}" if name else ""
    return {names[0]: link}


def claim_reply(
    ui: Ui,
    settings: Settings,
    code: d.Code,
    product_names: Sequence[str],
    *,
    links: Mapping[str, str] | None = None,
) -> MessageSpec:
    """The private reply after a member claimed a coupon (the same reply when they claim it again): the code in bold,
    its terms, and how to use it (add items, then press Use coupon on the review step). ``links``: the channel links
    the bot knows (``server_keys.channel_mentions``)."""
    money: Money = lambda minor: views.money(ui, minor)  # noqa: E731
    values = {"code": views.escape(code.code)}
    head = views._content_embed(
        ui,
        k.CLAIM_TITLE,
        k.CLAIM_DESCRIPTION,
        required=(k.CLAIM_TITLE, k.CLAIM_DESCRIPTION),
        values=values,
        tone="success",
        timestamp=True,
    )
    terms = views.section(views._text(ui, k.CLAIM_HEADING), terms_lines(ui, money, code, product_names))
    howto = views._text(ui, k.CLAIM_HOWTO, **values, **catalog_values(ui, settings, links))
    usage = views.section(views._text(ui, k.CLAIM_HOWTO_HEADING), [howto])
    embeds = views._card_embeds(head, [head.description, terms, usage])
    return MessageSpec(embeds=tuple(embeds), banner=ui.banner("successful-confirmation"))


def offer_ended_reply(ui: Ui) -> MessageSpec:
    """The private reply to a claim button whose offer has ended (the code ended or expired, was used up or no longer
    exists, or discount codes are switched off)."""
    embed = views._content_embed(
        ui, k.CLAIM_ENDED_TITLE, k.CLAIM_ENDED, required=(k.CLAIM_ENDED_TITLE, k.CLAIM_ENDED), tone="neutral"
    )
    return MessageSpec(embeds=(embed,), banner=ui.banner("general-response"))  # the member cancelled nothing


# -- staff: /discount (operator text) ---------------------------------------------------------------------------------


def terms_text(op: OperatorText, money: Money, code: d.Code, product_names: Sequence[str], *, now: int) -> str:
    """A code's terms in plain words for staff (operator text), e.g. "10% off · every item · minimum $20.00 · at
    most 100 uses · once per customer · until <date>"."""
    if code.kind == d.PERCENT:
        parts = [op("checkout.discounts.terms.percent", percent=percent_text(code.value))]
    elif code.kind == d.AMOUNT:
        parts = [op("checkout.discounts.terms.amount", amount=money(code.value))]
    else:
        parts = [op("checkout.discounts.terms.bogo")]
    if code.limited:
        names = _names(product_names) if product_names else ", ".join(str(p) for p in sorted(code.product_ids))
        parts.append(op("checkout.discounts.terms.products", products=names))
    else:
        parts.append(op("checkout.discounts.terms.all_items"))
    if code.min_subtotal_minor > 0:
        parts.append(op("checkout.discounts.terms.minimum", minimum=money(code.min_subtotal_minor)))
    if code.max_uses is not None:
        parts.append(op("checkout.discounts.terms.max_uses", max=code.max_uses))
    if code.once_per_customer:
        parts.append(op("checkout.discounts.terms.once"))
    if code.ended_at is not None:
        parts.append(op("checkout.discounts.terms.ended", date=f"<t:{code.ended_at}:f>"))
    elif code.expires_at is not None:
        key = "checkout.discounts.terms.expired" if code.expired(now) else "checkout.discounts.terms.expires"
        parts.append(op(key, date=f"<t:{code.expires_at}:f>"))
    else:
        parts.append(op("checkout.discounts.terms.no_expiry"))
    return op("checkout.discounts.terms.join").join(parts)


def created_reply(
    ui: Ui, op: OperatorText, money: Money, code: d.Code, product_names: Sequence[str], *, now: int
) -> MessageSpec:
    """The private reply to ``/discount create``: the code and its terms in plain words."""
    title = op("checkout.discounts.created", code=code.code)
    terms = terms_text(op, money, code, product_names, now=now)
    description = "\n\n".join(
        (op("checkout.discounts.created_terms", terms=terms), op("checkout.discounts.created_hint"))
    )
    return MessageSpec(
        embeds=(ui.operator_embed(title, description, tone="success"),), banner=ui.banner("general-response")
    )


def _list_line(op: OperatorText, money: Money, stats: CodeStats, now: int) -> str:
    code = stats.code
    uses = (
        op("checkout.discounts.list.uses_max", uses=stats.uses, max=code.max_uses)
        if code.max_uses is not None
        else op("checkout.discounts.list.uses", uses=stats.uses)
    )
    return op(
        "checkout.discounts.list.line",
        code=views.escape(code.code),
        terms=terms_text(op, money, code, stats.product_names, now=now),
        uses=uses,
        claims=stats.claims,
    )


def list_messages(
    ui: Ui,
    op: OperatorText,
    money: Money,
    usable: Sequence[CodeStats],
    ended: Sequence[CodeStats],
    *,
    now: int,
) -> list[MessageSpec]:
    """``/discount list`` (operator text): the codes that can be used now (type and terms in plain words, products by
    name, uses and claims, end date), then the codes that ended or expired most recently. Split over several
    messages when long (nothing is cut)."""
    lines = [op("checkout.discounts.list.usable", count=len(usable))]
    for stats in usable:
        lines += [_list_line(op, money, stats, now), ""]  # a blank line between two codes
    if not usable:
        lines += [op("checkout.discounts.list.none"), ""]
    if ended:
        lines.append(op("checkout.discounts.list.ended", count=len(ended)))
        for stats in ended:
            lines += [_list_line(op, money, stats, now), ""]
    blocks = [block.strip("\n") for block in chunk_lines(lines[:-1], LIST_CHUNK)]
    title = op("checkout.discounts.list.title")
    specs = [MessageSpec(embeds=(ui.operator_embed(title, block),)) for block in blocks]
    return [replace(specs[0], banner=ui.banner("staff-dashboard")), *specs[1:]]
