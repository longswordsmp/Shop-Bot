"""Technical and business *settings* (not text).

Settings live in ``config/settings.toml``. They describe behaviour: feature flags, server structure,
prices-related configuration, timers, limits. Customer-facing wording never lives here; it lives in the
content configuration (``config/content.toml``) under documented keys.

Parsing is strict: unknown keys and wrong types are fatal errors (they are almost always typos).
Semantic problems (a feature referencing a channel that is not defined, a missing business decision)
are reported per feature by ``settings_problems`` so the affected feature stays disabled while the
rest of the bot keeps working.

Business decisions are never defaulted: values such as currency, payment provider, review rating scale
and review timing start *unset* and the dependent feature reports what is missing. Automation timers
default to 0, meaning "disabled" — the bot never invents an expiry or auto-close policy.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from shopbot.config.banner_ids import BANNER_IDS
from shopbot.core.countries import AREA_CODES, COUNTRIES

T = TypeVar("T")

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# A folder inside the config folder: letters, digits, "-", "_" and "/" between names (no "..", no absolute path).
_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
ACCESS_LEVELS = (
    "public_readonly",
    "public_interactive",
    "members_readonly",
    "members_interactive",
    "staff",
    "admin",
    "inherit",
)
MEMBER_ACCESS_LEVELS = ("members_readonly", "members_interactive")
BUTTON_STYLES = ("primary", "secondary", "success", "danger")
TONES = ("primary", "info", "success", "warning", "danger", "neutral")
RESERVED_TICKET_CATEGORY_IDS = frozenset({"checkout"})
MAX_FORM_FIELDS = 5
CHANNEL_PATTERN_PLACEHOLDERS = frozenset({"prefix", "number", "user"})
# checkout.customer_role_on: the order status that makes a member a customer.
CUSTOMER_ROLE_TRIGGERS = ("paid", "fulfilled")

# Discord permission flag names accepted in server.roles[].permissions.
GUILD_PERMISSION_NAMES = frozenset(
    {
        "add_reactions",
        "attach_files",
        "ban_members",
        "change_nickname",
        "connect",
        "create_events",
        "create_expressions",
        "create_instant_invite",
        "create_private_threads",
        "create_public_threads",
        "deafen_members",
        "embed_links",
        "external_emojis",
        "external_stickers",
        "kick_members",
        "manage_channels",
        "manage_events",
        "manage_expressions",
        "manage_guild",
        "manage_messages",
        "manage_nicknames",
        "manage_roles",
        "manage_threads",
        "manage_webhooks",
        "mention_everyone",
        "moderate_members",
        "move_members",
        "mute_members",
        "priority_speaker",
        "read_message_history",
        "read_messages",
        "send_messages",
        "send_messages_in_threads",
        "send_tts_messages",
        "speak",
        "stream",
        "use_application_commands",
        "use_embedded_activities",
        "use_external_sounds",
        "use_soundboard",
        "use_voice_activation",
        "view_audit_log",
        "view_guild_insights",
    }
)


class SettingsError(Exception):
    """Fatal parse errors. ``errors`` holds one message per problem, each prefixed with its path."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


