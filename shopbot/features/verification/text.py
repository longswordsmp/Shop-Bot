"""Operator wording of the verification feature: /setup report lines and the Discord audit-log reason.

Shown only to admins (the /setup report) or in Discord's audit log, never to customers. Each entry can be
overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # /setup report: the welcome panel
    "verification.publish.created": "Verification: welcome panel posted in {channel}",
    "verification.publish.updated": "Verification: welcome panel updated in {channel}",
    "verification.publish.unchanged": "Verification: welcome panel up to date in {channel}",
    "verification.publish.recreated": (
        "Verification: welcome panel re-posted in {channel} (the previous message was deleted)"
    ),
    "verification.publish.moved": "Verification: welcome panel moved to {channel}",
    "verification.publish.no_guild": "Verification: the configured server is not available.",
    "verification.publish.channel_missing": (
        "Verification: welcome panel not posted because channel `{channel}` does not exist (check /setup above)"
    ),
    "verification.publish.missing_content": (
        "Verification: welcome panel not posted because content key `{content_key}` is not configured"
    ),
    "verification.publish.missing_permissions": (
        "Verification: welcome panel not posted because the bot lacks {permissions} in {channel}"
    ),
    "verification.publish.limits": (
        "Verification: welcome panel not posted because it exceeds Discord limits: {violations}"
    ),
    "verification.publish.failed": (
        "Verification: posting the welcome panel failed (HTTP {http_status}, Discord code {code})"
    ),
    "verification.publish.unresolved_links": (
        "Verification: the channel links {placeholders} show as empty because those channels do not exist yet"
    ),
    # /setup report: can the button grant the Member role?
    "verification.role.missing": (
        "Verification: the Member role `{role}` does not exist yet, so the 'I agree' button cannot grant it "
        "(check the roles section above)"
    ),
    "verification.role.no_manage_roles": (
        "Verification: the bot lacks the Manage Roles permission, so the 'I agree' button cannot grant {role}"
    ),
    "verification.role.not_assignable": (
        "Verification: the bot cannot grant {role} because that role is not below the bot's highest role (or is "
        "managed by an integration). Move the bot's role above it in Server Settings → Roles."
    ),
    # Discord audit-log reason when the role is granted
    "verification.audit_reason": "Accepted the server rules (verification button)",
}
