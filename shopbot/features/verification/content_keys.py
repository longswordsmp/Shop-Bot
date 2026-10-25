"""Customer-facing content keys of the verification feature (#welcome rules panel) and its settings checks.

One pair of rule keys (``verification.rule.<n>.name`` / ``.value``) exists per configured rule
(``verification.rules``). Text shown in descriptions and rule texts may contain ``{channel_<key>}`` placeholders
(clickable channel links, see ``server_keys.channel_placeholders``); titles and field headings cannot show links
(Discord does not render mentions there), so they do not offer them.
"""

from __future__ import annotations

from shopbot.config.content_keys import ContentKey, Kind, ck
from shopbot.config.settings import Settings
from shopbot.features.server_keys import channel_placeholders, effective_access, reference_problems, staff_role_lists

VERIFICATION = "verification"

# -- welcome panel ------------------------------------------------------------------------------------
WELCOME_TITLE = "verification.welcome.title"
WELCOME_DESCRIPTION = "verification.welcome.description"
WELCOME_IMAGE = "verification.welcome.image_url"
WELCOME_THUMBNAIL = "verification.welcome.thumbnail_url"
RULES_TITLE = "verification.rules.title"
RULES_DESCRIPTION = "verification.rules.description"
BUTTON_LABEL = "verification.button.label"
BUTTON_EMOJI = "verification.button.emoji"

# -- private replies and the optional direct message ---------------------------------------------------
CONFIRMED_TITLE = "verification.confirmed.title"
CONFIRMED_DESCRIPTION = "verification.confirmed.description"
ALREADY_TITLE = "verification.already_verified.title"
ALREADY_DESCRIPTION = "verification.already_verified.description"
DM_TITLE = "verification.dm.title"
DM_DESCRIPTION = "verification.dm.description"

USER_PLACEHOLDERS = ("user_name", "user_mention")

# Access levels that let a member who has not accepted the rules yet see the entry channel.
PUBLIC_ACCESS = ("public_readonly", "public_interactive")

# Permissions the Member role must never carry: every member who clicks the button receives it.
ELEVATED_PERMISSIONS = frozenset(
    {
        "ban_members",
        "deafen_members",
        "kick_members",
        "manage_channels",
        "manage_events",
        "manage_expressions",
        "manage_guild",
        "manage_messages",
        "manage_nicknames",
        "manage_roles",
        "manage_threads",
        "manage_webhooks",
        "mention_everyone",
        "moderate_members",
        "move_members",
        "mute_members",
        "view_audit_log",
        "view_guild_insights",
    }
)

_LINKS_DOC = (
    "You may write {channel_<key>} (for example {channel_catalog}) for a clickable link to a channel from "
    "server.channels."
)
_USER_DOC = "{user_name} is the member's display name, {user_mention} mentions them (mentions do not ping in embeds)."


def rule_name_key(number: int) -> str:
    return f"verification.rule.{number}.name"


def rule_value_key(number: int) -> str:
    return f"verification.rule.{number}.value"


def rule_keys(settings: Settings) -> list[str]:
    """Every configured rule's (name, value) keys, in display order."""
    keys: list[str] = []
    for number in range(1, settings.verification.rules + 1):
        keys += [rule_name_key(number), rule_value_key(number)]
    return keys


def link_keys(settings: Settings) -> tuple[str, ...]:
    """Keys whose text may contain channel links (checked by /setup for links to channels that do not exist)."""
    rules = tuple(rule_value_key(n) for n in range(1, settings.verification.rules + 1))
    return (
        WELCOME_DESCRIPTION,
        RULES_DESCRIPTION,
        *rules,
        CONFIRMED_DESCRIPTION,
        ALREADY_DESCRIPTION,
        DM_DESCRIPTION,
    )


