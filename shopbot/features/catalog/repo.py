"""Product repository: synchronous ``fn(conn, ...)`` functions over the ``products`` table.

Inventory model
---------------
- ``stock_on_hand``: units physically available to sell, *including* units held for pending orders.
  ``NULL`` means unlimited.
- ``reserved``: units held by pending (unpaid / unfulfilled) orders.
- available = ``stock_on_hand - reserved`` (``None`` when unlimited). See ``available_quantity``.
- ``version``: bumped by EVERY write (edits and inventory movements) for optimistic concurrency.
- ``archived``: soft delete. Rows are never deleted (order lines reference them).

Use from other features (checkout)
----------------------------------
Every function takes the connection first and never opens, commits or rolls back a transaction itself,
so other features call them INSIDE their own ``app.db.transaction(fn, ...)`` unit of work. Write
functions must only be called inside a transaction. An exception raised here propagates and rolls back
the caller's whole transaction (e.g. the order row created in the same unit of work).

Typical checkout usage (all inside one ``app.db.transaction`` function)::

    product = repo.get_product(conn, guild_id, product_id)          # guild-scoped load
    if product is None or not product.listed: ...                   # unknown / not for sale
    if not repo.reserve(conn, product_id, quantity, now): ...       # out of stock / inactive -> no change
    ...                                                              # create the order rows
    repo.release(conn, product_id, quantity, now)                   # order cancelled / expired
    repo.commit_sale(conn, product_id, quantity, now)               # order paid / fulfilled

``reserve`` does not enforce ``max_per_order`` (an order may reserve a product in several steps); the
caller checks ``product.max_per_order`` against the order's total quantity before reserving.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from shopbot.core.money import MAX_AMOUNT_MINOR

NAME_MAX = 100
DESCRIPTION_MAX = 4000
IMAGE_URL_MAX = 2048
STOCK_MAX = 1_000_000_000
MAX_PER_ORDER_MAX = 1_000_000
SORT_LIMIT = 1_000_000
EDITABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "price_minor",
        "image_url",
        "stock_on_hand",
        "max_per_order",
        "active",
        "archived",
        "sort_order",
    }
)

# ---------------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------------


class CatalogError(Exception):
    """Base class for catalog repository errors."""


class ProductNotFound(CatalogError):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"product {product_id} does not exist")
        self.product_id = product_id


class ConcurrentUpdate(CatalogError):
    """The product changed (its version moved on) since the caller read it."""

    def __init__(self, product_id: int, expected_version: int, actual_version: int) -> None:
        super().__init__(f"product {product_id}: expected version {expected_version}, found {actual_version}")
        self.product_id = product_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class InvalidProduct(CatalogError, ValueError):
    """A field value violates the product rules (length, range, type)."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class StockBelowReserved(CatalogError):
    """A stock change would leave fewer units on hand than are reserved by pending orders."""

    def __init__(self, product_id: int, requested: int, reserved: int) -> None:
        super().__init__(f"product {product_id}: stock {requested} is below the {reserved} reserved units")
        self.product_id = product_id
        self.requested = requested
        self.reserved = reserved


class UnlimitedStock(CatalogError):
    """``add_stock`` was called on a product without a stock limit."""

    def __init__(self, product_id: int) -> None:
        super().__init__(f"product {product_id} has unlimited stock")
        self.product_id = product_id


class InsufficientReservation(CatalogError):
    """``commit_sale`` was asked to sell more units than are reserved (a caller bug)."""

    def __init__(self, product_id: int, quantity: int, reserved: int) -> None:
        super().__init__(f"product {product_id}: cannot sell {quantity} units, only {reserved} reserved")
        self.product_id = product_id
        self.quantity = quantity
        self.reserved = reserved


# ---------------------------------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Product:
    id: int
    guild_id: int
    name: str
    description: str
    price_minor: int
    image_url: str | None
    stock_on_hand: int | None
    reserved: int
    max_per_order: int | None
    active: bool
    archived: bool
    sort_order: int
    version: int
    created_at: int
    updated_at: int

    @property
    def unlimited(self) -> bool:
        return self.stock_on_hand is None

    @property
    def available(self) -> int | None:
        return available_quantity(self)

    @property
    def listed(self) -> bool:
        """Shown to customers and orderable (subject to stock)."""
        return self.active and not self.archived


def available_quantity(product: Product) -> int | None:
    """Units that can still be ordered: ``None`` when unlimited, otherwise ``stock_on_hand - reserved`` (>= 0)."""
    if product.stock_on_hand is None:
        return None
    return max(0, product.stock_on_hand - product.reserved)


