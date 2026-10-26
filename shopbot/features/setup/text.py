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
    "server.setup.action.assign": "assigning the role",
    # owner role
    "server.setup.owner_role_assigned": "`{item}`: gave {target} to the owner (<@{user_id}>)",
    "server.setup.owner_role_would_assign": "`{item}`: would give {target} to the owner (<@{user_id}>)",
    "server.setup.owner_role_present": "`{item}`: the owner (<@{user_id}>) already has {target}",
    "server.setup.owner_not_member": "{kind} `{item}`: OWNER_ID {user_id} is not a member of this server",
    # entry-channel safety: channels /setup does not manage that everyone can see (problems section)
    "server.setup.public_unmanaged": (
        '{kind} {target} ("{name}"): not managed by /setup, and `@everyone` can see it, including members who have '
        "not accepted the rules"
    ),
    "server.setup.public_unmanaged_more": "… and {count} more unmanaged channels that `@everyone` can see",
    "server.setup.public_unmanaged_hint": (
        "Unverified members must see only the configured entry channels. Delete the channels above (for example "
        "Discord's default #general text channel and General voice channel) or deny View Channel to `@everyone` on "
        "them. /setup never changes or deletes channels it does not manage."
    ),
    "server.setup.channel_kind.text": "Text channel",
    "server.setup.channel_kind.voice": "Voice channel",
    "server.setup.channel_kind.stage": "Stage channel",
    "server.setup.channel_kind.forum": "Forum channel",
    "server.setup.channel_kind.category": "Category",
    "server.setup.channel_kind.other": "Channel",
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
    # /reset (owner only)
    "server.reset.preview_title": "Reset the server",
    "server.reset.preview_description": (
        "This deletes the server's channels, categories and roles listed below, so /setup can build everything "
        "new. Deleted channels and their messages cannot be brought back.\n\n"
        "Before anything is deleted, a database backup is made. {data}\n\n"
        "The progress and the result are sent to you by DM, because this channel is deleted too. A private channel "
        "is then created for you to run /setup in.\n\n"
        "To go ahead, press **Continue** and type `{phrase}`."
    ),
    "server.reset.data_kept": "Orders, products, reviews and store credit are kept.",
    "server.reset.data_wiped": (
        "**The shop data is deleted too:** every order, product, review, ticket, store credit balance and customer "
        "record (they stay in the backup)."
    ),
    "server.reset.field.channels": "Channels to delete ({count})",
    "server.reset.field.categories": "Categories to delete ({count})",
    "server.reset.field.roles": "Roles to delete ({count})",
    "server.reset.field.kept": "Kept",
    "server.reset.field.work": "Orders and tickets",
    "server.reset.field.failed": "Not deleted ({count})",
    "server.reset.none": "None",
    "server.reset.more": "… and {count} more",
    "server.reset.kept.everyone": "`@everyone`",
    "server.reset.kept.managed": "Roles of bots, integrations and server boosts: {names}",
    "server.reset.kept.above_bot": "Roles the bot may not delete (not below its highest role): {names}",
    "server.reset.kept.community": "Required by Discord for a Community server: {names}",
    "server.reset.kept.no_permission": "The bot lacks Manage Channels there: {names}",
    "server.reset.work_kept": (
        "Orders in progress: {orders}. Open tickets: {tickets}. Their channels are deleted: tickets are closed "
        "without a transcript (close them first to keep one), orders keep their status, customers keep getting "
        "updates by DM, and /setup posts the staff cards of open "
        "orders again in the new orders channel. Members lose the deleted roles and verify again on the new "
        "welcome panel; /setup gives customers the Customer role back."
    ),
    "server.reset.work_wiped": (
        "Orders in progress: {orders}. Open tickets: {tickets}. They are deleted with the shop data. Members lose "
        "the deleted roles and verify again on the new welcome panel."
    ),
    "server.reset.button.continue": "Continue",
    "server.reset.button.cancel": "Cancel",
    "server.reset.form_title": "Confirm the server reset",
    "server.reset.form_title_wipe": "Confirm the reset and the data wipe",
    "server.reset.form_label": "Type RESET and the server name",
    "server.reset.owner_only": "Only the bot owner (OWNER_ID) or the server owner can reset the server.",
    "server.reset.no_guild": "The configured server is not available to the bot (check GUILD_ID and the invite).",
    "server.reset.busy": "/setup or /reset is already running. Wait for it to finish, then try again.",
    "server.reset.nothing": "There is nothing the bot can delete in this server.",
    "server.reset.cancelled": "Reset cancelled. Nothing was changed.",
    "server.reset.mismatch": "The confirmation did not match `{phrase}`. Nothing was changed.",
    "server.reset.changed": (
        "The server changed since the preview. Nothing was changed: run /reset again to see what it would delete now."
    ),
    "server.reset.dm_closed": (
        "I could not send you a DM, and the result of the reset is sent there. Allow direct messages from members of "
        "this server (the server's Privacy Settings) and run /reset again. Nothing was changed."
    ),
    "server.reset.backup_failed": "The backup could not be made ({error}). Nothing was changed.",
    "server.reset.started": "The reset has started. The progress and the result are in your DMs.",
    "server.reset.progress_title": "Resetting the server",
    "server.reset.progress_description": (
        "Deleted {done} of {total} · not deleted: {failed}\nThe result is sent here when it is done."
    ),
    "server.reset.result_title": "The server was reset",
    "server.reset.result_title_problems": "The server was reset, with problems",
    "server.reset.result_deleted": "Deleted: {channels} channels, {categories} categories and {roles} roles.",
    "server.reset.result_backup": "Backup made first: `{backup}` (in the `backups` folder next to the database).",
    "server.reset.result_data_kept": "Orders, products, reviews and store credit were kept.",
    "server.reset.result_data_wiped": (
        "The shop data was deleted: {orders} orders, {products} products and {reviews} reviews (and everything that "
        "belonged to them)."
    ),
    "server.reset.result_next": (
        "**Next:** run **/setup** in {channel} to build the server and post every panel. You can delete that channel "
        "afterwards."
    ),
    "server.reset.result_next_no_channel": (
        "**Next:** the private channel for /setup could not be created. Create any channel, run **/setup** there, "
        "and delete it afterwards."
    ),
    "server.reset.result_kept": "{count} channels and roles were kept (as listed in the preview).",
    "server.reset.failure": "{kind} {name}: HTTP {status} (Discord code {code})",
    "server.reset.kind.channel": "Channel",
    "server.reset.kind.category": "Category",
    "server.reset.kind.role": "Role",
    "server.reset.audit_reason": "Server reset (/reset) requested by user {user_id}",
    "server.reset.setup_channel_name": "🛠・setup",
    "server.reset.setup_channel_topic": "Run /setup here to build the server. Delete this channel afterwards.",
}