class _Reader:
    """Typed accessor over a TOML table that records errors instead of raising immediately."""

    def __init__(self, data: Any, path: str, errors: list[str]) -> None:
        if not isinstance(data, dict):
            errors.append(f"{path or '<root>'}: expected a table")
            data = {}
        self._data: dict[str, Any] = data
        self._path = path
        self._errors = errors
        self._seen: set[str] = set()

    def _p(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def _get(self, key: str) -> Any:
        self._seen.add(key)
        return self._data.get(key)

    def error(self, key: str, message: str) -> None:
        self._errors.append(f"{self._p(key)}: {message}")

    def str_(
        self,
        key: str,
        default: str = "",
        *,
        choices: tuple[str, ...] | None = None,
        pattern: re.Pattern[str] | None = None,
        allow_empty: bool = True,
        max_len: int = 200,
    ) -> str:
        value = self._get(key)
        if value is None:
            return default
        if not isinstance(value, str):
            self.error(key, "expected a string")
            return default
        if value == "" and allow_empty:
            return value
        if choices is not None and value not in choices:
            self.error(key, f"must be one of {list(choices)}")
            return default
        if pattern is not None and not pattern.match(value):
            self.error(key, f"must match {pattern.pattern}")
            return default
        if len(value) > max_len:
            self.error(key, f"must be at most {max_len} characters")
            return default
        return value

    def int_(self, key: str, default: int, *, min_: int | None = None, max_: int | None = None) -> int:
        value = self._get(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self.error(key, "expected an integer")
            return default
        if min_ is not None and value < min_:
            self.error(key, f"must be >= {min_}")
            return default
        if max_ is not None and value > max_:
            self.error(key, f"must be <= {max_}")
            return default
        return value

    def bool_(self, key: str, default: bool) -> bool:
        value = self._get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            self.error(key, "expected true or false")
            return default
        return value

    def keys_(self, key: str) -> tuple[str, ...]:
        """A list of internal keys (role/channel/category references)."""
        value = self._get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.error(key, "expected a list of strings")
            return ()
        for item in value:
            if not KEY_PATTERN.match(item):
                self.error(key, f"'{item}' is not a valid key ({KEY_PATTERN.pattern})")
        if len(set(value)) != len(value):
            self.error(key, "contains duplicates")
        return tuple(value)

    def strs_(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.error(key, "expected a list of strings")
            return ()
        return tuple(value)

    def color_(self, key: str) -> int | None:
        value = self.str_(key, "")
        if value == "":
            return None
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            self.error(key, "must be a hex color like #5865F2, or empty")
            return None
        return int(value[1:], 16)

    def url_(self, key: str) -> str:
        value = self.str_(key, "", max_len=2048)
        if value and not value.startswith("https://"):
            self.error(key, "must be an https:// URL or empty")
            return ""
        return value

    def table(self, key: str) -> _Reader:
        value = self._get(key)
        return _Reader({} if value is None else value, self._p(key), self._errors)

    def tables(self, key: str) -> Iterator[_Reader]:
        value = self._get(key)
        if value is None:
            return
        if not isinstance(value, list):
            self.error(key, "expected an array of tables ([[...]])")
            return
        for index, item in enumerate(value):
            yield _Reader(item, f"{self._p(key)}[{index}]", self._errors)

    def finish(self) -> None:
        for key in sorted(set(self._data) - self._seen):
            self._errors.append(f"{self._p(key)}: unknown setting (check spelling)")


# ---------------------------------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BotSettings:
    interaction_cooldown_ms: int = 700
    rate_limit_per_10s: int = 12
    scheduler_poll_seconds: int = 5
    scheduler_batch_size: int = 20
    scheduler_max_attempts: int = 5
    scheduler_retry_base_seconds: int = 30
    sync_commands_on_startup: bool = True
    message_content_intent: bool = True
    members_intent: bool = False


@dataclass(frozen=True)
class PermissionSettings:
    admin_roles: tuple[str, ...] = ()
    staff_roles: tuple[str, ...] = ()
    # Role key /setup gives to OWNER_ID (list it in admin_roles too so it grants admin access).
    owner_role: str = ""
    # Role key that unlocks members_* channels; granted by the verification button.
    member_role: str = ""


@dataclass(frozen=True)
class BrandingSettings:
    colors: dict[str, int | None] = field(default_factory=lambda: dict.fromkeys(TONES))
    logo_url: str = ""
    banner_url: str = ""
    footer_icon_url: str = ""
    panel_thumbnail: bool = False
    panel_banner: bool = False
    show_timestamps: bool = True
    progress_filled: str = ""
    progress_empty: str = ""


@dataclass(frozen=True)
class IconSettings:
    """The bot's custom icons, uploaded as application emojis (``shopbot.ui.icons``)."""

    enabled: bool = False
    directory: str = "emojis"  # folder (relative to the config folder) with one <name>.png per icon


@dataclass(frozen=True)
class BannerSettings:
    """Banner pictures shown at the top of messages (``shopbot.ui.banners``)."""

    enabled: bool = False
    directory: str = "banners"  # folder (relative to the config folder) with one picture per banner id
    off: tuple[str, ...] = ()  # banner ids not shown


@dataclass(frozen=True)
class FeatureFlags:
    info_panels: bool = False
    tickets: bool = False
    catalog: bool = False
    checkout: bool = False
    credits: bool = False
    reviews: bool = False
    verification: bool = False
    new_arrivals: bool = False
    how_to_buy: bool = False
    faq: bool = False
    shipping: bool = False
    announcements: bool = False
    problems: bool = False
    my_orders: bool = False
    dm_notifications: bool = False

    def enabled(self) -> tuple[str, ...]:
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name))


@dataclass(frozen=True)
class RoleDef:
    key: str
    id: int = 0
    permissions: tuple[str, ...] = ()
    # "exact": the role gets exactly ``permissions``; "add": the listed permissions are added and any others the role
    # already has are kept (safe for adopting an existing role).
    permissions_mode: str = "exact"
    color: int | None = None
    hoist: bool = False
    mentionable: bool = False


@dataclass(frozen=True)
class CategoryDef:
    key: str
    access: str = "public_readonly"
    id: int = 0


@dataclass(frozen=True)
class ChannelDef:
    key: str
    category: str = ""
    access: str = "inherit"
    id: int = 0
    slowmode_seconds: int = 0


@dataclass(frozen=True)
class ServerSettings:
    roles: tuple[RoleDef, ...] = ()
    categories: tuple[CategoryDef, ...] = ()
    channels: tuple[ChannelDef, ...] = ()

    def role(self, key: str) -> RoleDef | None:
        return next((r for r in self.roles if r.key == key), None)

    def category(self, key: str) -> CategoryDef | None:
        return next((c for c in self.categories if c.key == key), None)

    def channel(self, key: str) -> ChannelDef | None:
        return next((c for c in self.channels if c.key == key), None)


@dataclass(frozen=True)
class InfoPanelDef:
    key: str
    channel: str
    fields: int = 0
    inline_fields: bool = False
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormField:
    id: str
    style: str = "short"
    required: bool = True
    min_length: int = 0
    max_length: int = 1000


@dataclass(frozen=True)
class TicketCategory:
    id: str
    discord_category: str = ""
    staff_roles: tuple[str, ...] = ()
    max_open_per_user: int = 0
    button_style: str = "primary"
    ping_staff: bool = False
    form: tuple[FormField, ...] = ()


@dataclass(frozen=True)
class TicketSettings:
    panel_channel: str = ""
    panel_style: str = "buttons"
    staff_roles: tuple[str, ...] = ()
    transcript_channel: str = ""
    transcript_dm_user: bool = False
    staff_alert_channel: str = ""
    max_open_per_user: int = 1
    creation_cooldown_seconds: int = 30
    max_created_per_user_per_day: int = 10
    allow_user_close: bool = True
    allow_reopen: bool = True
    user_can_reopen: bool = False
    close_confirmation: bool = True
    lock_on_close: bool = True
    closed_category: str = ""
    delete_after_close_minutes: int = 0
    auto_close_inactive_minutes: int = 0
    auto_close_warning_minutes: int = 0
    channel_name_pattern: str = "{prefix}-{number}"
    number_padding: int = 4
    categories: tuple[TicketCategory, ...] = ()

    def category(self, category_id: str) -> TicketCategory | None:
        return next((c for c in self.categories if c.id == category_id), None)


@dataclass(frozen=True)
class CatalogSettings:
    listing_channel: str = ""
    products_per_listing_message: int = 5
    manager_roles: tuple[str, ...] = ()
    browse_enabled: bool = True
    image_hosts: tuple[str, ...] = ()
    # Products with 1..N units left show the "low stock" availability (warning color); 0 = no low-stock state.
    low_stock_threshold: int = 0


@dataclass(frozen=True)
class FulfillmentOption:
    id: str
    fee_minor: int = 0
    form: tuple[FormField, ...] = ()


@dataclass(frozen=True)
class FeeDef:
    id: str
    kind: str = "flat"
    amount_minor: int = 0
    basis_points: int = 0


@dataclass(frozen=True)
class ExtraDef:
    """An optional extra the customer may add on the delivery step (e.g. shipping protection), at a flat price.
    Labels: content keys checkout.extra.<id>.label / .description."""

    id: str
    amount_minor: int = 0


MAX_EXTRAS = 4  # one row of toggle buttons next to the step's navigation


@dataclass(frozen=True)
class CheckoutSettings:
    panel_channel: str = ""
    order_category: str = ""
    staff_roles: tuple[str, ...] = ()
    staff_channel: str = ""
    currency: str = ""
    currency_exponent: int = -1
    decimal_separator: str = "."
    thousands_separator: str = ","
    max_open_orders_per_user: int = 1
    max_line_quantity: int = 0
    max_cart_lines: int = 20
    min_order_total_minor: int = 0
    products_per_page: int = 25
    session_expiry_minutes: int = 0
    payment_expiry_minutes: int = 0
    payment_reminder_minutes: int = 0
    # Minutes before an unfinished order (cart) expires at which the customer is warned in the order ticket
    # (0 = no warning; needs session_expiry_minutes and must be less than it).
    session_expiry_warning_minutes: int = 0
    delete_channel_after_close_minutes: int = 0
    transcript_on_close: bool = False
    details_form: tuple[FormField, ...] = ()
    # Optional form collected when the customer presses Submit (empty = submit directly).
    submission_form: tuple[FormField, ...] = ()
    # The details step asks for the shipping address: a country we ship to ([shipping] groups) from menus, then a
    # private address form (name, street, city, state/region, postal code), checked against excluded areas.
    ask_address: bool = False
    fulfillment_options: tuple[FulfillmentOption, ...] = ()
    # Optional extras chosen with the delivery option (shown on the delivery step; added to the order as fees).
    extras: tuple[ExtraDef, ...] = ()
    fees: tuple[FeeDef, ...] = ()
    # Customer role (operations package): role key given when an order reaches ``customer_role_on`` ("" = off).
    customer_role: str = ""
    customer_role_on: str = "fulfilled"
    customer_role_remove_on_refund: bool = True

    def fulfillment(self, option_id: str | None) -> FulfillmentOption | None:
        return next((o for o in self.fulfillment_options if o.id == option_id), None)

    def find_extra(self, extra_id: str) -> ExtraDef | None:
        return next((e for e in self.extras if e.id == extra_id), None)


@dataclass(frozen=True)
class PaymentSettings:
    provider: str = ""


@dataclass(frozen=True)
class CreditSettings:
    allow_at_checkout: bool = True


@dataclass(frozen=True)
class ReviewSettings:
    channel: str = ""
    prompt_delay_minutes: int = -1
    rating_scale: int = 0
    allow_comment: bool = True
    comment_required: bool = False
    comment_max_length: int = 1000
    prompt_via: str = "dm_then_channel"
    # Staff approval (moderation): submitted reviews wait in ``approval_channel`` (a staff channel key) for an
    # Approve/Reject decision instead of being published right away.
    require_approval: bool = False
    approval_channel: str = ""


@dataclass(frozen=True)
class NotificationSettings:
    fallback_to_channel: bool = True


@dataclass(frozen=True)
class AuditSettings:
    channel: str = ""
    retention_days: int = 0


# -- Sections for the server specification. Each feature owns its dataclass and its _parse_* function below. --


@dataclass(frozen=True)
class VerificationSettings:
    """[verification]: the #welcome rules panel whose button grants ``permissions.member_role``."""

    channel: str = ""
    # Number of server rules shown on the panel (content keys verification.rule.<n>.name/value); 0 = not set yet.
    rules: int = 0
    # Also confirm a successful verification by direct message (only when features.dm_notifications is on).
    dm_confirmation: bool = False
    # Color of the "I agree, enter the server" button.
    button_style: str = "success"


@dataclass(frozen=True)
class NewArrivalsSettings:
    """[new_arrivals]: the #new-arrivals opt-in panel and automatic product announcements."""

    channel: str = ""
    # Role key (server.roles) of the opt-in notification role the panel button adds/removes.
    role: str = ""
    announce_new_products: bool = True
    announce_restocks: bool = True
    # Mention the notification role in automatic product updates.
    mention_role: bool = True


MAX_GUIDE_STEPS = 25
MAX_GUIDE_LINKS = 25
MAX_FAQ_SECTIONS = 25
MAX_FAQ_QUESTIONS = 25
MAX_SHIPPING_GROUPS = 20
MAX_SHIPPING_INFO_SECTIONS = 10
COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class HowToBuySettings:
    """[how_to_buy]: the step-by-step guide panel."""

    channel: str = ""
    # Number of numbered steps (content keys how_to_buy.step.<n>.name/.value).
    steps: int = 4
    # Channel keys shown as link buttons under the guide (in this order).
    link_channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class FaqSection:
    id: str
    questions: int = 1


@dataclass(frozen=True)
class FaqSettings:
    """[faq]: the FAQ panels (sections of questions and answers)."""

    channel: str = ""
    sections: tuple[FaqSection, ...] = ()


@dataclass(frozen=True)
class ShippingGroup:
    """Countries we ship to, shown together on #shipping (name: content key shipping.group.<id>.name)."""

    id: str
    countries: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShippingSettings:
    """[shipping]: where we ship (#shipping and the address step).

    A country is *available* when it is in one of ``groups``; *partly available* when it also has
    ``excluded_areas`` (e.g. US states); every other country is not available (``not_available`` lists the ones
    #shipping names explicitly)."""

    channel: str = ""
    groups: tuple[ShippingGroup, ...] = ()
    excluded_areas: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    not_available: tuple[str, ...] = ()
    country_names: Mapping[str, str] = field(default_factory=dict)
    # Extra information sections (content keys shipping.info.<n>.name/.value), shown after the countries.
    info_sections: int = 0

    def country_name(self, code: str) -> str:
        return self.country_names.get(code) or COUNTRIES.get(code, code)

    def ships_to(self, code: str) -> bool:
        return any(code in group.countries for group in self.groups)

    @property
    def countries(self) -> tuple[str, ...]:
        """Every country we ship to, in display order."""
        return tuple(code for group in self.groups for code in group.countries)


ANNOUNCEMENT_MAX_FIELDS = 3  # the /announce form holds title + message + these fields (Discord: 5 inputs)
ANNOUNCEMENT_FIELD_MAX_LENGTH = 1024  # an embed field value holds at most 1024 characters
ANNOUNCEMENT_MAX_MENTION_ROLES = 25  # Discord shows at most 25 autocomplete choices


@dataclass(frozen=True)
class AnnouncementField:
    """One optional extra input of the /announce form, posted as an embed field (name: content key
    ``announcements.field.<id>.name``; value: what staff type)."""

    id: str
    style: str = "short"
    required: bool = False
    max_length: int = ANNOUNCEMENT_FIELD_MAX_LENGTH


@dataclass(frozen=True)
class AnnouncementSettings:
    """[announcements]: /announce and the channel it publishes to."""

    channel: str = ""
    # Extra role keys allowed to use /announce besides permissions.staff_roles and admins.
    roles: tuple[str, ...] = ()
    # Role keys staff may choose to mention (ping) with an announcement; empty = announcements never mention.
    mention_roles: tuple[str, ...] = ()
    fields: tuple[AnnouncementField, ...] = ()
    allow_image: bool = True

    def field(self, field_id: str) -> AnnouncementField | None:
        return next((f for f in self.fields if f.id == field_id), None)


@dataclass(frozen=True)
class ProblemType:
    """[[problems.types]]: one kind of issue customers can report. Its label and form wording are content keys."""

    id: str
    form: tuple[FormField, ...] = ()


@dataclass(frozen=True)
class ProblemSettings:
    """[problems]: the Report a Problem workflow."""

    discord_category: str = ""
    eligible_statuses: tuple[str, ...] = ()
    allow_without_order: bool = True
    max_open_per_user: int = 1
    staff_roles: tuple[str, ...] = ()
    require_reason_on_reject: bool = True
    ping_staff: bool = False
    types: tuple[ProblemType, ...] = ()

    def type(self, type_id: str) -> ProblemType | None:
        return next((t for t in self.types if t.id == type_id), None)


# Orders per /myorders page: one card per order plus the list's own card must fit Discord's 10 cards per message.
MY_ORDERS_MAX_PER_PAGE = 9


@dataclass(frozen=True)
class MyOrdersSettings:
    """[my_orders]: the customer /myorders command."""

    orders_per_page: int = 5


@dataclass(frozen=True)
class OperationsSettings:
    """[operations]: staff alerts, reminders, Owner alerts and record-handling options.

    ``staff_channel``: channel key of the staff alert channel (#staff-chat). ``ping_roles``: role keys mentioned on
    alerts that need attention. ``owner_dm_alerts``: the Owner (OWNER_ID) also receives order alerts and critical
    failure alerts by direct message (falling back to the staff channel). Reminder thresholds are minutes; 0 = off.
    ``answers_in_orders_channel``: also post the customer's typed form answers (details, fulfillment option and
    submission forms) as messages under the order record in ``checkout.staff_channel``. Off (the default): the
    answers are shown only privately, to the staff member who opens the record's Details or runs ``/order view``.
    """

    staff_channel: str = ""
    ping_roles: tuple[str, ...] = ()
    owner_dm_alerts: bool = False
    answers_in_orders_channel: bool = False
    unclaimed_order_minutes: int = 0
    unpaid_order_minutes: int = 0
    unfulfilled_order_minutes: int = 0
    unanswered_ticket_minutes: int = 0
    reminder_repeat_minutes: int = 0

    def reminders_enabled(self) -> bool:
        return any(
            minutes > 0
            for minutes in (
                self.unclaimed_order_minutes,
                self.unpaid_order_minutes,
                self.unfulfilled_order_minutes,
                self.unanswered_ticket_minutes,
            )
        )


@dataclass(frozen=True)
class Settings:
    bot: BotSettings = field(default_factory=BotSettings)
    permissions: PermissionSettings = field(default_factory=PermissionSettings)
    branding: BrandingSettings = field(default_factory=BrandingSettings)
    banners: BannerSettings = field(default_factory=BannerSettings)
    icons: IconSettings = field(default_factory=IconSettings)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    server: ServerSettings = field(default_factory=ServerSettings)
    info_panels: tuple[InfoPanelDef, ...] = ()
    tickets: TicketSettings = field(default_factory=TicketSettings)
    catalog: CatalogSettings = field(default_factory=CatalogSettings)
    checkout: CheckoutSettings = field(default_factory=CheckoutSettings)
    payments: PaymentSettings = field(default_factory=PaymentSettings)
    credits: CreditSettings = field(default_factory=CreditSettings)
    reviews: ReviewSettings = field(default_factory=ReviewSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    audit: AuditSettings = field(default_factory=AuditSettings)
    verification: VerificationSettings = field(default_factory=VerificationSettings)
    new_arrivals: NewArrivalsSettings = field(default_factory=NewArrivalsSettings)
    how_to_buy: HowToBuySettings = field(default_factory=HowToBuySettings)
    faq: FaqSettings = field(default_factory=FaqSettings)
    shipping: ShippingSettings = field(default_factory=ShippingSettings)
    announcements: AnnouncementSettings = field(default_factory=AnnouncementSettings)
    problems: ProblemSettings = field(default_factory=ProblemSettings)
    my_orders: MyOrdersSettings = field(default_factory=MyOrdersSettings)
    operations: OperationsSettings = field(default_factory=OperationsSettings)


# ---------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------


def _unique(items: tuple[T, ...], attr: Callable[[T], str], path: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for item in items:
        key = attr(item)
        if key in seen:
            errors.append(f"{path}: duplicate id '{key}'")
        seen.add(key)


def _form(reader: _Reader, key: str) -> tuple[FormField, ...]:
    fields: list[FormField] = []
    for f in reader.tables(key):
        style = f.str_("style", "short", choices=("short", "paragraph"))
        max_cap = 4000
        item = FormField(
            id=f.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            style=style,
            required=f.bool_("required", True),
            min_length=f.int_("min_length", 0, min_=0, max_=max_cap),
            max_length=f.int_("max_length", 1000, min_=1, max_=max_cap),
        )
        if not item.id:
            f.error("id", "is required")
        if item.min_length > item.max_length:
            f.error("min_length", "must not exceed max_length")
        f.finish()
        fields.append(item)
    if len(fields) > MAX_FORM_FIELDS:
        reader.error(key, f"at most {MAX_FORM_FIELDS} fields fit in one Discord form")
    _unique(tuple(fields), lambda x: x.id, f"{reader._p(key)}", reader._errors)
    return tuple(fields)


def parse_settings(data: dict[str, Any]) -> Settings:
    errors: list[str] = []
    root = _Reader(data, "", errors)
    version = root.int_("schema_version", 1, min_=1, max_=1)
    del version

    b = root.table("bot")
    bot = BotSettings(
        interaction_cooldown_ms=b.int_("interaction_cooldown_ms", 700, min_=0, max_=10_000),
        rate_limit_per_10s=b.int_("rate_limit_per_10s", 12, min_=1, max_=100),
        scheduler_poll_seconds=b.int_("scheduler_poll_seconds", 5, min_=1, max_=300),
        scheduler_batch_size=b.int_("scheduler_batch_size", 20, min_=1, max_=200),
        scheduler_max_attempts=b.int_("scheduler_max_attempts", 5, min_=1, max_=50),
        scheduler_retry_base_seconds=b.int_("scheduler_retry_base_seconds", 30, min_=1, max_=3600),
        sync_commands_on_startup=b.bool_("sync_commands_on_startup", True),
        message_content_intent=b.bool_("message_content_intent", True),
        members_intent=b.bool_("members_intent", False),
    )
    b.finish()

    p = root.table("permissions")
    permissions = PermissionSettings(
        admin_roles=p.keys_("admin_roles"),
        staff_roles=p.keys_("staff_roles"),
        owner_role=p.str_("owner_role", "", pattern=KEY_PATTERN),
        member_role=p.str_("member_role", "", pattern=KEY_PATTERN),
    )
    p.finish()

    br = root.table("branding")
    colors_table = br.table("colors")
    colors = {tone: colors_table.color_(tone) for tone in TONES}
    colors_table.finish()
    branding = BrandingSettings(
        colors=colors,
        logo_url=br.url_("logo_url"),
        banner_url=br.url_("banner_url"),
        footer_icon_url=br.url_("footer_icon_url"),
        panel_thumbnail=br.bool_("panel_thumbnail", False),
        panel_banner=br.bool_("panel_banner", False),
        show_timestamps=br.bool_("show_timestamps", True),
        progress_filled=br.str_("progress_filled", "", max_len=8),
        progress_empty=br.str_("progress_empty", "", max_len=8),
    )
    br.finish()

    bn = root.table("banners")
    banners = BannerSettings(
        enabled=bn.bool_("enabled", False),
        directory=bn.str_("directory", "banners", pattern=_FOLDER_PATTERN, allow_empty=False),
        off=bn.strs_("off"),
    )
    for banner_id in banners.off:
        if banner_id not in BANNER_IDS:
            bn.error("off", f"unknown banner '{banner_id}' (known: {', '.join(BANNER_IDS)})")
    bn.finish()

    ic = root.table("icons")
    icons = IconSettings(
        enabled=ic.bool_("enabled", False),
        directory=ic.str_("directory", "emojis", pattern=_FOLDER_PATTERN, allow_empty=False),
    )
    ic.finish()

    fl = root.table("features")
    features = FeatureFlags(**{name: fl.bool_(name, False) for name in FeatureFlags.__dataclass_fields__})
    fl.finish()

    s = root.table("server")
    roles = tuple(
        _finish(
            r,
            RoleDef(
                key=r.str_("key", "", pattern=KEY_PATTERN, allow_empty=False),
                id=r.int_("id", 0, min_=0),
                permissions=_perm_names(r),
                permissions_mode=r.str_("permissions_mode", "exact", choices=("exact", "add")),
                color=r.color_("color"),
                hoist=r.bool_("hoist", False),
                mentionable=r.bool_("mentionable", False),
            ),
        )
        for r in s.tables("roles")
    )
    categories = tuple(
        _finish(
            c,
            CategoryDef(
                key=c.str_("key", "", pattern=KEY_PATTERN, allow_empty=False),
                access=c.str_("access", "public_readonly", choices=ACCESS_LEVELS[:-1]),
                id=c.int_("id", 0, min_=0),
            ),
        )
        for c in s.tables("categories")
    )
    channels = tuple(
        _finish(
            c,
            ChannelDef(
                key=c.str_("key", "", pattern=KEY_PATTERN, allow_empty=False),
                category=c.str_("category", "", pattern=KEY_PATTERN),
                access=c.str_("access", "inherit", choices=ACCESS_LEVELS),
                id=c.int_("id", 0, min_=0),
                slowmode_seconds=c.int_("slowmode_seconds", 0, min_=0, max_=21600),
            ),
        )
        for c in s.tables("channels")
    )
    s.finish()
    for kind, items in (("server.roles", roles), ("server.categories", categories), ("server.channels", channels)):
        _unique(items, lambda x: x.key, kind, errors)  # type: ignore[attr-defined]
    server = ServerSettings(roles=roles, categories=categories, channels=channels)

    info_panels = tuple(
        _finish(
            ip,
            InfoPanelDef(
                key=ip.str_("key", "", pattern=KEY_PATTERN, allow_empty=False),
                channel=ip.str_("channel", "", pattern=KEY_PATTERN),
                fields=ip.int_("fields", 0, min_=0, max_=25),
                inline_fields=ip.bool_("inline_fields", False),
                links=ip.keys_("links"),
            ),
        )
        for ip in root.tables("info_panels")
    )
    _unique(info_panels, lambda x: x.key, "info_panels", errors)
    for index, panel in enumerate(info_panels):
        if len(panel.links) > 25:
            errors.append(f"info_panels[{index}].links: at most 25 link buttons")

    t = root.table("tickets")
    ticket_categories: list[TicketCategory] = []
    for c in t.tables("categories"):
        category = TicketCategory(
            id=c.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            discord_category=c.str_("discord_category", "", pattern=KEY_PATTERN),
            staff_roles=c.keys_("staff_roles"),
            max_open_per_user=c.int_("max_open_per_user", 0, min_=0, max_=50),
            button_style=c.str_("button_style", "primary", choices=BUTTON_STYLES),
            ping_staff=c.bool_("ping_staff", False),
            form=_form(c, "form"),
        )
        if category.id in RESERVED_TICKET_CATEGORY_IDS:
            c.error("id", f"'{category.id}' is reserved")
        c.finish()
        ticket_categories.append(category)
    tickets = TicketSettings(
        panel_channel=t.str_("panel_channel", "", pattern=KEY_PATTERN),
        panel_style=t.str_("panel_style", "buttons", choices=("buttons", "select")),
        staff_roles=t.keys_("staff_roles"),
        transcript_channel=t.str_("transcript_channel", "", pattern=KEY_PATTERN),
        transcript_dm_user=t.bool_("transcript_dm_user", False),
        staff_alert_channel=t.str_("staff_alert_channel", "", pattern=KEY_PATTERN),
        max_open_per_user=t.int_("max_open_per_user", 1, min_=1, max_=50),
        creation_cooldown_seconds=t.int_("creation_cooldown_seconds", 30, min_=0, max_=86400),
        max_created_per_user_per_day=t.int_("max_created_per_user_per_day", 10, min_=0, max_=1000),
        allow_user_close=t.bool_("allow_user_close", True),
        allow_reopen=t.bool_("allow_reopen", True),
        user_can_reopen=t.bool_("user_can_reopen", False),
        close_confirmation=t.bool_("close_confirmation", True),
        lock_on_close=t.bool_("lock_on_close", True),
        closed_category=t.str_("closed_category", "", pattern=KEY_PATTERN),
        delete_after_close_minutes=t.int_("delete_after_close_minutes", 0, min_=0, max_=525_600),
        auto_close_inactive_minutes=t.int_("auto_close_inactive_minutes", 0, min_=0, max_=525_600),
        auto_close_warning_minutes=t.int_("auto_close_warning_minutes", 0, min_=0, max_=525_600),
        channel_name_pattern=t.str_("channel_name_pattern", "{prefix}-{number}", allow_empty=False, max_len=100),
        number_padding=t.int_("number_padding", 4, min_=0, max_=10),
        categories=tuple(ticket_categories),
    )
    t.finish()
    _unique(tickets.categories, lambda x: x.id, "tickets.categories", errors)
    if len(tickets.categories) > 25:
        errors.append("tickets.categories: at most 25 categories fit in one panel")
    pattern_tokens = set(re.findall(r"\{([a-z_]+)\}", tickets.channel_name_pattern))
    if not pattern_tokens <= CHANNEL_PATTERN_PLACEHOLDERS:
        errors.append(
            "tickets.channel_name_pattern: unknown placeholders "
            f"{sorted(pattern_tokens - CHANNEL_PATTERN_PLACEHOLDERS)}"
        )
    if "number" not in pattern_tokens:
        errors.append("tickets.channel_name_pattern: must contain {number} so channel names are unique")

    ca = root.table("catalog")
    catalog = CatalogSettings(
        listing_channel=ca.str_("listing_channel", "", pattern=KEY_PATTERN),
        products_per_listing_message=ca.int_("products_per_listing_message", 5, min_=1, max_=10),
        manager_roles=ca.keys_("manager_roles"),
        browse_enabled=ca.bool_("browse_enabled", True),
        image_hosts=ca.strs_("image_hosts"),
        low_stock_threshold=ca.int_("low_stock_threshold", 0, min_=0, max_=1_000_000),
    )
    ca.finish()

    co = root.table("checkout")
    options: list[FulfillmentOption] = []
    for o in co.tables("fulfillment_options"):
        options.append(
            _finish(
                o,
                FulfillmentOption(
                    id=o.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
                    fee_minor=o.int_("fee_minor", 0, min_=0, max_=10**12),
                    form=_form(o, "form"),
                ),
            )
        )
    fees: list[FeeDef] = []
    for f in co.tables("fees"):
        fee = FeeDef(
            id=f.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            kind=f.str_("kind", "flat", choices=("flat", "percent")),
            amount_minor=f.int_("amount_minor", 0, min_=0, max_=10**12),
            basis_points=f.int_("basis_points", 0, min_=0, max_=100_000),
        )
        f.finish()
        fees.append(fee)
    extras: list[ExtraDef] = []
    for x in co.tables("extras"):
        extra = ExtraDef(
            id=x.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            amount_minor=x.int_("amount_minor", 0, min_=1, max_=10**9),
        )
        if not extra.id:
            x.error("id", "is required")
        if extra.amount_minor <= 0:
            x.error("amount_minor", "is required (the price in minor units, e.g. 300 for $3.00)")
        x.finish()
        extras.append(extra)
    checkout = CheckoutSettings(
        panel_channel=co.str_("panel_channel", "", pattern=KEY_PATTERN),
        order_category=co.str_("order_category", "", pattern=KEY_PATTERN),
        staff_roles=co.keys_("staff_roles"),
        staff_channel=co.str_("staff_channel", "", pattern=KEY_PATTERN),
        currency=co.str_("currency", "", pattern=re.compile(r"^[A-Z]{3}$")),
        currency_exponent=co.int_("currency_exponent", -1, min_=-1, max_=4),
        decimal_separator=co.str_("decimal_separator", ".", max_len=2),
        thousands_separator=co.str_("thousands_separator", ",", max_len=2),
        max_open_orders_per_user=co.int_("max_open_orders_per_user", 1, min_=1, max_=20),
        max_line_quantity=co.int_("max_line_quantity", 0, min_=0, max_=1_000_000),
        max_cart_lines=co.int_("max_cart_lines", 20, min_=1, max_=20),
        min_order_total_minor=co.int_("min_order_total_minor", 0, min_=0, max_=10**12),
        products_per_page=co.int_("products_per_page", 25, min_=1, max_=25),
        session_expiry_minutes=co.int_("session_expiry_minutes", 0, min_=0, max_=525_600),
        payment_expiry_minutes=co.int_("payment_expiry_minutes", 0, min_=0, max_=525_600),
        payment_reminder_minutes=co.int_("payment_reminder_minutes", 0, min_=0, max_=525_600),
        session_expiry_warning_minutes=co.int_("session_expiry_warning_minutes", 0, min_=0, max_=525_600),
        delete_channel_after_close_minutes=co.int_("delete_channel_after_close_minutes", 0, min_=0, max_=525_600),
        transcript_on_close=co.bool_("transcript_on_close", False),
        details_form=_form(co, "details_form"),
        ask_address=co.bool_("ask_address", False),
        submission_form=_form(co, "submission_form"),
        fulfillment_options=tuple(options),
        fees=tuple(fees),
        extras=tuple(extras),
        customer_role=co.str_("customer_role", "", pattern=KEY_PATTERN),
        customer_role_on=co.str_("customer_role_on", "fulfilled", choices=CUSTOMER_ROLE_TRIGGERS),
        customer_role_remove_on_refund=co.bool_("customer_role_remove_on_refund", True),
    )
    co.finish()
    _unique(checkout.fulfillment_options, lambda x: x.id, "checkout.fulfillment_options", errors)
    _unique(checkout.fees, lambda x: x.id, "checkout.fees", errors)
    _unique(checkout.extras, lambda x: x.id, "checkout.extras", errors)
    if len(checkout.extras) > MAX_EXTRAS:
        errors.append(f"checkout.extras: at most {MAX_EXTRAS} extras (one row of buttons)")
    if checkout.extras and not checkout.fulfillment_options:
        errors.append("checkout.extras: extras are chosen on the delivery step; add [[checkout.fulfillment_options]]")
    if len(checkout.fulfillment_options) > 25:
        errors.append("checkout.fulfillment_options: at most 25 options fit in one selection menu")
    if checkout.decimal_separator == checkout.thousands_separator and checkout.decimal_separator:
        errors.append("checkout: decimal_separator and thousands_separator must differ")

    pa = root.table("payments")
    payments = PaymentSettings(provider=pa.str_("provider", "", pattern=KEY_PATTERN))
    pa.finish()

    cr = root.table("credits")
    credits = CreditSettings(allow_at_checkout=cr.bool_("allow_at_checkout", True))
    cr.finish()

    rv = root.table("reviews")
    reviews = ReviewSettings(
        channel=rv.str_("channel", "", pattern=KEY_PATTERN),
        prompt_delay_minutes=rv.int_("prompt_delay_minutes", -1, min_=-1, max_=525_600),
        rating_scale=rv.int_("rating_scale", 0, min_=0, max_=10),
        allow_comment=rv.bool_("allow_comment", True),
        comment_required=rv.bool_("comment_required", False),
        comment_max_length=rv.int_("comment_max_length", 1000, min_=1, max_=4000),
        prompt_via=rv.str_("prompt_via", "dm_then_channel", choices=("dm", "channel", "dm_then_channel")),
        require_approval=rv.bool_("require_approval", False),
        approval_channel=rv.str_("approval_channel", "", pattern=KEY_PATTERN),
    )
    rv.finish()

    no = root.table("notifications")
    notifications = NotificationSettings(fallback_to_channel=no.bool_("fallback_to_channel", True))
    no.finish()

    au = root.table("audit")
    audit = AuditSettings(
        channel=au.str_("channel", "", pattern=KEY_PATTERN),
        retention_days=au.int_("retention_days", 0, min_=0, max_=3650),
    )
    au.finish()

    verification = _parse_verification(root.table("verification"))
    new_arrivals = _parse_new_arrivals(root.table("new_arrivals"))
    how_to_buy = _parse_how_to_buy(root.table("how_to_buy"))
    faq = _parse_faq(root.table("faq"))
    shipping = _parse_shipping(root.table("shipping"))
    announcements = _parse_announcements(root.table("announcements"))
    problems = _parse_problems(root.table("problems"))
    my_orders = _parse_my_orders(root.table("my_orders"))
    operations = _parse_operations(root.table("operations"))

    root.finish()
    if errors:
        raise SettingsError(errors)
    return Settings(
        bot=bot,
        permissions=permissions,
        branding=branding,
        banners=banners,
        icons=icons,
        features=features,
        server=server,
        info_panels=info_panels,
        tickets=tickets,
        catalog=catalog,
        checkout=checkout,
        payments=payments,
        credits=credits,
        reviews=reviews,
        notifications=notifications,
        audit=audit,
        verification=verification,
        new_arrivals=new_arrivals,
        how_to_buy=how_to_buy,
        faq=faq,
        shipping=shipping,
        announcements=announcements,
        problems=problems,
        my_orders=my_orders,
        operations=operations,
    )


def _finish(reader: _Reader, value: T) -> T:
    reader.finish()
    return value


def _perm_names(reader: _Reader) -> tuple[str, ...]:
    names = reader.strs_("permissions")
    for name in names:
        if name not in GUILD_PERMISSION_NAMES:
            reader.error("permissions", f"unknown Discord permission '{name}'")
    return names


# -- Parsers for the server-specification sections (one per feature; each finishes its own table). -----------


MAX_VERIFICATION_RULES = 25  # one embed field per rule; Discord allows 25 fields per embed


def _parse_verification(r: _Reader) -> VerificationSettings:
    return _finish(
        r,
        VerificationSettings(
            channel=r.str_("channel", "", pattern=KEY_PATTERN),
            rules=r.int_("rules", 0, min_=0, max_=MAX_VERIFICATION_RULES),
            dm_confirmation=r.bool_("dm_confirmation", False),
            button_style=r.str_("button_style", "success", choices=BUTTON_STYLES, allow_empty=False),
        ),
    )


def _parse_new_arrivals(r: _Reader) -> NewArrivalsSettings:
    return _finish(
        r,
        NewArrivalsSettings(
            channel=r.str_("channel", "", pattern=KEY_PATTERN),
            role=r.str_("role", "", pattern=KEY_PATTERN),
            announce_new_products=r.bool_("announce_new_products", True),
            announce_restocks=r.bool_("announce_restocks", True),
            mention_role=r.bool_("mention_role", True),
        ),
    )


def _parse_how_to_buy(r: _Reader) -> HowToBuySettings:
    value = HowToBuySettings(
        channel=r.str_("channel", "", pattern=KEY_PATTERN),
        steps=r.int_("steps", 4, min_=1, max_=MAX_GUIDE_STEPS),
        link_channels=r.keys_("link_channels"),
    )
    if len(value.link_channels) > MAX_GUIDE_LINKS:
        r.error("link_channels", f"at most {MAX_GUIDE_LINKS} link buttons fit under one message")
    return _finish(r, value)


def _parse_faq(r: _Reader) -> FaqSettings:
    sections = tuple(
        _finish(
            s,
            FaqSection(
                id=s.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
                questions=s.int_("questions", 1, min_=1, max_=MAX_FAQ_QUESTIONS),
            ),
        )
        for s in r.tables("sections")
    )
    for index, section in enumerate(sections):
        if not section.id:
            r.error(f"sections[{index}].id", "is required")
    if len(sections) > MAX_FAQ_SECTIONS:
        r.error("sections", f"at most {MAX_FAQ_SECTIONS} sections")
    _unique(sections, lambda x: x.id, r._p("sections"), r._errors)
    return _finish(r, FaqSettings(channel=r.str_("channel", "", pattern=KEY_PATTERN), sections=sections))


def _parse_shipping(r: _Reader) -> ShippingSettings:
    groups: list[ShippingGroup] = []
    for g in r.tables("groups"):
        group = ShippingGroup(
            id=g.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            countries=g.strs_("countries"),
        )
        if not group.id:
            g.error("id", "is required")
        if not group.countries:
            g.error("countries", 'list at least one country code, e.g. ["CA", "US"]')
        _country_codes(g, "countries", group.countries)
        groups.append(_finish(g, group))
    if len(groups) > MAX_SHIPPING_GROUPS:
        r.error("groups", f"at most {MAX_SHIPPING_GROUPS} groups")
    _unique(tuple(groups), lambda x: x.id, r._p("groups"), r._errors)
    shipped = [code for group in groups for code in group.countries]
    for code in sorted({c for c in shipped if shipped.count(c) > 1}):
        r.error("groups", f"{code} is listed in more than one group")
    not_available = r.strs_("not_available")
    _country_codes(r, "not_available", not_available)
    for code in not_available:
        if code in shipped:
            r.error("not_available", f"{code} is also listed in a group (we ship there)")
    areas_table = r.table("excluded_areas")
    excluded: dict[str, tuple[str, ...]] = {}
    for code in list(areas_table._data):
        areas = areas_table.strs_(code)
        if not COUNTRY_CODE_PATTERN.match(code) or code not in COUNTRIES:
            areas_table.error(code, "is not a country code (two capital letters, e.g. US)")
            continue
        if code not in shipped:
            areas_table.error(code, "is not in any [[shipping.groups]] (we do not ship there at all)")
        known = AREA_CODES.get(code)
        for area in areas:
            if known is not None and area not in known:
                areas_table.error(code, f"'{area}' is not a state code of {code} (e.g. \"AK\")")
            elif not area.strip():
                areas_table.error(code, "contains an empty name")
        if len(set(areas)) != len(areas):
            areas_table.error(code, "contains duplicates")
        if areas:
            excluded[code] = areas
    areas_table.finish()
    names_table = r.table("country_names")
    names: dict[str, str] = {}
    for code in list(names_table._data):
        name = names_table.str_(code, "", allow_empty=False, max_len=60)
        if code not in COUNTRIES:
            names_table.error(code, "is not a country code (two capital letters, e.g. CZ)")
        elif name:
            names[code] = name
    names_table.finish()
    value = ShippingSettings(
        channel=r.str_("channel", "", pattern=KEY_PATTERN),
        groups=tuple(groups),
        excluded_areas=excluded,
        not_available=not_available,
        country_names=names,
        info_sections=r.int_("info_sections", 0, min_=0, max_=MAX_SHIPPING_INFO_SECTIONS),
    )
    return _finish(r, value)


def _country_codes(r: _Reader, key: str, codes: tuple[str, ...]) -> None:
    for code in codes:
        if not COUNTRY_CODE_PATTERN.match(code) or code not in COUNTRIES:
            r.error(key, f"'{code}' is not a country code (two capital letters, e.g. US, CA, DE)")
    if len(set(codes)) != len(codes):
        r.error(key, "contains duplicates")


def _parse_announcements(r: _Reader) -> AnnouncementSettings:
    fields: list[AnnouncementField] = []
    for f in r.tables("fields"):
        item = AnnouncementField(
            id=f.str_("id", "", pattern=KEY_PATTERN, allow_empty=False),
            style=f.str_("style", "short", choices=("short", "paragraph")),
            required=f.bool_("required", False),
            max_length=f.int_("max_length", ANNOUNCEMENT_FIELD_MAX_LENGTH, min_=1, max_=ANNOUNCEMENT_FIELD_MAX_LENGTH),
        )
        if not item.id:
            f.error("id", "is required")
        f.finish()
        fields.append(item)
    if len(fields) > ANNOUNCEMENT_MAX_FIELDS:
        r.error(
            "fields",
            f"at most {ANNOUNCEMENT_MAX_FIELDS} fields fit in the /announce form (with the title and the message)",
        )
    _unique(tuple(fields), lambda x: x.id, r._p("fields"), r._errors)
    mention_roles = r.keys_("mention_roles")
    if len(mention_roles) > ANNOUNCEMENT_MAX_MENTION_ROLES:
        r.error("mention_roles", f"at most {ANNOUNCEMENT_MAX_MENTION_ROLES} roles can be offered")
    settings = AnnouncementSettings(
        channel=r.str_("channel", "", pattern=KEY_PATTERN),
        roles=r.keys_("roles"),
        mention_roles=mention_roles,
        fields=tuple(fields),
        allow_image=r.bool_("allow_image", True),
    )
    return _finish(r, settings)


MAX_PROBLEM_TYPES = 25  # one selection menu


def _parse_problems(r: _Reader) -> ProblemSettings:
    types: list[ProblemType] = []
    for t in r.tables("types"):
        item = ProblemType(id=t.str_("id", "", pattern=KEY_PATTERN, allow_empty=False), form=_form(t, "form"))
        if not item.id:
            t.error("id", "is required")
        t.finish()
        types.append(item)
    if len(types) > MAX_PROBLEM_TYPES:
        r.error("types", f"at most {MAX_PROBLEM_TYPES} issue types fit in one selection menu")
    _unique(tuple(types), lambda x: x.id, r._p("types"), r._errors)
    return _finish(
        r,
        ProblemSettings(
            discord_category=r.str_("discord_category", "", pattern=KEY_PATTERN),
            eligible_statuses=r.keys_("eligible_statuses"),
            allow_without_order=r.bool_("allow_without_order", True),
            max_open_per_user=r.int_("max_open_per_user", 1, min_=1, max_=20),
            staff_roles=r.keys_("staff_roles"),
            require_reason_on_reject=r.bool_("require_reason_on_reject", True),
            ping_staff=r.bool_("ping_staff", False),
            types=tuple(types),
        ),
    )


def _parse_my_orders(r: _Reader) -> MyOrdersSettings:
    return _finish(
        r, MyOrdersSettings(orders_per_page=r.int_("orders_per_page", 5, min_=1, max_=MY_ORDERS_MAX_PER_PAGE))
    )


_MAX_MINUTES = 525_600  # one year


def _parse_operations(r: _Reader) -> OperationsSettings:
    return _finish(
        r,
        OperationsSettings(
            staff_channel=r.str_("staff_channel", "", pattern=KEY_PATTERN),
            ping_roles=r.keys_("ping_roles"),
            owner_dm_alerts=r.bool_("owner_dm_alerts", False),
            answers_in_orders_channel=r.bool_("answers_in_orders_channel", False),
            unclaimed_order_minutes=r.int_("unclaimed_order_minutes", 0, min_=0, max_=_MAX_MINUTES),
            unpaid_order_minutes=r.int_("unpaid_order_minutes", 0, min_=0, max_=_MAX_MINUTES),
            unfulfilled_order_minutes=r.int_("unfulfilled_order_minutes", 0, min_=0, max_=_MAX_MINUTES),
            unanswered_ticket_minutes=r.int_("unanswered_ticket_minutes", 0, min_=0, max_=_MAX_MINUTES),
            reminder_repeat_minutes=r.int_("reminder_repeat_minutes", 0, min_=0, max_=_MAX_MINUTES),
        ),
    )
