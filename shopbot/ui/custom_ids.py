"""Stable component identifiers.

Grammar: ``sb:<route>:<action>[:<arg>...]`` — e.g. ``sb:tk:close:42``. IDs carry only record
identifiers; every handler re-loads the record from the database and re-checks authorization, so a
forged or stale ID can never grant access. IDs are stable across restarts, which is what makes
components persistent.
"""

from __future__ import annotations

import re

PREFIX = "sb"
NAME = r"[a-z][a-z0-9_]{0,23}"
ARG = r"[A-Za-z0-9_.\-]{1,64}"
TEMPLATE = rf"{PREFIX}:(?P<route>{NAME}):(?P<action>{NAME})(?P<args>(?::{ARG})*)"
_COMPILED = re.compile(TEMPLATE)
_NAME_RE = re.compile(rf"^{NAME}$")
_ARG_RE = re.compile(rf"^{ARG}$")
MAX_LENGTH = 100


class CustomIdError(ValueError):
    pass


def cid(route: str, action: str, *args: str | int) -> str:
    if not _NAME_RE.match(route) or not _NAME_RE.match(action):
        raise CustomIdError(f"invalid route/action {route!r}/{action!r}")
    parts = [PREFIX, route, action]
    for arg in args:
        text = str(arg)
        if not _ARG_RE.match(text):
            raise CustomIdError(f"invalid custom_id argument {text!r}")
        parts.append(text)
    value = ":".join(parts)
    if len(value) > MAX_LENGTH:
        raise CustomIdError(f"custom_id longer than {MAX_LENGTH} characters: {value!r}")
    return value


def parse(custom_id: str) -> tuple[str, str, tuple[str, ...]] | None:
    match = _COMPILED.fullmatch(custom_id)
    if match is None:
        return None
    raw_args = match.group("args")
    args = tuple(raw_args[1:].split(":")) if raw_args else ()
    return match.group("route"), match.group("action"), args
