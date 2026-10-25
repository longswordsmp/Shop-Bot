"""Operator text: wording for admin-only command replies, setup reports, and the audit channel.

This text is technical tooling output shown only to authorized administrators (ephemeral replies to
admin-gated commands, and the admin-only audit channel). It is never shown to customers. Every entry can
be overridden in content.toml under ``operator.<key>``. Feature packages contribute their own entries
through ``FeatureModule.operator_text``.
"""

from __future__ import annotations

import re

CORE_OPERATOR_TEXT: dict[str, str] = {
    "audit.actor": "Actor",
    "audit.target": "Target",
    "audit.details": "Details",
    "command.failed": "The command failed. Reference: {error_id}. Details are in the bot logs.",
    "command.not_in_guild": "This command can only be used in the configured server.",
    "command.done": "Done.",
    "command.not_found": "No matching record was found.",
    "command.invalid": "Invalid input: {reason}",
    "command.feature_disabled": "The '{feature}' feature is not enabled or not fully configured. Run /status.",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def render_operator(template: str, values: dict[str, object]) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER_RE.sub(substitute, template)
