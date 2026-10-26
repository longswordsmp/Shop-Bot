"""Operator text of the operations feature: /setup report lines (shown only to admins). Staff alerts, reminders and
the Owner's alerts are business messages (content keys). Each entry can be overridden in content.toml under
``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    "operations.publish.alerts": "Staff alerts and reminders are posted in {channel}.",
    "operations.publish.customer_role_restored": (
        "Customer role: given back to {count} customers whose role was deleted by /reset."
    ),
    "operations.publish.no_guild": "Staff alerts: the configured server is not available.",
    "operations.publish.staff_channel_missing": (
        "operations.staff_channel: channel '{name}' does not exist yet (run /setup); staff alerts cannot be posted."
    ),
    "operations.publish.staff_channel_permissions": (
        "operations.staff_channel: the bot lacks {permissions} in {channel}."
    ),
    "operations.publish.ping_not_mentionable": (
        "operations.ping_roles: {roles} cannot be pinged (make the role mentionable, or give the bot 'Mention "
        "@everyone, @here and All Roles'); alerts show the mention without notifying anyone."
    ),
    "operations.publish.ping_role_missing": "operations.ping_roles: role '{name}' does not exist yet (run /setup).",
    "operations.publish.owner_unknown": (
        "operations.owner_dm_alerts is on, but OWNER_ID is not set: the Owner receives no private alerts."
    ),
    "operations.publish.customer_role_missing": (
        "checkout.customer_role: role '{name}' does not exist yet (run /setup)."
    ),
    "operations.publish.customer_role_hierarchy": (
        "checkout.customer_role: the bot can only give {role} when its own highest role is above it (and it has "
        "Manage Roles)."
    ),
}
