"""Operator text of the new-arrivals feature: /setup report lines and the Discord audit-log reason for role changes.

Shown only to administrators (the admin-only /setup report and Discord's server audit log), never to customers.
Each entry can be overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    "new_arrivals.publish.created": "New arrivals panel: posted in {channel}",
    "new_arrivals.publish.updated": "New arrivals panel: updated in {channel}",
    "new_arrivals.publish.unchanged": "New arrivals panel: up to date in {channel}",
    "new_arrivals.publish.recreated": "New arrivals panel: re-posted in {channel} (the previous message was deleted)",
    "new_arrivals.publish.moved": "New arrivals panel: moved to {channel}",
    "new_arrivals.publish.no_guild": "New arrivals panel: the configured server is not available.",
    "new_arrivals.publish.channel_missing": (
        "New arrivals panel: not posted because channel `{channel}` does not exist (check /setup above)"
    ),
    "new_arrivals.publish.missing_content": (
        "New arrivals panel: not posted because content key `{content_key}` is not configured"
    ),
    "new_arrivals.publish.missing_permissions": (
        "New arrivals panel: not posted because the bot lacks {permissions} in {channel}"
    ),
    "new_arrivals.publish.limits": "New arrivals panel: not posted because it exceeds Discord limits: {violations}",
    "new_arrivals.publish.failed": "New arrivals panel: posting failed (HTTP {http_status}, Discord code {code})",
    "new_arrivals.publish.role_missing": (
        "New arrivals: the notification role `{role}` does not exist, so the panel button answers with the generic "
        "error until /setup can create it (check the roles section above)"
    ),
    "new_arrivals.publish.role_not_manageable": (
        "New arrivals: the bot cannot give or remove {role} (it is above the bot's own role or managed by an "
        "integration); move the bot's role above it in Server Settings → Roles"
    ),
    # Reason recorded in Discord's server audit log when the button adds or removes the role.
    "new_arrivals.role_reason": "New-arrivals notification button",
}
