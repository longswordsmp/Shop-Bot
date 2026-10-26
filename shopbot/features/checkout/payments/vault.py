"""Encryption of submitted gift card codes.

A code is stored only as a Fernet token (AES-128-CBC with an HMAC-SHA256 signature) and a keyed fingerprint (an
HMAC of the code without spaces, dashes or case, so the same card is refused when it is submitted again). The key
comes from the ``GIFT_CARD_KEY`` environment variable, or from a key file next to the database that is created on
first use (readable by the bot's user only). Neither the key nor a code is ever logged; error messages never
include them.

Losing the key makes the stored codes unreadable (staff then ask the customer for the card again); a new key file
is never created over an existing one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_FILE_NAME = "gift_card.key"
_FINGERPRINT_CONTEXT = b"shopbot gift card fingerprint v1"


class VaultError(Exception):
    """The key could not be loaded, or a stored code could not be read with it (no secret in the message)."""


@dataclass(frozen=True)
class CardSecret:
    """A gift card's code and optional PIN, as the customer typed them (trimmed)."""

    code: str = field(repr=False)
    pin: str | None = field(default=None, repr=False)


def normalize_code(code: str) -> str:
    """The code compared for duplicates: letters and digits only, upper case."""
    return "".join(ch for ch in code if ch.isalnum()).upper()


class Vault:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)
        self._mac_key = hmac.new(key, _FINGERPRINT_CONTEXT, hashlib.sha256).digest()

    def seal(self, secret: CardSecret) -> bytes:
        payload = json.dumps({"code": secret.code, "pin": secret.pin}, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload)

    def open(self, token: bytes) -> CardSecret:
        try:
            data = json.loads(self._fernet.decrypt(token).decode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise VaultError("the stored gift card cannot be read with the current key") from exc
        return CardSecret(code=str(data["code"]), pin=data.get("pin") or None)

    def fingerprint(self, code: str) -> str:
        return hmac.new(self._mac_key, normalize_code(code).encode("utf-8"), hashlib.sha256).hexdigest()


def load_vault(env_key: str | None, db_path: Path) -> Vault:
    """The vault for ``GIFT_CARD_KEY`` when set, else for the key file next to the database (created once)."""
    if env_key:
        return Vault(env_key.encode("ascii"))
    return Vault(_key_file(db_path.parent / KEY_FILE_NAME))


def _key_file(path: Path) -> bytes:
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # created meanwhile: use that one
        return _read_key(path)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
        handle.flush()
        os.fsync(handle.fileno())
    return key


def _read_key(path: Path) -> bytes:
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except ValueError as exc:
        raise VaultError(f"{path.name} does not hold a valid key") from exc
    return key
