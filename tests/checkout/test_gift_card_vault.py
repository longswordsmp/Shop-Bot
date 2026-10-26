"""The gift card vault: codes are sealed with a Fernet key from GIFT_CARD_KEY or a key file created once next to the
database (owner-readable only, never replaced); the fingerprint ignores case, spaces and dashes."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from shopbot.env import EnvError, load_env
from shopbot.features.checkout.payments.vault import KEY_FILE_NAME, CardSecret, VaultError, load_vault

TOKEN = "A" * 24 + "." + "B" * 6 + "." + "C" * 38


def test_the_key_file_is_created_once_owner_only_and_reused(tmp_path: Path) -> None:
    db = tmp_path / "data" / "bot.db"
    first = load_vault(None, db)
    key_file = db.parent / KEY_FILE_NAME
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    sealed = first.seal(CardSecret("ABCD-1234-EFGH", "99"))
    key = key_file.read_bytes()
    second = load_vault(None, db)  # after a restart: the same key
    assert second.open(sealed) == CardSecret("ABCD-1234-EFGH", "99")
    assert key_file.read_bytes() == key  # not rewritten
    assert first.fingerprint("abcd 1234-efgh") == second.fingerprint("ABCD-1234-EFGH")


def test_the_environment_key_wins_and_another_key_cannot_open_a_code(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    vault = load_vault(key, tmp_path / "bot.db")
    assert not (tmp_path / KEY_FILE_NAME).exists()
    sealed = vault.seal(CardSecret("ABCD-1234-EFGH"))
    with pytest.raises(VaultError) as exc:
        load_vault(Fernet.generate_key().decode(), tmp_path / "bot.db").open(sealed)
    assert "ABCD" not in str(exc.value)


def test_a_damaged_key_file_is_refused_not_replaced(tmp_path: Path) -> None:
    (tmp_path / KEY_FILE_NAME).write_bytes(b"not a key")
    with pytest.raises(VaultError):
        load_vault(None, tmp_path / "bot.db")
    assert (tmp_path / KEY_FILE_NAME).read_bytes() == b"not a key"


def test_gift_card_key_is_validated_and_never_shown(tmp_path: Path) -> None:
    base = {"DISCORD_TOKEN": TOKEN, "GUILD_ID": "123456789012345678", "DB_PATH": str(tmp_path / "x.db")}
    with pytest.raises(EnvError) as exc:
        load_env({**base, "GIFT_CARD_KEY": "short"})
    assert any("GIFT_CARD_KEY" in p and "short" not in p for p in exc.value.problems)
    key = Fernet.generate_key().decode()
    env = load_env({**base, "GIFT_CARD_KEY": key})
    assert env.gift_card_key == key and key not in repr(env)
    assert load_env(base).gift_card_key is None