def _row(r: sqlite3.Row) -> Product:
    return Product(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        name=str(r["name"]),
        description=str(r["description"]),
        price_minor=int(r["price_minor"]),
        image_url=None if r["image_url"] is None else str(r["image_url"]),
        stock_on_hand=None if r["stock_on_hand"] is None else int(r["stock_on_hand"]),
        reserved=int(r["reserved"]),
        max_per_order=None if r["max_per_order"] is None else int(r["max_per_order"]),
        active=bool(r["active"]),
        archived=bool(r["archived"]),
        sort_order=int(r["sort_order"]),
        version=int(r["version"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
    )


# ---------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------


def _int(field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidProduct(field, "must be a whole number")
    return value


def normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize product fields (names and descriptions are stripped). Raises InvalidProduct."""
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise InvalidProduct(sorted(unknown)[0], "is not an editable product field")
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "name":
            if not isinstance(value, str):
                raise InvalidProduct(key, "must be text")
            name = value.strip()
            if not 1 <= len(name) <= NAME_MAX:
                raise InvalidProduct(key, f"must be 1-{NAME_MAX} characters")
            out[key] = name
        elif key == "description":
            if not isinstance(value, str):
                raise InvalidProduct(key, "must be text")
            description = value.strip()
            if len(description) > DESCRIPTION_MAX:
                raise InvalidProduct(key, f"must be at most {DESCRIPTION_MAX} characters")
            out[key] = description
        elif key == "price_minor":
            price = _int(key, value)
            if not 0 <= price <= MAX_AMOUNT_MINOR:
                raise InvalidProduct(key, "is out of range")
            out[key] = price
        elif key == "image_url":
            if value is None or value == "":
                out[key] = None
                continue
            if not isinstance(value, str) or not value.startswith("https://") or len(value) > IMAGE_URL_MAX:
                raise InvalidProduct(key, f"must be an https:// URL of at most {IMAGE_URL_MAX} characters")
            if any(ch.isspace() for ch in value):
                raise InvalidProduct(key, "must not contain spaces")
            out[key] = value
        elif key == "stock_on_hand":
            if value is None:
                out[key] = None
                continue
            stock = _int(key, value)
            if not 0 <= stock <= STOCK_MAX:
                raise InvalidProduct(key, f"must be between 0 and {STOCK_MAX}")
            out[key] = stock
        elif key == "max_per_order":
            if value is None:
                out[key] = None
                continue
            limit = _int(key, value)
            if not 1 <= limit <= MAX_PER_ORDER_MAX:
                raise InvalidProduct(key, f"must be between 1 and {MAX_PER_ORDER_MAX}")
            out[key] = limit
        elif key in ("active", "archived"):
            if not isinstance(value, bool):
                raise InvalidProduct(key, "must be true or false")
            out[key] = value
        elif key == "sort_order":
            position = _int(key, value)
            if not -SORT_LIMIT <= position <= SORT_LIMIT:
                raise InvalidProduct(key, f"must be between {-SORT_LIMIT} and {SORT_LIMIT}")
            out[key] = position
    return out


def _positive_quantity(quantity: int) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidProduct("quantity", "must be a positive whole number")
    return quantity


# ---------------------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------------------


def _get_by_id(conn: sqlite3.Connection, product_id: int) -> Product | None:
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return None if row is None else _row(row)


def _require(conn: sqlite3.Connection, product_id: int) -> Product:
    product = _get_by_id(conn, product_id)
    if product is None:
        raise ProductNotFound(product_id)
    return product


def get_product(conn: sqlite3.Connection, guild_id: int, product_id: int) -> Product | None:
    """The product with this id in this guild, or None (also for another guild's product)."""
    row = conn.execute("SELECT * FROM products WHERE id = ? AND guild_id = ?", (product_id, guild_id)).fetchone()
    return None if row is None else _row(row)


# Static SQL: the optional filters are parameters (``? OR active = 1``), never string-built.
_LIST_SQL = (
    "SELECT * FROM products WHERE guild_id = ? AND (? OR active = 1) AND (? OR archived = 0) "
    "ORDER BY sort_order, id LIMIT ? OFFSET ?"
)
_COUNT_SQL = "SELECT COUNT(*) FROM products WHERE guild_id = ? AND (? OR active = 1) AND (? OR archived = 0)"
_SEARCH_SQL = (
    "SELECT * FROM products WHERE guild_id = ? AND (? OR archived = 0) "
    "AND (? = '' OR instr(lower(name), lower(?)) > 0 OR CAST(id AS TEXT) = ?) ORDER BY sort_order, id LIMIT ?"
)


def list_products(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    include_inactive: bool = False,
    include_archived: bool = False,
    offset: int = 0,
    limit: int | None = None,
) -> list[Product]:
    """Products in display order (``sort_order``, then ``id``). By default only listed products."""
    params = (
        guild_id,
        int(include_inactive),
        int(include_archived),
        -1 if limit is None else max(0, limit),
        max(0, offset),
    )
    return [_row(r) for r in conn.execute(_LIST_SQL, params)]


def count_products(
    conn: sqlite3.Connection, guild_id: int, *, include_inactive: bool = False, include_archived: bool = False
) -> int:
    row = conn.execute(_COUNT_SQL, (guild_id, int(include_inactive), int(include_archived))).fetchone()
    return int(row[0])


def search_products(
    conn: sqlite3.Connection, guild_id: int, query: str, *, include_archived: bool = False, limit: int = 25
) -> list[Product]:
    """Products whose name contains ``query`` (case-insensitive) or whose id equals it (staff autocomplete)."""
    text = query.strip()
    params = (guild_id, int(include_archived), text, text, text.lstrip("#"), max(1, limit))
    return [_row(r) for r in conn.execute(_SEARCH_SQL, params)]


# ---------------------------------------------------------------------------------------------------
# Writes (call inside a transaction)
# ---------------------------------------------------------------------------------------------------


def create_product(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    name: str,
    price_minor: int,
    now: int,
    description: str = "",
    stock_on_hand: int | None = None,
    max_per_order: int | None = None,
    image_url: str | None = None,
    active: bool = True,
    sort_order: int | None = None,
) -> Product:
    """Insert a product. Without ``sort_order`` it goes after every existing product."""
    fields = normalize_fields(
        {
            "name": name,
            "description": description,
            "price_minor": price_minor,
            "stock_on_hand": stock_on_hand,
            "max_per_order": max_per_order,
            "image_url": image_url,
            "active": active,
        }
    )
    if sort_order is None:
        row = conn.execute("SELECT MAX(sort_order) FROM products WHERE guild_id = ?", (guild_id,)).fetchone()
        position = 0 if row[0] is None else min(SORT_LIMIT, int(row[0]) + 1)
    else:
        position = normalize_fields({"sort_order": sort_order})["sort_order"]
    cursor = conn.execute(
        "INSERT INTO products(guild_id, name, description, price_minor, image_url, stock_on_hand, reserved, "
        "max_per_order, active, archived, sort_order, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?, 1, ?, ?)",
        (
            guild_id,
            fields["name"],
            fields["description"],
            fields["price_minor"],
            fields["image_url"],
            fields["stock_on_hand"],
            fields["max_per_order"],
            int(fields["active"]),
            position,
            now,
            now,
        ),
    )
    return _require(conn, int(cursor.lastrowid or 0))


def update_product(
    conn: sqlite3.Connection, product_id: int, expected_version: int, *, now: int, **fields: Any
) -> Product:
    """Change product fields if the product is still at ``expected_version`` (optimistic concurrency).

    Raises ``ProductNotFound``, ``ConcurrentUpdate`` (version moved on), ``InvalidProduct`` or
    ``StockBelowReserved`` (``stock_on_hand`` lower than the reserved units). When every given field already
    has the requested value nothing is written and the version is unchanged. Returns the current product.
    """
    current = _require(conn, product_id)
    if current.version != expected_version:
        raise ConcurrentUpdate(product_id, expected_version, current.version)
    clean = normalize_fields(fields)
    stock = clean.get("stock_on_hand", current.stock_on_hand)
    if stock is not None and stock < current.reserved:
        raise StockBelowReserved(product_id, stock, current.reserved)
    if all(getattr(current, key) == value for key, value in clean.items()):
        return current
    merged = {key: clean.get(key, getattr(current, key)) for key in EDITABLE_FIELDS}
    cursor = conn.execute(
        "UPDATE products SET name = ?, description = ?, price_minor = ?, image_url = ?, stock_on_hand = ?, "
        "max_per_order = ?, active = ?, archived = ?, sort_order = ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND version = ?",
        (
            merged["name"],
            merged["description"],
            merged["price_minor"],
            merged["image_url"],
            merged["stock_on_hand"],
            merged["max_per_order"],
            int(merged["active"]),
            int(merged["archived"]),
            merged["sort_order"],
            now,
            product_id,
            expected_version,
        ),
    )
    if cursor.rowcount != 1:  # unreachable inside a transaction; kept as a guard
        raise ConcurrentUpdate(product_id, expected_version, _require(conn, product_id).version)
    return _require(conn, product_id)


def touch_product(conn: sqlite3.Connection, product_id: int, expected_version: int, *, now: int) -> Product:
    """Record a change stored outside the products row (e.g. its uploaded image): bump the version if the product
    is still at ``expected_version``. Raises ``ProductNotFound`` or ``ConcurrentUpdate``."""
    cursor = conn.execute(
        "UPDATE products SET version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
        (now, product_id, expected_version),
    )
    if cursor.rowcount != 1:
        current = _require(conn, product_id)
        raise ConcurrentUpdate(product_id, expected_version, current.version)
    return _require(conn, product_id)


def set_stock(conn: sqlite3.Connection, product_id: int, stock_on_hand: int | None, now: int) -> Product:
    """Set the units on hand (``None`` = unlimited). Raises ``StockBelowReserved`` below the reserved units."""
    stock = normalize_fields({"stock_on_hand": stock_on_hand})["stock_on_hand"]
    cursor = conn.execute(
        "UPDATE products SET stock_on_hand = ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND (? IS NULL OR reserved <= ?)",
        (stock, now, product_id, stock, stock),
    )
    if cursor.rowcount == 0:
        current = _require(conn, product_id)
        raise StockBelowReserved(product_id, stock if stock is not None else 0, current.reserved)
    return _require(conn, product_id)


def add_stock(conn: sqlite3.Connection, product_id: int, delta: int, now: int) -> Product:
    """Restock (positive ``delta``) or write off (negative ``delta``) units of a limited-stock product.

    Raises ``UnlimitedStock`` for unlimited products, ``StockBelowReserved`` when the result would be lower
    than the reserved units (or negative), ``InvalidProduct`` for a zero/huge delta or result.
    """
    change = _int("delta", delta)
    if change == 0 or abs(change) > STOCK_MAX:
        raise InvalidProduct("delta", f"must be a non-zero whole number between {-STOCK_MAX} and {STOCK_MAX}")
    cursor = conn.execute(
        "UPDATE products SET stock_on_hand = stock_on_hand + ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND stock_on_hand IS NOT NULL AND stock_on_hand + ? >= reserved AND stock_on_hand + ? <= ?",
        (change, now, product_id, change, change, STOCK_MAX),
    )
    if cursor.rowcount == 0:
        current = _require(conn, product_id)
        if current.stock_on_hand is None:
            raise UnlimitedStock(product_id)
        result = current.stock_on_hand + change
        if result > STOCK_MAX:
            raise InvalidProduct("stock_on_hand", f"must be between 0 and {STOCK_MAX}")
        raise StockBelowReserved(product_id, result, current.reserved)
    return _require(conn, product_id)


def reserve(conn: sqlite3.Connection, product_id: int, quantity: int, now: int) -> bool:
    """Hold ``quantity`` units for a pending order. One conditional UPDATE: never oversells.

    Returns False (and changes nothing) when the product is missing, inactive, archived, or has fewer than
    ``quantity`` available units. Unlimited products always succeed while listed.
    """
    qty = _positive_quantity(quantity)
    cursor = conn.execute(
        "UPDATE products SET reserved = reserved + ?, version = version + 1, updated_at = ? "
        "WHERE id = ? AND active = 1 AND archived = 0 AND (stock_on_hand IS NULL OR stock_on_hand - reserved >= ?)",
        (qty, now, product_id, qty),
    )
    return cursor.rowcount == 1


def release(conn: sqlite3.Connection, product_id: int, quantity: int, now: int) -> int:
    """Return held units to availability (order cancelled/expired). Never goes below 0.

    Returns the number of units actually released (less than ``quantity`` only if fewer were reserved,
    which indicates a caller bug). Works for inactive and archived products. Raises ``ProductNotFound``.
    """
    qty = _positive_quantity(quantity)
    current = _require(conn, product_id)
    released = min(qty, current.reserved)
    conn.execute(
        "UPDATE products SET reserved = MAX(0, reserved - ?), version = version + 1, updated_at = ? WHERE id = ?",
        (qty, now, product_id),
    )
    return released


def commit_sale(conn: sqlite3.Connection, product_id: int, quantity: int, now: int) -> None:
    """Turn ``quantity`` reserved units into a sale.

    Limited stock: ``stock_on_hand -= quantity`` and ``reserved -= quantity``; unlimited: ``reserved -= quantity``.
    Raises ``ProductNotFound`` or ``InsufficientReservation`` (nothing changes) when fewer units are reserved.
    """
    qty = _positive_quantity(quantity)
    cursor = conn.execute(
        "UPDATE products SET stock_on_hand = CASE WHEN stock_on_hand IS NULL THEN NULL ELSE stock_on_hand - ? END, "
        "reserved = reserved - ?, version = version + 1, updated_at = ? WHERE id = ? AND reserved >= ?",
        (qty, qty, now, product_id, qty),
    )
    if cursor.rowcount == 0:
        current = _require(conn, product_id)
        raise InsufficientReservation(product_id, qty, current.reserved)
