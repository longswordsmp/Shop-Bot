"""Operator wording for info panel publishing (shown only in the admin-only /setup report)."""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    "info_panels.publish.created": "Info panel `{panel}`: posted in {channel}",
    "info_panels.publish.updated": "Info panel `{panel}`: updated in {channel}",
    "info_panels.publish.unchanged": "Info panel `{panel}`: up to date in {channel}",
    "info_panels.publish.recreated": "Info panel `{panel}`: re-posted in {channel} (the previous message was deleted)",
    "info_panels.publish.moved": "Info panel `{panel}`: moved to {channel}",
    "info_panels.publish.removed": "Info panel `{panel}`: removed (no longer configured)",
    "info_panels.publish.none_configured": "Info panels: none are configured in settings ([[info_panels]]).",
    "info_panels.publish.no_guild": "Info panels: the configured server is not available.",
    "info_panels.publish.channel_missing": (
        "Info panel `{panel}`: not posted because channel `{channel}` does not exist (check /setup above)"
    ),
    "info_panels.publish.missing_content": (
        "Info panel `{panel}`: not posted because content key `{content_key}` is not configured"
    ),
    "info_panels.publish.missing_permissions": (
        "Info panel `{panel}`: not posted because the bot lacks {permissions} in {channel}"
    ),
    "info_panels.publish.limits": "Info panel `{panel}`: not posted because it exceeds Discord limits: {violations}",
    "info_panels.publish.failed": "Info panel `{panel}`: posting failed (HTTP {http_status}, Discord code {code})",
}