def content_keys(settings: Settings) -> list[ContentKey]:
    v = settings.verification
    links = channel_placeholders(settings)
    where = f"the welcome panel that /setup posts in the '{v.channel or 'verification.channel'}' channel"
    g_panel = "Verification: welcome panel"
    g_rules = "Verification: server rules (on the welcome panel)"
    g_replies = "Verification: private replies after the 'I agree' button"
    g_dm = "Verification: direct message (verification.dm_confirmation)"
    keys = [
        ck(
            WELCOME_TITLE,
            Kind.TITLE,
            VERIFICATION,
            g_panel,
            f"Title of the first card of {where}, for example a welcome line.",
        ),
        ck(
            WELCOME_DESCRIPTION,
            Kind.DESCRIPTION,
            VERIFICATION,
            g_panel,
            f"Short introduction to the server on the first card of {where}. {_LINKS_DOC}",
            placeholders=links,
        ),
        ck(
            WELCOME_IMAGE,
            Kind.URL,
            VERIFICATION,
            g_panel,
            f"Optional large image (https:// URL) at the bottom of the first card of {where}. If empty, the "
            "branding banner is used when branding.panel_banner is on.",
            required=False,
        ),
        ck(
            WELCOME_THUMBNAIL,
            Kind.URL,
            VERIFICATION,
            g_panel,
            f"Optional small image (https:// URL) in the top-right corner of the first card of {where}. If empty, "
            "the branding logo is used when branding.panel_thumbnail is on.",
            required=False,
        ),
        ck(
            BUTTON_LABEL,
            Kind.BUTTON_LABEL,
            VERIFICATION,
            g_panel,
            f"Label of the button under {where}; it accepts the rules and grants the Member role (the "
            'specification calls it "I agree, enter the server").',
        ),
        ck(
            BUTTON_EMOJI,
            Kind.EMOJI,
            VERIFICATION,
            g_panel,
            "Optional emoji shown on the 'I agree' button.",
            required=False,
        ),
        ck(
            RULES_TITLE,
            Kind.TITLE,
            VERIFICATION,
            g_rules,
            f"Optional heading of the rules card (the second card of {where}).",
            required=False,
        ),
        ck(
            RULES_DESCRIPTION,
            Kind.DESCRIPTION,
            VERIFICATION,
            g_rules,
            f"Optional text above the list of rules on the rules card. {_LINKS_DOC}",
            required=False,
            placeholders=links,
        ),
    ]
    for number in range(1, v.rules + 1):
        position = f"rule {number} of {v.rules}"
        keys += [
            ck(
                rule_name_key(number),
                Kind.FIELD_NAME,
                VERIFICATION,
                g_rules,
                f"Heading of {position} on the rules card (for example a number and a short title). Links cannot "
                "be shown in headings.",
            ),
            ck(
                rule_value_key(number),
                Kind.FIELD_VALUE,
                VERIFICATION,
                g_rules,
                f"Text of {position} on the rules card. {_LINKS_DOC}",
                placeholders=links,
            ),
        ]
    keys += [
        ck(
            CONFIRMED_TITLE,
            Kind.TITLE,
            VERIFICATION,
            g_replies,
            "Optional title of the private confirmation shown after the member accepted the rules and received "
            "the Member role. {user_name} is the member's display name.",
            required=False,
            placeholders=("user_name",),
        ),
        ck(
            CONFIRMED_DESCRIPTION,
            Kind.DESCRIPTION,
            VERIFICATION,
            g_replies,
            "Private confirmation (only the member sees it) after they accepted the rules and received the Member "
            f"role; the member-only channels are visible from now on. {_USER_DOC} {_LINKS_DOC}",
            placeholders=(*USER_PLACEHOLDERS, *links),
        ),
        ck(
            ALREADY_TITLE,
            Kind.TITLE,
            VERIFICATION,
            g_replies,
            "Optional title of the private reply to a member who clicks the 'I agree' button although they already "
            "have the Member role. {user_name} is the member's display name.",
            required=False,
            placeholders=("user_name",),
        ),
        ck(
            ALREADY_DESCRIPTION,
            Kind.DESCRIPTION,
            VERIFICATION,
            g_replies,
            "Private reply to a member who clicks the 'I agree' button although they already have the Member role "
            f"(nothing changes). {_USER_DOC} {_LINKS_DOC}",
            placeholders=(*USER_PLACEHOLDERS, *links),
        ),
        ck(
            DM_TITLE,
            Kind.TITLE,
            VERIFICATION,
            g_dm,
            "Optional title of the direct message confirming a successful verification. {user_name} is the "
            "member's display name.",
            required=False,
            placeholders=("user_name",),
        ),
        ck(
            DM_DESCRIPTION,
            Kind.DESCRIPTION,
            VERIFICATION,
            g_dm,
            "Direct message confirming a successful verification, sent in addition to the private confirmation "
            "when verification.dm_confirmation and features.dm_notifications are on (required then). "
            f"{_USER_DOC} {_LINKS_DOC}",
            required=v.dm_confirmation and settings.features.dm_notifications,
            placeholders=(*USER_PLACEHOLDERS, *links),
        ),
    ]
    return keys


def _staff_role_lists(settings: Settings) -> list[tuple[str, tuple[str, ...]]]:
    """Settings that give a role staff or admin powers somewhere (the Member role must not appear in any)."""
    return staff_role_lists(settings)


def settings_problems(settings: Settings) -> list[str]:
    v = settings.verification
    problems: list[str] = []
    if not v.channel:
        problems.append("verification.channel: required (the entry channel where the rules panel is posted)")
    else:
        problems.extend(reference_problems(settings, channels=(("verification.channel", v.channel),)))
        if settings.server.channel(v.channel) is not None:
            access = effective_access(settings, v.channel)
            if access not in PUBLIC_ACCESS:
                problems.append(
                    f"verification.channel: channel '{v.channel}' must be visible to members who have not accepted "
                    f"the rules yet (access public_readonly or public_interactive; it is '{access}')"
                )
    member_role = settings.permissions.member_role
    if not member_role:
        problems.append("permissions.member_role: required (the role the verification button grants)")
    else:
        role = settings.server.role(member_role)
        if role is None:
            problems.append(f"permissions.member_role: role '{member_role}' is not defined in server.roles")
        else:
            elevated = sorted(set(role.permissions) & ELEVATED_PERMISSIONS)
            if elevated:
                problems.append(
                    f"server.roles '{member_role}': the Member role must not have {elevated} (every member who "
                    "accepts the rules receives it)"
                )
        for path, keys in _staff_role_lists(settings):
            if member_role in keys:
                problems.append(
                    f"permissions.member_role: '{member_role}' is also listed in {path}; the verification button "
                    "would give staff access to everyone who accepts the rules"
                )
    if v.rules < 1:
        problems.append(
            "verification.rules is not set (the number of server rules shown on the welcome panel, 1-25; each rule "
            "then needs verification.rule.<n>.name and .value)"
        )
    return problems
