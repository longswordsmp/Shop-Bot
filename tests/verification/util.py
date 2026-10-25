"""Test environment for the verification feature: an entry category/channel, a member-only area and the Member
role added to the test base layout (tests/base_settings.toml has none of them)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopbot.config.settings import Settings
from shopbot.db.repos import audit as audit_repo
from shopbot.features.verification import repo
from shopbot.features.verification import service as verification_service
from tests.fakes import FakeMessage, FakeRole, FakeTextChannel, click
from tests.helpers import TestEnv, deep_merge, make_settings, make_test_env, provision_channels, server_extension

RULES = 3
AGREE_ID = "sb:verify:agree"


def verification_overrides(extra: dict[str, Any] | None = None, *, rules: int = RULES) -> dict[str, Any]:
    overrides = deep_merge(
        server_extension(
            roles=[{"key": "member"}],
            categories=[
                {"key": "start_here", "access": "public_readonly"},
                {"key": "members_area", "access": "members_readonly"},
            ],
            channels=[
                {"key": "welcome", "category": "start_here", "access": "inherit"},
                {"key": "lounge", "category": "members_area", "access": "inherit"},
            ],
        ),
        {
            "features": {"verification": True},
            "permissions": {"member_role": "member"},
            "verification": {"channel": "welcome", "rules": rules},
        },
    )
    return deep_merge(overrides, extra or {})


def verification_settings(extra: dict[str, Any] | None = None, *, rules: int = RULES) -> Settings:
    return make_settings(verification_overrides(extra, rules=rules))


async def verification_env(
    tmp_path: Path,
    extra: dict[str, Any] | None = None,
    *,
    rules: int = RULES,
    content: dict[str, str] | None = None,
    provision: bool = True,
    role: bool = True,
) -> TestEnv:
    """Channels created and the Member role recorded as /setup would have done (unless switched off)."""
    tenv = await make_test_env(tmp_path, settings=verification_settings(extra, rules=rules), content=content)
    if provision:
        await provision_channels(tenv)
    if role:
        member_role = tenv.guild.add_role(name="member", position=2)
        await tenv.app.resources.remember("role", "member", member_role.id)
    return tenv


def member_role(tenv: TestEnv) -> FakeRole:
    role = tenv.app.resources.role(tenv.guild, "member")  # type: ignore[arg-type]
    assert isinstance(role, FakeRole)
    return role


def channel(tenv: TestEnv, key: str) -> FakeTextChannel:
    found = tenv.app.resources.text_channel(tenv.guild, key)  # type: ignore[arg-type]
    assert isinstance(found, FakeTextChannel)
    return found


def panel_messages(tenv: TestEnv) -> list[FakeMessage]:
    return list(channel(tenv, "welcome").messages)


async def publish(tenv: TestEnv) -> list[str]:
    return await verification_service.publish(tenv.app)


async def agree(tenv: TestEnv, user: Any, *, message: FakeMessage | None = None, app: Any = None) -> Any:
    return await click(app or tenv.app, user=user, custom_id=AGREE_ID, message=message)


def has_member_role(tenv: TestEnv, user: Any) -> bool:
    role = member_role(tenv)
    return sum(1 for r in user.roles if r.id == role.id) == 1


def reply(interaction: Any) -> FakeMessage:
    """The single private answer (deferred, then sent as an ephemeral followup)."""
    assert len(interaction.sent) == 1, [m.text() for m in interaction.sent]
    message: FakeMessage = interaction.sent[0]
    assert message.flags.ephemeral
    return message


async def audit(tenv: TestEnv, action: str) -> list[audit_repo.AuditEvent]:
    events = await tenv.app.db.run(audit_repo.recent, tenv.app.env.guild_id, limit=200)
    return [event for event in events if event.action == action]


async def acceptance(tenv: TestEnv, user_id: int) -> repo.Acceptance | None:
    return await tenv.app.db.run(repo.get_acceptance, tenv.app.env.guild_id, user_id)
