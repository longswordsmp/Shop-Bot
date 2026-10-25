"""Operator wording for /setup (admin-only ephemeral replies and the Discord audit-log reason).

Every entry can be overridden in content.toml under ``operator.<key>``. Customers never see this text.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # report frame
    "server.setup.title": "Server setup report",
    "server.setup.title_dry_run": "Server setup plan (dry run: nothing was changed)",
    "server.setup.title_continued": "Server setup report (continued)",
    "server.setup.summary": (
        "Created: {created} · Adopted: {adopted} · Updated: {updated} · Up to date: {unchanged} · "
        "Skipped: {skipped} · Failed: {failed}"
    ),
    "server.setup.summary_dry_run": (
        "Would create: {created} · Would adopt: {adopted} · Would update: {updated} · Up to date: {unchanged} · "
        "Would skip: {skipped}"
    ),
    "server.setup.section.roles": "Roles",
    "server.setup.section.categories": "Categories",
    "server.setup.section.channels": "Channels",
    "server.setup.section.panels": "Panels",
    "server.setup.section.problems": "Problems",
    "server.setup.none": "Nothing to report.",
    # command-level outcomes
    "server.setup.busy": "/setup is already running. Wait for it to finish, then run it again if needed.",
    "server.setup.no_guild": "The configured server is not available to the bot (check GUILD_ID and the invite).",
    "server.setup.reply_failed": "The setup finished but the report could not be delivered. Check the bot logs.",
    "server.setup.audit_reason": "Server setup (/setup) requested by user {user_id}",
    "server.setup.missing_permissions": (
        "The bot is missing server permissions: {permissions}. Grant them to the bot's role and run /setup again."
    ),
    # targets and kinds
    "server.setup.target_name": '"{name}"',
    "server.setup.kind.role": "Role",
    "server.setup.kind.category": "Category",
    "server.setup.kind.channel": "Channel",
    # per-resource outcomes (shown in the resource's own section)
    "server.setup.created": "`{item}`: created {target}",
    "server.setup.would_create": "`{item}`: would create {target}",
    "server.setup.recreated": "`{item}`: re-created {target} (the previous one was deleted)",
    "server.setup.would_recreate": "`{item}`: would re-create {target} (the previous one was deleted)",
    "server.setup.adopted": "`{item}`: adopted existing {target}",
    "server.setup.would_adopt": "`{item}`: would adopt existing {target}",
    "server.setup.updated": "`{item}`: updated {target} ({changes})",
    "server.setup.would_update": "`{item}`: would update {target} ({changes})",
    "server.setup.unchanged": "`{item}`: up to date {target}",
    # per-resource problems (shown in the problems section)
    "server.setup.name_missing": "{kind} `{item}`: not created because content key `{content_key}` is not configured",
    "server.setup.id_not_found": "{kind} `{item}`: id {id} from settings does not match any {kind_lower} in the server",
    "server.setup.role_not_manageable": (
        "{kind} `{item}`: {target} needs changes ({changes}) but it is managed by an integration, is @everyone, "
        "or is not below the bot's highest role"
    ),
    "server.setup.category_unavailable": (
        "{kind} `{item}`: skipped because its category `{category}` is not available"
    ),
    "server.setup.failed": "{kind} `{item}`: {action} failed (HTTP {http_status}, Discord code {code})",
    "server.setup.action.create": "creating",
    "server.setup.action.update": "updating",
    # change names
    "server.setup.change.name": "name",
    "server.setup.change.permissions": "permissions",
    "server.setup.change.color": "color",
    "server.setup.change.hoist": "hoist",
    "server.setup.change.mentionable": "mentionable",
    "server.setup.change.topic": "topic",
    "server.setup.change.slowmode": "slowmode",
    "server.setup.change.category": "category",
    "server.setup.change.overwrites": "permission overwrites",
    "server.setup.change.sync": "permissions synced with category",
    # panels
    "server.setup.publish_skipped": "Panels were not published (publish: False).",
    "server.setup.panel_withdrawn": "Withdrew panel `{key}`: feature `{feature}` is not ready",
    "server.setup.panel_would_withdraw": "Would withdraw panel `{key}` (feature `{feature}` is not ready)",
    "server.setup.panel_withdraw_failed": "Could not withdraw panel `{key}` of feature `{feature}`",
    "server.setup.publish_skipped_dry_run": "Panels are not published during a dry run.",
    "server.setup.publish_failed": (
        "Feature `{feature}`: publishing failed (reference {error_id}; details in the bot logs)"
    ),
    "server.setup.no_panels": "No ready feature has panels to publish.",
    "server.setup.feature_not_ready": "Feature `{feature}`: enabled but not ready ({count} problems)",
    "server.setup.status_hint": "Run /status to see what is missing for features that are not ready.",
}
