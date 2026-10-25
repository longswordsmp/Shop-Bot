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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
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
    delete_channel_after_close_minutes: int = 0
    transcript_on_close: bool = False
    details_form: tuple[FormField, ...] = ()
    fulfillment_options: tuple[FulfillmentOption, ...] = ()
    fees: tuple[FeeDef, ...] = ()

    def fulfillment(self, option_id: str | None) -> FulfillmentOption | None:
        return next((o for o in self.fulfillment_options if o.id == option_id), None)


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


@dataclass(frozen=True)
class HowToBuySettings:
    """[how_to_buy]: the step-by-step guide panel."""

    channel: str = ""


@dataclass(frozen=True)
class FaqSettings:
    """[faq]: the FAQ panels (sections of questions and answers)."""

    channel: str = ""


@dataclass(frozen=True)
class ShippingSettings:
    """[shipping]: the fulfillment/regions panel."""

    channel: str = ""


@dataclass(frozen=True)
class AnnouncementSettings:
    """[announcements]: /announce and the channel it publishes to."""

    channel: str = ""


@dataclass(frozen=True)
class ProblemSettings:
    """[problems]: the Report a Problem workflow."""


@dataclass(frozen=True)
class MyOrdersSettings:
    """[my_orders]: the customer /myorders command."""


@dataclass(frozen=True)
class OperationsSettings:
    """[operations]: staff alerts, reminders, Owner alerts and record-handling options."""


@dataclass(frozen=True)
class Settings:
    bot: BotSettings = field(default_factory=BotSettings)
    permissions: PermissionSettings = field(default_factory=PermissionSettings)
    branding: BrandingSettings = field(default_factory=BrandingSettings)
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
        delete_channel_after_close_minutes=co.int_("delete_channel_after_close_minutes", 0, min_=0, max_=525_600),
        transcript_on_close=co.bool_("transcript_on_close", False),
        details_form=_form(co, "details_form"),
        fulfillment_options=tuple(options),
        fees=tuple(fees),
    )
    co.finish()
    _unique(checkout.fulfillment_options, lambda x: x.id, "checkout.fulfillment_options", errors)
    _unique(checkout.fees, lambda x: x.id, "checkout.fees", errors)
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


def _parse_verification(r: _Reader) -> VerificationSettings:
    return _finish(r, VerificationSettings(channel=r.str_("channel", "", pattern=KEY_PATTERN)))


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
    return _finish(r, HowToBuySettings(channel=r.str_("channel", "", pattern=KEY_PATTERN)))


def _parse_faq(r: _Reader) -> FaqSettings:
    return _finish(r, FaqSettings(channel=r.str_("channel", "", pattern=KEY_PATTERN)))


def _parse_shipping(r: _Reader) -> ShippingSettings:
    return _finish(r, ShippingSettings(channel=r.str_("channel", "", pattern=KEY_PATTERN)))


def _parse_announcements(r: _Reader) -> AnnouncementSettings:
    return _finish(r, AnnouncementSettings(channel=r.str_("channel", "", pattern=KEY_PATTERN)))


def _parse_problems(r: _Reader) -> ProblemSettings:
    return _finish(r, ProblemSettings())


def _parse_my_orders(r: _Reader) -> MyOrdersSettings:
    return _finish(r, MyOrdersSettings())


def _parse_operations(r: _Reader) -> OperationsSettings:
    return _finish(r, OperationsSettings())
