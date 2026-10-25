"""Preview: every credits UI state, built by the runtime view functions from synthetic sample data.

Amounts and ledger rows exist only in the preview (member/actor IDs are obviously fake). A state whose text is not
configured yet is skipped (the content store records the missing keys for the checker).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shopbot.config.content import MissingContent
from shopbot.features.credits import repo, service, views
from shopbot.features.credits.content_keys import CREDITS
from shopbot.ui.spec import MessageSpec, PreviewState

if TYPE_CHECKING:
    from shopbot.context import App

SAMPLE_MEMBER_ID = 100_000_000_000_000_001
SAMPLE_STAFF_ID = 100_000_000_000_000_002
SAMPLE_BALANCE_MINOR = 12_345
SAMPLE_LARGE_MINOR = 99_999_999_999
SAMPLE_CHANGE_MINOR = 2_500
SAMPLE_TIME = 1_700_000_000


def _sample_entries(count: int) -> tuple[repo.Entry, ...]:
    kinds = (repo.KIND_ADMIN_ADJUST, repo.KIND_ORDER_DEBIT, repo.KIND_ORDER_RETURN, repo.KIND_REFUND_TO_CREDIT)
    entries: list[repo.Entry] = []
    for index in range(count):
        kind = kinds[index % len(kinds)]
        order_number = None if kind == repo.KIND_ADMIN_ADJUST else 1000 + index
        delta = -1_500 if kind == repo.KIND_ORDER_DEBIT else 1_500
        entries.append(
            repo.Entry(
                id=count - index,
                guild_id=0,
                user_id=SAMPLE_MEMBER_ID,
                delta_minor=delta,
                kind=kind,
                order_id=order_number,
                actor_id=SAMPLE_STAFF_ID if kind == repo.KIND_ADMIN_ADJUST else None,
                note=f"[sample note {index + 1}]" if kind == repo.KIND_ADMIN_ADJUST else None,
                created_at=SAMPLE_TIME - index * 3600,
                order_number=order_number,
            )
        )
    return tuple(entries)


def preview(app: App) -> list[PreviewState]:
    ui = app.ui
    states: list[PreviewState] = []

    def add(name: str, note: str, build: Callable[[], MessageSpec | None]) -> None:
        try:
            spec = build()
        except MissingContent:
            return
        if spec is not None:
            states.append(PreviewState(feature=CREDITS, name=name, note=note, spec=spec))

    def money(minor: int) -> str:
        return service.customer_amount(app, minor)

    def op_money(minor: int) -> str:
        return service.operator_amount(app, minor)

    add(
        "balance",
        "Private reply to the 'my balance' button (sample balance).",
        lambda: views.balance_reply(ui, amount=money(SAMPLE_BALANCE_MINOR)),
    )
    add(
        "balance_large",
        "Private reply to the 'my balance' button with a very large sample balance.",
        lambda: views.balance_reply(ui, amount=money(SAMPLE_LARGE_MINOR)),
    )
    add(
        "adjusted_notice_added",
        "Direct message to a member after an admin added credits (only when a notice is configured).",
        lambda: views.adjusted_notice(
            ui, amount=money(SAMPLE_CHANGE_MINOR), balance=money(SAMPLE_BALANCE_MINOR + SAMPLE_CHANGE_MINOR)
        ),
    )
    add(
        "adjusted_notice_removed",
        "Direct message to a member after an admin removed credits (the amount is negative).",
        lambda: views.adjusted_notice(
            ui, amount=money(-SAMPLE_CHANGE_MINOR), balance=money(SAMPLE_BALANCE_MINOR - SAMPLE_CHANGE_MINOR)
        ),
    )
    full_page = views.HistoryPage(
        user_id=SAMPLE_MEMBER_ID,
        page=0,
        pages=3,
        total=3 * views.HISTORY_PAGE_SIZE,
        balance_minor=SAMPLE_LARGE_MINOR,
        entries=_sample_entries(views.HISTORY_PAGE_SIZE),
    )
    add(
        "staff_history",
        "Staff only (/credits history): a full page of sample entries with paging buttons.",
        lambda: views.history_view(ui, app.op, op_money, full_page),
    )
    empty_page = views.HistoryPage(user_id=SAMPLE_MEMBER_ID, page=0, pages=1, total=0, balance_minor=0, entries=())
    add(
        "staff_history_empty",
        "Staff only (/credits history) for a member without entries.",
        lambda: views.history_view(ui, app.op, op_money, empty_page),
    )
    return states
