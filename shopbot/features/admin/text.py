"""Operator text for the admin tools.

Everything the admin feature shows is admin-only tooling output (ephemeral replies to administrator-gated
commands and admin-only buttons), so it uses operator text, never customer content. Each entry can be
overridden in content.toml under ``operator.<key>``.
"""

from __future__ import annotations

OPERATOR_TEXT: dict[str, str] = {
    # -- shared --------------------------------------------------------------------------------------
    "admin.yes": "yes",
    "admin.no": "no",
    "admin.none": "none",
    "admin.page.previous": "Previous",
    "admin.page.next": "Next",
    "admin.page.indicator": "Page {page} of {pages}",
    "admin.list.more": "… and {count} more",
    "admin.list.empty": "Nothing to show.",
    "admin.section.continued": "{title} (continued)",
    "admin.unknown_feature": "Unknown feature `{feature}`.",
    "admin.known_features": "Known features: {features}",
    "admin.section.feature": "Feature: {feature}",
    "admin.no_guild": "The bot cannot see the configured server (GUILD_ID). Invite the bot or check the ID.",
    "admin.state.ready": "ready",
    "admin.state.not_ready": "not ready ({problems} problems)",
    "admin.state.disabled": "disabled",
    "admin.problem.keyed": "`{content_key}` — {message}",
    "admin.problem.keyed_invalid": "`{content_key}` — invalid: {message}",
    "admin.problem.unkeyed": "{kind}: {message}",
    "admin.problem.kind.missing_content": "Missing content",
    "admin.problem.kind.invalid_content": "Content rule",
    "admin.problem.kind.settings": "Settings",
    "admin.problem.kind.dependency": "Dependency",
    # -- /status -------------------------------------------------------------------------------------
    "admin.status.title": "Bot status",
    "admin.status.summary": "{ready} of {enabled} enabled features are ready.",
    "admin.status.features": "Features",
    "admin.status.feature_ready": "✅ `{feature}` — ready",
    "admin.status.feature_not_ready": "⚠️ `{feature}` — enabled, not ready ({problems} problems)",
    "admin.status.feature_disabled": "▫️ `{feature}` — disabled ({problems} problems)",
    "admin.status.unknown_keys": "Unknown content keys (likely typos)",
    "admin.status.pictures": "Banner and icon pictures that cannot be used (replace the file, then /reload)",
    "admin.status.permissions": "Bot permissions (server-wide)",
    "admin.status.permissions_ok": "All permissions needed by the enabled features are granted.",
    "admin.status.permissions_missing": "Missing for {capability}: {permissions}",
    "admin.status.intents": "Gateway intents (settings)",
    "admin.status.intents_value": "Message content: {message_content} · Server members: {members}",
    "admin.status.intent_warning": "Ticket/order transcripts need the message content intent, which is disabled.",
    "admin.status.tasks": "Scheduled tasks",
    "admin.status.tasks_value": "Pending: {pending} · Running: {running} · Failed: {failed}",
    "admin.status.database": "Database",
    "admin.status.hint": (
        "Use `/status feature:<name>` for a feature's problems and `/content missing` for missing text."
    ),
    "admin.status.feature_title": "Status: {feature}",
    "admin.status.feature_state": "Enabled: {enabled} · Ready: {ready} · Problems: {problems}",
    "admin.status.problems": "Problems",
    "admin.status.no_problems": "No problems.",
    # -- /content ------------------------------------------------------------------------------------
    "admin.content.unknown_key": "Unknown content key `{content_key}`.",
    "admin.content.did_you_mean": "Did you mean: {suggestions}",
    "admin.content.modal_title": "Edit content",
    "admin.content.input_label": "Value",
    "admin.content.input_description": "Key: {content_key}",
    "admin.content.input_too_long": "The current value is too long to prefill; see /content show.",
    "admin.content.expired": "This form is no longer valid (the bot restarted or it expired). Run /content set again.",
    "admin.content.invalid_title": "Not saved: {content_key}",
    "admin.content.problems": "Problems",
    "admin.content.problem_line": "- {problem}",
    "admin.content.problem_empty": "the value is empty (use /content clear to remove an override)",
    "admin.content.problem_encoding": "the value contains characters that cannot be stored",
    "admin.content.submitted_value": "The value you submitted is shown above so you can copy and fix it.",
    "admin.content.saved_title": "Saved: {content_key}",
    "admin.content.value_attached": (
        "The value is attached as a file because it cannot be shown exactly in a code block."
    ),
    "admin.content.readiness": "Readiness",
    "admin.content.readiness_change": "`{feature}`: {before} → {after}",
    "admin.content.readiness_same": "`{feature}`: {state} (unchanged)",
    "admin.content.show_title": "Content: {content_key}",
    "admin.content.unset_value": "This key is not configured.",
    "admin.content.field.source": "Source",
    "admin.content.field.kind": "Kind",
    "admin.content.field.max_length": "Max length",
    "admin.content.field.required": "Required",
    "admin.content.field.placeholders": "Placeholders",
    "admin.content.field.fallback": "Fallback",
    "admin.content.field.feature": "Feature / group",
    "admin.content.field.doc": "Where it appears",
    "admin.content.field.problems": "Problems with the current value",
    "admin.content.source.override": "override (/content set)",
    "admin.content.source.file": "content.toml",
    "admin.content.source.unset": "not configured",
    "admin.content.source.fallback": "not configured; uses the fallback `{fallback}`",
    "admin.content.feature_group": "`{feature}` / {group}",
    "admin.content.characters": "{count} characters",
    "admin.content.cleared_title": "Override removed: {content_key}",
    "admin.content.cleared_now": "The value now comes from: {source}",
    "admin.content.no_override": "There is no /content set override for `{content_key}`; nothing changed.",
    "admin.content.missing_title": "Missing or invalid content",
    "admin.content.missing_scope_all": "Enabled features. Fix these, then run /setup to publish panels.",
    "admin.content.missing_scope_feature": "Feature `{feature}`.",
    "admin.content.missing_none": "All required content is configured and valid.",
    "admin.content.keys_title": "Content keys",
    "admin.content.keys_summary": "{total} keys · {configured} configured · {missing} required and missing",
    "admin.content.key_line": "{icon} `{content_key}` — {kind}, {requirement}, {source}",
    "admin.content.key_icon.ok": "✅",
    "admin.content.key_icon.missing": "❌",
    "admin.content.key_icon.invalid": "⚠️",
    "admin.content.key_icon.optional": "▫️",
    "admin.content.requirement.required": "required",
    "admin.content.requirement.optional": "optional",
    "admin.content.export_title": "Content export",
    "admin.content.export_summary": (
        "{total} keys, {configured} configured ({overrides} set with /content set). "
        "Unset keys are written as empty strings."
    ),
    "admin.content.export_failed": "The export could not be created",
    "admin.content.panels": "Posted panels",
    "admin.content.panels_none": "No posted panel needed an update (run /setup to post the panels).",
    "admin.content.panels_failed": (
        "{count} panel(s) could not be updated (reference {error_id}); run /setup to post them again."
    ),
    "admin.content.panels_hint": "Other messages show the new text the next time the bot sends or updates them.",
    # -- Edit text (message command) -----------------------------------------------------------------
    "admin.edit.title": "Edit text in this message",
    "admin.edit.description": (
        "Pick the text to change. A form opens with its current wording; when you save, the posted panels are "
        "updated. Product names, descriptions and prices are changed with /product edit."
    ),
    "admin.edit.placeholder": "Choose the text to edit",
    "admin.edit.more": "{count} more texts are in this message: use /content set and type a few words of the text.",
    "admin.edit.not_bot": "Only messages sent by this bot can be edited here.",
    "admin.edit.none_title": "No editable text found",
    "admin.edit.none": (
        "This message shows no configured text (e.g. it only shows products or what staff typed). To change any "
        "text, use /content set and type a few words of it."
    ),
    # -- /reload -------------------------------------------------------------------------------------
    "admin.reload.ok_title": "Configuration reloaded",
    "admin.reload.failed_title": "Reload failed; the previous configuration is still active",
    "admin.reload.errors": "Errors",
    "admin.reload.hint": "Run /setup to create missing channels and republish panels with the new configuration.",
    # -- /backup -------------------------------------------------------------------------------------
    "admin.backup.title": "Backup created",
    "admin.backup.details": (
        "File: `{path}`\nSize: {size} bytes\nBackups kept: {kept} · Older backups removed: {removed}"
    ),
    "admin.backup.private": "The backup stays on the server's disk; it is never uploaded to Discord.",
    "admin.backup.failed": "Backup failed",
    "admin.backup.unavailable": "Backups need a database file (DB_PATH); an in-memory database cannot be backed up.",
    "admin.backup.busy": "A backup is already running. Try again in a moment.",
    # -- /audit --------------------------------------------------------------------------------------
    "admin.audit.title": "Audit log",
    "admin.audit.events": "Events (newest first)",
    "admin.audit.empty": "No matching audit events.",
    "admin.audit.line": "`#{id}` <t:{ts}:f> **{action}** · actor: {actor} · target: {target}",
    "admin.audit.details": "details: {details}",
    "admin.audit.system": "system",
    # -- /tasks --------------------------------------------------------------------------------------
    "admin.tasks.title": "Scheduled tasks",
    "admin.tasks.list": "Tasks",
    "admin.tasks.empty": "No matching tasks.",
    "admin.tasks.line": "`#{id}` **{kind}** · {status} · run at <t:{run_at}:f> · attempts {attempts}/{max_attempts}",
    "admin.tasks.error": "last error: {error}",
}
