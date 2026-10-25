"""Operator wording for publishing the guides (shown only in the admin-only /setup report)."""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    "guides.publish.done": (
        "`{feature}`: {messages} message(s) in {channel} ({created} posted, {updated} updated, "
        "{unchanged} up to date, {removed} old message(s) removed)"
    ),
    "guides.publish.no_guild": "`{feature}`: the configured server is not available.",
    "guides.publish.channel_missing": (
        "`{feature}`: not posted because channel `{channel}` does not exist (check the channels section above)"
    ),
    "guides.publish.link_channel_missing": (
        "`{feature}`: not posted because the link button to channel `{channel}` would be broken: the channel "
        "does not exist (check the channels section above)"
    ),
    "guides.publish.text_channel_missing": (
        "`{feature}`: not posted because content key `{content_key}` links to channel `{channel}`, which does "
        "not exist (check the channels section above)"
    ),
    "guides.publish.missing_content": ("`{feature}`: not posted because content key `{content_key}` is not configured"),
    "guides.publish.missing_permissions": ("`{feature}`: not posted because the bot lacks {permissions} in {channel}"),
    "guides.publish.limits": ("`{feature}`: not posted because message {index} exceeds Discord limits: {violations}"),
    "guides.publish.failed": (
        "`{feature}`: posting failed (HTTP {http_status}, Discord code {code}); run /setup again to retry"
    ),
    "guides.publish.empty": "`{feature}`: nothing to post (no text or sections are configured)",
    "guides.publish.not_ready": (
        "`{feature}`: not posted because the feature is not ready (disabled or not fully configured; run /status)"
    ),
}
