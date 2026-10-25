"""Ticket records (rows of the ``tickets`` table) and their states."""

from __future__ import annotations

from dataclasses import dataclass, field

KIND_SUPPORT = "support"
KIND_ORDER = "order"

CREATING = "creating"
OPEN = "open"
CLOSED = "closed"
DELETED = "deleted"
FAILED = "failed"
STATUSES = (CREATING, OPEN, CLOSED, DELETED, FAILED)
ACTIVE_STATUSES = (CREATING, OPEN)

TRANSCRIPT_NONE = "none"
TRANSCRIPT_PENDING = "pending"
TRANSCRIPT_DONE = "done"
TRANSCRIPT_FAILED = "failed"
TRANSCRIPT_SKIPPED = "skipped"

CLOSE_REASON_INACTIVE = "inactive"
CLOSE_REASON_CHANNEL_DELETED = "channel_deleted"


@dataclass(frozen=True)
class Ticket:
    id: int
    guild_id: int
    number: int
    kind: str
    category_id: str
    user_id: int
    channel_id: int | None
    status: str
    assigned_staff_id: int | None
    form_answers: dict[str, str] = field(compare=False)
    control_message_id: int | None
    created_at: int
    updated_at: int
    last_activity_at: int
    closed_at: int | None
    closed_by: int | None
    close_reason: str | None
    deleted_at: int | None
    transcript_status: str
    transcript_message_id: int | None
    version: int

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == CLOSED

    @property
    def is_support(self) -> bool:
        return self.kind == KIND_SUPPORT
