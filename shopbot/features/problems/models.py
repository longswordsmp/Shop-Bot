"""Problem report records (rows of ``problem_reports``) and their states.

A report is ``open`` from its submission until staff decide it, exactly once: ``open -> resolved | rejected``.
Its ticket (``ticket_id``) is a support ticket created through the ticket service; ``panel_message_id`` is the live
report panel in that ticket (NULL only while the report is being created).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shopbot.features.problems.content_keys import OPEN, REJECTED, RESOLVED

__all__ = ["OPEN", "REJECTED", "RESOLVED", "Report"]


@dataclass(frozen=True)
class Report:
    id: int
    guild_id: int
    user_id: int
    nonce: str
    order_id: int | None
    issue_type: str
    answers: dict[str, str] = field(compare=False)
    ticket_id: int
    panel_message_id: int | None
    status: str
    reason: str | None
    decided_by: int | None
    created_at: int
    updated_at: int
    decided_at: int | None

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    @property
    def finished(self) -> bool:
        """Its ticket panel was posted (the creation completed)."""
        return self.panel_message_id is not None
