"""Embed design system.

``Ui`` turns content keys into ``EmbedSpec``/``ButtonSpec`` objects with consistent branding: tone
colors, optional author line and footer (from content), logo/banner imagery (from settings), timestamps,
page and progress indicators. Every visible word comes from the content store; when required text is
missing these helpers return ``None`` so callers can refuse to show an incomplete message.

Operator embeds (``operator_embed``) are for admin-only replies and the audit channel; their wording
comes from operator text, never from customer content.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from shopbot.config.content import ContentStore
from shopbot.config.settings import Settings
from shopbot.core.clock import Clock
from shopbot.core.money import currency_exponent, format_amount
from shopbot.features import core_keys as ck
from shopbot.ui.spec import ButtonSpec, ButtonStyle, EmbedField, EmbedSpec, SelectOptionSpec

_OPERATOR_COLORS = {
    "primary": 0x5865F2,
    "info": 0x5865F2,
    "success": 0x57F287,
    "warning": 0xFEE75C,
    "danger": 0xED4245,
    "neutral": 0x99AAB5,
}


class Ui:
    def __init__(self, settings: Callable[[], Settings], content: ContentStore, clock: Clock) -> None:
        self._settings = settings
        self.content = content
        self.clock = clock

    # -- text -------------------------------------------------------------------------------------

    def text(self, key: str, **values: str | int) -> str | None:
        return self.content.render(key, **values)

    def money(self, minor: int) -> str | None:
        """Format an amount with the configured money format; None if the format is not configured."""
        settings = self._settings().checkout
        if not settings.currency:
            return None
        exponent = currency_exponent(settings.currency, settings.currency_exponent)
        amount = format_amount(minor, exponent, settings.decimal_separator, settings.thousands_separator)
        return self.content.render(ck.MONEY_FORMAT, amount=amount, currency=settings.currency)

    def progress(self, current: int, total: int) -> str | None:
        branding = self._settings().branding
        bar = branding.progress_filled * current + branding.progress_empty * max(0, total - current)
        return self.content.render(ck.PROGRESS_FORMAT, current=current, total=total, bar=bar)

    def page_indicator(self, page: int, pages: int) -> str | None:
        return self.content.render(ck.PAGE_INDICATOR, page=page, pages=pages)

    # -- embeds -----------------------------------------------------------------------------------

    def color(self, tone: str) -> int | None:
        return self._settings().branding.colors.get(tone)

    def embed(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        tone: str = "primary",
        fields: Sequence[EmbedField] = (),
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        url: str | None = None,
        panel: bool = False,
        timestamp: bool = False,
        footer_extra: str | None = None,
    ) -> EmbedSpec:
        """Build a branded customer-facing embed from already-resolved text."""
        branding = self._settings().branding
        footer_parts = [p for p in (self.content.get(ck.FOOTER_TEXT), footer_extra) if p]
        author = self.content.get(ck.AUTHOR_NAME)
        if panel:
            image_url = image_url or (branding.banner_url if branding.panel_banner and branding.banner_url else None)
            if thumbnail_url is None and branding.panel_thumbnail and branding.logo_url:
                thumbnail_url = branding.logo_url
        return EmbedSpec(
            title=title,
            description=description,
            url=url,
            color=self.color(tone),
            fields=tuple(fields),
            author_name=author,
            author_icon_url=(branding.logo_url or None) if author else None,
            footer_text="\n".join(footer_parts) or None,
            footer_icon_url=(branding.footer_icon_url or None) if footer_parts else None,
            thumbnail_url=thumbnail_url or None,
            image_url=image_url or None,
            timestamp=self.clock.now() if (timestamp and branding.show_timestamps) else None,
        )

    def content_embed(
        self,
        title_key: str | None,
        description_key: str | None = None,
        *,
        values: dict[str, str | int] | None = None,
        require: Sequence[str] = (),
        **kwargs: object,
    ) -> EmbedSpec | None:
        """Embed whose title/description come from content keys.

        Returns None if any key in ``require`` (default: the given title and description keys) is not
        configured, so incomplete messages are never sent.
        """
        values = values or {}
        required = list(require) or [k for k in (title_key, description_key) if k]
        if not all(self.content.has(k) for k in required):
            return None
        title = self.content.render(title_key, **values) if title_key else None
        description = self.content.render(description_key, **values) if description_key else None
        if not title and not description and not kwargs.get("fields"):
            return None
        return self.embed(title=title, description=description, **kwargs)  # type: ignore[arg-type]

    def error_embed(self, key: str, **values: str | int) -> EmbedSpec | None:
        description = self.content.render(key, **values)
        if description is None:
            return None
        return self.embed(title=self.content.get(ck.ERROR_TITLE), description=description, tone="danger")

    def field(
        self, name_key: str, value: str | None, *, inline: bool = False, values: dict[str, str | int] | None = None
    ) -> EmbedField | None:
        name = self.content.render(name_key, **(values or {}))
        if name is None or not value:
            return None
        return EmbedField(name=name, value=value, inline=inline)

    # -- components -------------------------------------------------------------------------------

    def button(
        self,
        label_key: str | None,
        custom_id: str,
        *,
        style: ButtonStyle = "secondary",
        emoji_key: str | None = None,
        disabled: bool = False,
        values: dict[str, str | int] | None = None,
    ) -> ButtonSpec | None:
        label = self.content.render(label_key, **(values or {})) if label_key else None
        emoji = self.content.get(emoji_key) if emoji_key else None
        if not label and not emoji:
            return None
        return ButtonSpec(label=label, custom_id=custom_id, style=style, emoji=emoji, disabled=disabled)

    def link_button(self, label_key: str, url_key: str, *, emoji_key: str | None = None) -> ButtonSpec | None:
        label = self.content.get(label_key)
        url = self.content.get(url_key)
        if not label or not url:
            return None
        emoji = self.content.get(emoji_key) if emoji_key else None
        return ButtonSpec(label=label, style="link", url=url, emoji=emoji)

    def option(
        self,
        label: str | None,
        value: str,
        *,
        description: str | None = None,
        emoji: str | None = None,
        default: bool = False,
    ) -> SelectOptionSpec | None:
        if not label:
            return None
        return SelectOptionSpec(label=label, value=value, description=description, emoji=emoji, default=default)

    # -- operator (admin-only) --------------------------------------------------------------------

    def operator_embed(
        self, title: str, description: str | None = None, *, tone: str = "info", fields: Sequence[EmbedField] = ()
    ) -> EmbedSpec:
        color = self.color(tone) or _OPERATOR_COLORS.get(tone)
        return EmbedSpec(
            title=title, description=description, color=color, fields=tuple(fields), timestamp=self.clock.now()
        )


def chunk_lines(lines: Sequence[str], limit: int) -> list[str]:
    """Group lines into blocks no longer than ``limit`` characters (a single over-long line stays alone).

    Used for data-driven lists (cart lines, product listings) so they fit embed field limits without
    cutting any text.
    """
    blocks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            blocks.append(current)
        current = line
    if current:
        blocks.append(current)
    return blocks
