"""``/category`` and ``/product categories``: catalog managers make the categories of the #catalog menu and put
products in them. Replies are operator text and always private.

Creating, editing and deleting a category is audited (``catalog.category_create`` / ``_edit`` / ``_delete``) and
refreshes the listing at once, so the header's menu shows the change (the reply carries the refresh report). Putting a
product in a category or taking it out (``catalog.product_categories``) changes no public message: a category's
private view reads its products when a member picks it. Deleting a category takes it off its products; the products
stay.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from shopbot.config.content import MissingContent
from shopbot.features.catalog import categories, repo, service, views
from shopbot.features.catalog.categories import Category
from shopbot.features.catalog.service import NOTED, REFUSED, SAVED, OpReply
from shopbot.ui.spec import MessageSpec

if TYPE_CHECKING:
    from shopbot.context import App

CLEAR = "-"  # the /category edit value that removes a category's emoji or description


class _Refused(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def _refused(app: App, exc: Exception) -> OpReply:
    """Operator reply for an expected refusal (invalid input, unknown or duplicate category, too many)."""
    op = app.op
    if isinstance(exc, categories.CategoryNotFound):
        return OpReply(op("catalog.category.not_found", category_id=exc.category_id), tone="warning", banner=REFUSED)
    if isinstance(exc, _Refused):
        text = exc.text
    elif isinstance(exc, categories.DuplicateName):
        text = op(
            "catalog.category.invalid.duplicate", name=views.escape(exc.existing.name), category_id=exc.existing.id
        )
    elif isinstance(exc, categories.TooManyCategories):
        text = op("catalog.category.too_many", max=categories.MAX_CATEGORIES)
    elif isinstance(exc, categories.InvalidCategory):
        reasons = {
            "name": op("catalog.category.invalid.name", max=categories.NAME_MAX),
            "emoji": op("catalog.category.invalid.emoji"),
            "description": op("catalog.category.invalid.description", max=categories.DESCRIPTION_MAX),
            "position": op(
                "catalog.category.invalid.position", min=-categories.POSITION_LIMIT, max=categories.POSITION_LIMIT
            ),
        }
        text = reasons.get(exc.field, str(exc))
    else:
        raise exc
    return OpReply(op("catalog.cmd.invalid"), text, tone="warning", banner=REFUSED)


def _name(app: App, raw: str) -> str:
    name = categories.normalize_name(raw)
    try:
        everything = views.all_name(app.ui)
    except MissingContent:
        everything = ""
    if any(categories.same_name(name, built_in) for built_in in ("all", everything) if built_in):
        raise _Refused(app.op("catalog.category.invalid.name_all", name=views.escape(name)))
    return name


def _emoji(app: App, raw: str) -> str:
    return "" if raw.strip() == CLEAR else categories.normalize_emoji(raw, app.content.icon_names())


def _description(raw: str) -> str:
    return "" if raw.strip() == CLEAR else categories.normalize_description(raw)


async def _audit(app: App, action: str, actor_id: int, category_id: int, details: dict[str, Any]) -> None:
    await app.audit.record(
        f"catalog.category_{action}", actor_id=actor_id, target_type="category", target_id=category_id, details=details
    )


async def create(
    app: App, *, actor_id: int, name: str, emoji: str = "", description: str = "", position: int | None = None
) -> OpReply:
    """``/category create``: a new category at the end of the menu (or at ``position``)."""
    op = app.op
    try:
        values: dict[str, Any] = {
            "name": _name(app, name),
            "emoji": _emoji(app, emoji),
            "description": _description(description),
            "position": None if position is None else categories.normalize_position(position),
        }
        category = await app.db.transaction(_tx_create, app.env.guild_id, values, actor_id, app.clock.now())
    except (categories.CategoryError, _Refused) as exc:
        return _refused(app, exc)
    details = {"name": category.name, "emoji": category.emoji, "description": category.description}
    await _audit(app, "create", actor_id, category.id, {**details, "position": category.position})
    shown = views.escape(category.name)
    reply = OpReply(
        op("catalog.category.created", category_id=category.id, name=shown),
        op("catalog.category.created_hint", name=shown),
        tone="success",
        banner=SAVED,
    )
    return await service.with_listing_report(app, reply)


def _tx_create(conn: sqlite3.Connection, guild_id: int, values: dict[str, Any], actor_id: int, now: int) -> Category:
    return categories.create_category(conn, guild_id, actor_id=actor_id, now=now, **values)


async def edit(
    app: App,
    *,
    actor_id: int,
    category_id: int,
    name: str | None = None,
    emoji: str | None = None,
    description: str | None = None,
    position: int | None = None,
) -> OpReply:
    """``/category edit``: change the given values (``CLEAR`` removes the emoji or the description)."""
    op = app.op
    try:
        fields: dict[str, str | int] = {}
        if name is not None:
            fields["name"] = _name(app, name)
        if emoji is not None:
            fields["emoji"] = _emoji(app, emoji)
        if description is not None:
            fields["description"] = _description(description)
        if position is not None:
            fields["position"] = categories.normalize_position(position)
        if not fields:
            raise _Refused(op("catalog.category.edit_nothing"))
        before, after = await app.db.transaction(_tx_update, app.env.guild_id, category_id, fields, app.clock.now())
    except (categories.CategoryError, _Refused) as exc:
        return _refused(app, exc)
    shown = views.escape(after.name)
    if before == after:
        return OpReply(op("catalog.category.unchanged", category_id=category_id, name=shown), banner=NOTED)
    changes = {k: [getattr(before, k), getattr(after, k)] for k in sorted(fields)}
    changes = {k: v for k, v in changes.items() if v[0] != v[1]}
    await _audit(app, "edit", actor_id, category_id, changes)
    reply = OpReply(op("catalog.category.updated", category_id=category_id, name=shown), tone="success", banner=SAVED)
    return await service.with_listing_report(app, reply)


def _tx_update(
    conn: sqlite3.Connection, guild_id: int, category_id: int, fields: dict[str, str | int], now: int
) -> tuple[Category, Category]:
    return categories.update_category(conn, guild_id, category_id, now=now, **fields)


def _items(conn: sqlite3.Connection, guild_id: int, category_id: int) -> tuple[Category, int]:
    category = categories.get_category(conn, guild_id, category_id)
    if category is None:
        raise categories.CategoryNotFound(category_id)
    listed, hidden = categories.product_counts(conn, guild_id).get(category_id, (0, 0))
    return category, listed + hidden


def _tx_delete(conn: sqlite3.Connection, guild_id: int, category_id: int) -> tuple[Category, int, int]:
    """Delete the category. Returns it, its items as the confirmation counts them, and the number of links removed
    (archived items' too)."""
    _, items = _items(conn, guild_id, category_id)
    category, links = categories.delete_category(conn, guild_id, category_id)
    return category, items, links


async def delete(app: App, *, actor_id: int, category_id: int, confirm: bool) -> OpReply:
    """``/category delete``: without ``confirm``, what it would do; with it, the category goes (its products stay).
    Both replies count the items ``/category list`` shows (archived ones are not); the audit records every link."""
    op = app.op
    try:
        if not confirm:
            category, items = await app.db.run(_items, app.env.guild_id, category_id)
            text = op("catalog.category.delete_confirm", name=views.escape(category.name), items=items)
            return OpReply(op("catalog.category.delete_title", category_id=category_id), text, tone="warning")
        category, items, links = await app.db.transaction(_tx_delete, app.env.guild_id, category_id)
    except categories.CategoryError as exc:
        return _refused(app, exc)
    await _audit(app, "delete", actor_id, category_id, {"name": category.name, "products_untagged": links})
    text = op("catalog.category.deleted", name=views.escape(category.name), items=items)
    return await service.with_listing_report(app, OpReply(text, tone="success", banner=SAVED))


def _list_data(conn: sqlite3.Connection, guild_id: int) -> tuple[list[Category], dict[int, tuple[int, int]], int]:
    return (
        categories.list_categories(conn, guild_id),
        categories.product_counts(conn, guild_id),
        repo.count_products(conn, guild_id),
    )


async def list_messages(app: App) -> list[MessageSpec]:
    """``/category list``: "All" and every category in menu order, with their items."""
    menu, counts, everything = await app.db.run(_list_data, app.env.guild_id)
    return views.category_list_messages(app.ui, app.op, menu, counts, everything)


def _tx_tag(
    conn: sqlite3.Connection, guild_id: int, product_id: int, add: int | None, remove: int | None
) -> tuple[list[tuple[str, Category, bool]], list[Category]]:
    product = repo.get_product(conn, guild_id, product_id)
    if product is None:
        raise repo.ProductNotFound(product_id)
    if product.archived:
        raise service.ArchivedProduct(product_id)
    done: list[tuple[str, Category, bool]] = []
    for action, category_id in (("add", add), ("remove", remove)):
        if category_id is None:
            continue
        category = categories.get_category(conn, guild_id, category_id)
        if category is None:
            raise categories.CategoryNotFound(category_id)
        if action == "add":
            done.append((action, category, categories.add_product(conn, product_id, category_id)))
        else:
            done.append((action, category, categories.remove_product(conn, product_id, category_id)))
    return done, categories.categories_of(conn, guild_id, product_id)


async def product_categories(
    app: App, *, actor_id: int, product_id: int, add: int | None = None, remove: int | None = None
) -> OpReply:
    """``/product categories``: put the product in ``add`` and/or take it out of ``remove``; without either, show
    its categories."""
    op = app.op
    try:
        done, now_in = await app.db.transaction(_tx_tag, app.env.guild_id, product_id, add, remove)
    except categories.CategoryError as exc:
        return _refused(app, exc)
    except (repo.CatalogError, service.InputProblems) as exc:
        return service.error_reply(app, exc)
    keys = {
        ("add", True): "catalog.cmd.category_added",
        ("add", False): "catalog.cmd.category_already_in",
        ("remove", True): "catalog.cmd.category_removed",
        ("remove", False): "catalog.cmd.category_not_in",
    }
    lines = [op(keys[(action, changed)], name=views.escape(category.name)) for action, category, changed in done]
    if now_in:
        lines.append(op("catalog.cmd.categories_now", names=", ".join(views.escape(c.name) for c in now_in)))
    else:
        lines.append(op("catalog.cmd.categories_none"))
    changed = {action: category.name for action, category, did in done if did}
    title = op("catalog.cmd.categories_title", product_id=product_id)
    if not changed:
        return OpReply(title, "\n".join(lines), banner=NOTED)
    details = {("added" if action == "add" else "removed"): name for action, name in changed.items()}
    await app.audit.record(
        "catalog.product_categories", actor_id=actor_id, target_type="product", target_id=product_id, details=details
    )
    return OpReply(title, "\n".join(lines), tone="success", banner=SAVED)


async def autocomplete(app: App, query: str, product_id: int | None = None) -> list[Category]:
    """Categories whose name contains ``query``; with ``product_id``, only the ones that product is in."""
    if product_id is not None:
        tagged = await app.db.run(categories.categories_of, app.env.guild_id, product_id)
        wanted = query.strip().casefold()
        return [c for c in tagged if wanted in c.name.casefold()]
    return await app.db.run(categories.search, app.env.guild_id, query)
