"""Print the bot invitation URL with exactly the permissions the implemented features use.

Usage:
    python -m tools.invite_url <APPLICATION_ID>

The application ID is shown on the "General Information" page of the Discord Developer Portal. It is not a
secret. Scopes: ``bot`` and ``applications.commands`` (slash commands).
"""

from __future__ import annotations

import sys

import discord

from shopbot.core.permissions import BOT_PERMISSIONS

SCOPES = ("bot", "applications.commands")


def required_permissions() -> discord.Permissions:
    names = sorted({name for group in BOT_PERMISSIONS.values() for name in group})
    return discord.Permissions(**dict.fromkeys(names, True))


def invite_url(application_id: int) -> str:
    return discord.utils.oauth_url(application_id, permissions=required_permissions(), scopes=SCOPES)


def explain() -> list[str]:
    lines = []
    for capability, names in BOT_PERMISSIONS.items():
        lines.append(f"  {capability:<12} {', '.join(names)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].isdigit():
        print(__doc__)
        return 2
    perms = required_permissions()
    print(invite_url(int(args[0])))
    print(f"\nPermission integer: {perms.value}")
    print("Permissions by capability:")
    print("\n".join(explain()))
    print("\nThe bot does NOT request Administrator. After inviting, drag the bot's role above every role it")
    print("must manage (Server Settings > Roles) - Discord only lets a bot manage roles below its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
