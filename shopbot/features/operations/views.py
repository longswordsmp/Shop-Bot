"""Pure builders of the operations messages: order alerts, reminders and failure alerts (staff channel), and the
Owner's private alerts. Values are data supplied by the caller; every word comes from content keys. Missing required
text raises ``MissingContent`` (nothing incomplete is sent).

Banners: staff alerts (orders and failures) show ``staff-notifications``, reminders ``reminder``, and the Owner's
private alerts ``direct-message-notification`` (also when one is posted in the staff channel instead).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shopbot.config.banner_ids import BannerId
from shopbot.config.content import MissingContent
from shopbot.features.operations import content_keys as k
from shopbot.features.operations import repo
from shopbot.ui.spec import ButtonSpec, MessageSpec, rows_of
from shopbot.ui.style import Ui

Values = Mapping[str, str | int]

_ALERT_TONES = {k.NEW_ORDER: "info", k.PAYMENT_SUBMITTED: "warning", k.PAID_ORDER: "success"}
STAFF_ALERT_BANNER: BannerId = "staff-notifications"
REMINDER_BANNER: BannerId = "reminder"
OWNER_ALERT_BANNER: BannerId = "direct-message-notification"


def _embed_message(
    ui: Ui,
    title_key: str,
    description_key: str,
    values: Values,
    *,
    tone: str,
    banner: BannerId,
    ping_role_ids: Sequence[int] = (),
    mention_user_ids: Sequence[int] = (),
    link: ButtonSpec | None = None,
    second_link: ButtonSpec | None = None,
) -> MessageSpec:
    if not ui.content.has(description_key):
        raise MissingContent(description_key)
    embed = ui.content_embed(
        title_key, description_key, values=dict(values), require=(description_key,), tone=tone, timestamp=True
    )
    if embed is None:
        raise MissingContent(description_key)
    return MessageSpec(
        embeds=(embed,),
        rows=rows_of(link, second_link),
        mention_role_ids=tuple(ping_role_ids),
        mention_user_ids=tuple(mention_user_ids),
        banner=ui.banner(banner),
    )


def link_button(ui: Ui, label_key: str, url: str | None) -> ButtonSpec | None:
    """A link button (optional label, which may start with an icon) to a Discord message or channel; None when
    either is missing."""
    if not url or label_key not in ui.content.registry:
        return None
    label = ui.content.render(label_key)
    return ButtonSpec(label=label, style="link", url=url) if label else None


def order_alert(
    ui: Ui,
    alert: str,
    values: Values,
    *,
    owner: bool = False,
    ping_role_ids: Sequence[int] = (),
    record_url: str | None = None,
    ticket_url: str | None = None,
) -> MessageSpec:
    """A new or paid order: the staff alert (pings ``ping_role_ids``) or the Owner's private version, with links to
    the order record and the order ticket."""
    title_key = k.owner_key(alert, "title") if owner else k.alert_key(alert, "title")
    description_key = k.owner_key(alert, "description") if owner else k.alert_key(alert, "description")
    return _embed_message(
        ui,
        title_key,
        description_key,
        values,
        tone=_ALERT_TONES.get(alert, "info"),
        banner=OWNER_ALERT_BANNER if owner else STAFF_ALERT_BANNER,
        ping_role_ids=() if owner else ping_role_ids,
        link=link_button(ui, k.BUTTON_OPEN_RECORD, record_url),
        second_link=link_button(ui, k.BUTTON_OPEN_TICKET, ticket_url),
    )


def stock_alert(ui: Ui, alert: str, values: Values, *, ping_role_ids: Sequence[int] = ()) -> MessageSpec:
    """An item ran low or sold out (``operations.stock``): the staff alert (pings ``ping_role_ids``)."""
    return _embed_message(
        ui,
        k.alert_key(alert, "title"),
        k.alert_key(alert, "description"),
        values,
        tone="danger" if alert == k.SOLD_OUT else "warning",
        banner=STAFF_ALERT_BANNER,
        ping_role_ids=ping_role_ids,
    )


def reminder(
    ui: Ui, kind: str, values: Values, *, ping_role_ids: Sequence[int] = (), link_url: str | None = None
) -> MessageSpec:
    """A staff reminder about a record that waits too long."""
    label_key = k.BUTTON_OPEN_TICKET if kind == repo.UNANSWERED else k.BUTTON_OPEN_RECORD
    return _embed_message(
        ui,
        k.reminder_key(kind, "title"),
        k.reminder_key(kind, "description"),
        values,
        tone="warning",
        banner=REMINDER_BANNER,
        ping_role_ids=ping_role_ids,
        link=link_button(ui, label_key, link_url),
    )


def failure_alert(
    ui: Ui, kind: str, values: Values, *, owner: bool = False, ping_role_ids: Sequence[int] = ()
) -> MessageSpec:
    """A failure staff should know about (no private data), or the Owner's private version of a critical one."""
    if owner:
        title_key, description_key = k.owner_failure_key(kind, "title"), k.owner_failure_key(kind, "description")
    else:
        title_key, description_key = k.failure_key(kind, "title"), k.failure_key(kind, "description")
    return _embed_message(
        ui,
        title_key,
        description_key,
        values,
        tone="danger",
        banner=OWNER_ALERT_BANNER if owner else STAFF_ALERT_BANNER,
        ping_role_ids=() if owner else ping_role_ids,
    )
