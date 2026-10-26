"""Catalog categories: the repository (synchronous ``fn(conn, ...)`` functions over ``catalog_categories`` and
``product_categories``) and the rules for names, emoji and descriptions.

A category groups products for the menu on the #catalog header card: a member picks one and sees its products in a
private message (``service.category_view``). Catalog managers make them with ``/category`` and put products in them
with ``/product categories``; a product can be in several. ``ALL`` (0) is the built-in first choice of the menu: every
listed product, never stored. At most ``MAX_CATEGORIES`` exist, so the menu with "All" fits Discord's 25 options.
Names are unique per server ignoring case. Deleting a category removes its links only; the products stay.

Like ``repo``, the write functions never open, commit or roll back a transaction: call them inside
``app.db.transaction``.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass

from shopbot.core.countries import COUNTRIES
from shopbot.features.catalog.repo import Product, product_from_row

ALL = 0  # the built-in "All" choice (stored categories have ids from 1)
MAX_CATEGORIES = 24  # plus "All": 25 options, the most one Discord select menu holds
NAME_MAX = 50
DESCRIPTION_MAX = 100  # Discord's limit for a menu option's description
POSITION_LIMIT = 1_000_000
EDITABLE_FIELDS = frozenset({"name", "emoji", "description", "position"})


# ---------------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------------


class CategoryError(Exception):
    """Base class for category errors."""


class CategoryNotFound(CategoryError):
    def __init__(self, category_id: int) -> None:
        super().__init__(f"category {category_id} does not exist")
        self.category_id = category_id


class DuplicateName(CategoryError):
    """Another category already has this name (ignoring case)."""

    def __init__(self, existing: Category) -> None:
        super().__init__(f"category name {existing.name!r} is taken")
        self.existing = existing


class TooManyCategories(CategoryError):
    def __init__(self) -> None:
        super().__init__(f"at most {MAX_CATEGORIES} categories")


class InvalidCategory(CategoryError, ValueError):
    """``field`` is 'name', 'emoji', 'description' or 'position'."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


# ---------------------------------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    id: int
    guild_id: int
    name: str
    emoji: str  # '{icon_<name>}', a unicode emoji, or ''
    description: str
    position: int
    created_at: int
    updated_at: int


def _row(r: sqlite3.Row) -> Category:
    return Category(
        id=int(r["id"]),
        guild_id=int(r["guild_id"]),
        name=str(r["name"]),
        emoji=str(r["emoji"]),
        description=str(r["description"]),
        position=int(r["position"]),
        created_at=int(r["created_at"]),
        updated_at=int(r["updated_at"]),
    )


# ---------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------

_ICON_FORMS = re.compile(r"^(?:\{icon_([a-z0-9_]{2,32})\}|:?(?:icon_)?([a-z0-9_]{2,32}):?)$")
# The code points of Discord's unicode emoji (pictographs and symbols, the letters of flags, the skin tones). A
# lookalike symbol outside them (an arrow, a star) is refused: Discord refuses it as a menu option's emoji, and the
# #catalog header would then be sent without the menu's emoji (``service._refresh_listing``).
_EMOJI_RANGES = (
    (0x1F004, 0x1F004),
    (0x1F0CF, 0x1F0CF),
    (0x1F170, 0x1F171),
    (0x1F17E, 0x1F17F),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F1E6, 0x1F1FF),
    (0x1F201, 0x1F202),
    (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F),
    (0x1F232, 0x1F23A),
    (0x1F250, 0x1F251),
    (0x1F300, 0x1F6FF),
    (0x1F7E0, 0x1F7EB),
    (0x1F7F0, 0x1F7F0),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
    # Miscellaneous symbols and dingbats: only the code points that are emoji (a star or a check mark next to them is
    # a plain symbol).
    (0x2600, 0x2604),
    (0x260E, 0x260E),
    (0x2611, 0x2611),
    (0x2614, 0x2615),
    (0x2618, 0x2618),
    (0x261D, 0x261D),
    (0x2620, 0x2620),
    (0x2622, 0x2623),
    (0x2626, 0x2626),
    (0x262A, 0x262A),
    (0x262E, 0x262F),
    (0x2638, 0x263A),
    (0x2640, 0x2640),
    (0x2642, 0x2642),
    (0x2648, 0x2653),
    (0x265F, 0x2660),
    (0x2663, 0x2663),
    (0x2665, 0x2666),
    (0x2668, 0x2668),
    (0x267B, 0x267B),
    (0x267E, 0x267F),
    (0x2692, 0x2697),
    (0x2699, 0x2699),
    (0x269B, 0x269C),
    (0x26A0, 0x26A1),
    (0x26A7, 0x26A7),
    (0x26AA, 0x26AB),
    (0x26B0, 0x26B1),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26C8, 0x26C8),
    (0x26CE, 0x26CF),
    (0x26D1, 0x26D1),
    (0x26D3, 0x26D4),
    (0x26E9, 0x26EA),
    (0x26F0, 0x26F5),
    (0x26F7, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2702, 0x2702),
    (0x2705, 0x2705),
    (0x2708, 0x270D),
    (0x270F, 0x270F),
    (0x2712, 0x2712),
    (0x2714, 0x2714),
    (0x2716, 0x2716),
    (0x271D, 0x271D),
    (0x2721, 0x2721),
    (0x2728, 0x2728),
    (0x2733, 0x2734),
    (0x2744, 0x2744),
    (0x2747, 0x2747),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2763, 0x2764),
    (0x2795, 0x2797),
    (0x27A1, 0x27A1),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x231A, 0x231B),
    (0x2328, 0x2328),
    (0x23CF, 0x23CF),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x2194, 0x2199),
    (0x21A9, 0x21AA),
    (0x25AA, 0x25AB),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x25FB, 0x25FE),
    (0x2934, 0x2935),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x00A9, 0x00A9),
    (0x00AE, 0x00AE),
    (0x203C, 0x203C),
    (0x2049, 0x2049),
    (0x2122, 0x2122),
    (0x2139, 0x2139),
    (0x24C2, 0x24C2),
)
_PRESENTATION = 0xFE0F  # "show as emoji" after a pictograph (e.g. a red heart is U+2764 U+FE0F)
_REGIONAL = range(0x1F1E6, 0x1F200)  # the letters of a flag
_SKIN_TONES = range(0x1F3FB, 0x1F400)  # modifiers: never an emoji on their own


def _pictograph(code: int) -> bool:
    if code in _REGIONAL or code in _SKIN_TONES or not unicodedata.name(chr(code), ""):
        return False  # a flag letter, a skin tone or an unassigned code point
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def is_single_emoji(value: str) -> bool:
    """One unicode emoji as a menu option can show it: one pictograph, optionally followed by U+FE0F, or a country's
    flag (two flag letters that form a country code). Emoji with a skin tone and emoji joined from several (U+200D
    sequences) are refused: whether such a combination exists cannot be told from its parts, and Discord refuses one
    it does not know."""
    codes = [ord(char) for char in value]
    if len(codes) == 2 and all(code in _REGIONAL for code in codes):
        return "".join(chr(ord("A") + code - _REGIONAL.start) for code in codes) in COUNTRIES
    if codes and codes[-1] == _PRESENTATION:
        codes.pop()
    return len(codes) == 1 and _pictograph(codes[0])


def normalize_name(name: str) -> str:
    text = " ".join(name.split())
    if not 1 <= len(text) <= NAME_MAX:
        raise InvalidCategory("name", f"must be 1-{NAME_MAX} characters")
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        raise InvalidCategory("name", "must not contain control characters")
    return text


def normalize_description(text: str) -> str:
    value = " ".join(text.split())
    if len(value) > DESCRIPTION_MAX:
        raise InvalidCategory("description", f"must be at most {DESCRIPTION_MAX} characters")
    return value


def normalize_emoji(text: str, icon_names: Collection[str] | None) -> str:
    """'' (none), ``{icon_<name>}`` for one of the bot's icons (written as ``tag``, ``:tag:``, ``icon_tag`` or
    ``{icon_tag}``), or one unicode emoji. ``icon_names``: the bot's icons (None while icons are switched off)."""
    value = text.strip()
    if not value:
        return ""
    match = _ICON_FORMS.match(value.lower())
    if match is not None:
        name = match.group(1) or match.group(2)
        if icon_names is not None and name in icon_names:
            return f"{{icon_{name}}}"
        raise InvalidCategory("emoji", "unknown icon")
    if is_single_emoji(value):
        return value
    raise InvalidCategory("emoji", "not one emoji")


def normalize_position(position: int) -> int:
    if isinstance(position, bool) or not isinstance(position, int) or abs(position) > POSITION_LIMIT:
        raise InvalidCategory("position", f"must be between {-POSITION_LIMIT} and {POSITION_LIMIT}")
    return position


def same_name(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


# ---------------------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------------------


def list_categories(conn: sqlite3.Connection, guild_id: int) -> list[Category]:
    """Every category of the server in menu order (position, then id)."""
    rows = conn.execute(
        "SELECT * FROM catalog_categories WHERE guild_id = ? ORDER BY position, id", (guild_id,)
    ).fetchall()
    return [_row(r) for r in rows]


def get_category(conn: sqlite3.Connection, guild_id: int, category_id: int) -> Category | None:
    row = conn.execute(
        "SELECT * FROM catalog_categories WHERE id = ? AND guild_id = ?", (category_id, guild_id)
    ).fetchone()
    return None if row is None else _row(row)


def find_by_name(conn: sqlite3.Connection, guild_id: int, name: str) -> Category | None:
    """The category named ``name`` ignoring case (also beyond ASCII), or None."""
    return next((c for c in list_categories(conn, guild_id) if same_name(c.name, name)), None)


def listed_products(conn: sqlite3.Connection, guild_id: int, category_id: int) -> list[Product]:
    """The listed (active, not archived) products of a category in listing order; ``ALL``: every listed product."""
    if category_id == ALL:
        sql = "SELECT * FROM products WHERE guild_id = ? AND active = 1 AND archived = 0 ORDER BY sort_order, id"
        rows = conn.execute(sql, (guild_id,)).fetchall()
    else:
        sql = (
            "SELECT p.* FROM products p JOIN product_categories pc ON pc.product_id = p.id "
            "WHERE p.guild_id = ? AND pc.category_id = ? AND p.active = 1 AND p.archived = 0 "
            "ORDER BY p.sort_order, p.id"
        )
        rows = conn.execute(sql, (guild_id, category_id)).fetchall()
    return [product_from_row(r) for r in rows]


def product_counts(conn: sqlite3.Connection, guild_id: int) -> dict[int, tuple[int, int]]:
    """(listed, hidden) products per category id: hidden ones are inactive (archived products are not counted)."""
    rows = conn.execute(
        "SELECT pc.category_id AS id, SUM(p.active) AS listed, SUM(1 - p.active) AS hidden "
        "FROM product_categories pc JOIN products p ON p.id = pc.product_id "
        "WHERE p.guild_id = ? AND p.archived = 0 GROUP BY pc.category_id",
        (guild_id,),
    ).fetchall()
    return {int(r["id"]): (int(r["listed"]), int(r["hidden"])) for r in rows}


def categories_of(conn: sqlite3.Connection, guild_id: int, product_id: int) -> list[Category]:
    """The categories ``product_id`` is in, in menu order."""
    rows = conn.execute(
        "SELECT c.* FROM catalog_categories c JOIN product_categories pc ON pc.category_id = c.id "
        "WHERE c.guild_id = ? AND pc.product_id = ? ORDER BY c.position, c.id",
        (guild_id, product_id),
    ).fetchall()
    return [_row(r) for r in rows]


def search(conn: sqlite3.Connection, guild_id: int, query: str) -> list[Category]:
    """Categories whose name contains ``query`` (ignoring case), in menu order; every category for ''."""
    wanted = query.strip().casefold()
    return [c for c in list_categories(conn, guild_id) if wanted in c.name.casefold()]


# ---------------------------------------------------------------------------------------------------
# Writes (inside the caller's transaction)
# ---------------------------------------------------------------------------------------------------


def _check_name_free(conn: sqlite3.Connection, guild_id: int, name: str, category_id: int | None = None) -> None:
    existing = find_by_name(conn, guild_id, name)
    if existing is not None and existing.id != category_id:
        raise DuplicateName(existing)


def create_category(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    name: str,
    emoji: str = "",
    description: str = "",
    position: int | None = None,
    actor_id: int | None,
    now: int,
) -> Category:
    """Create a category (fields already normalized). ``position`` None: after the last one."""
    count = conn.execute("SELECT COUNT(*) FROM catalog_categories WHERE guild_id = ?", (guild_id,)).fetchone()[0]
    if int(count) >= MAX_CATEGORIES:
        raise TooManyCategories()
    _check_name_free(conn, guild_id, name)
    if position is None:
        row = conn.execute("SELECT MAX(position) FROM catalog_categories WHERE guild_id = ?", (guild_id,)).fetchone()
        position = 1 if row[0] is None else min(int(row[0]) + 1, POSITION_LIMIT)
    cursor = conn.execute(
        "INSERT INTO catalog_categories (guild_id, name, emoji, description, position, created_by, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, name, emoji, description, normalize_position(position), actor_id, now, now),
    )
    created = get_category(conn, guild_id, int(cursor.lastrowid or 0))
    assert created is not None
    return created


def update_category(
    conn: sqlite3.Connection, guild_id: int, category_id: int, *, now: int, **fields: str | int
) -> tuple[Category, Category]:
    """Change some of ``EDITABLE_FIELDS`` (already normalized). Returns (before, after); unchanged values leave the
    row alone (after is before)."""
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown category fields: {sorted(unknown)}")
    before = get_category(conn, guild_id, category_id)
    if before is None:
        raise CategoryNotFound(category_id)
    changes = {k: v for k, v in fields.items() if getattr(before, k) != v}
    if not changes:
        return before, before
    if "name" in changes:
        _check_name_free(conn, guild_id, str(changes["name"]), category_id)
    if "position" in changes:
        changes["position"] = normalize_position(int(changes["position"]))
    assignments = ", ".join(f"{k} = ?" for k in sorted(changes))
    conn.execute(
        f"UPDATE catalog_categories SET {assignments}, updated_at = ? WHERE id = ?",  # noqa: S608 (fixed names)
        (*(changes[k] for k in sorted(changes)), now, category_id),
    )
    after = get_category(conn, guild_id, category_id)
    assert after is not None
    return before, after


def delete_category(conn: sqlite3.Connection, guild_id: int, category_id: int) -> tuple[Category, int]:
    """Delete a category and its product links (the products stay). Returns (the category, links removed)."""
    category = get_category(conn, guild_id, category_id)
    if category is None:
        raise CategoryNotFound(category_id)
    links = conn.execute("DELETE FROM product_categories WHERE category_id = ?", (category_id,)).rowcount
    conn.execute("DELETE FROM catalog_categories WHERE id = ?", (category_id,))
    return category, links


def add_product(conn: sqlite3.Connection, product_id: int, category_id: int) -> bool:
    """Put a product in a category; False when it already was."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO product_categories (product_id, category_id) VALUES (?, ?)", (product_id, category_id)
    )
    return cursor.rowcount > 0


def remove_product(conn: sqlite3.Connection, product_id: int, category_id: int) -> bool:
    """Take a product out of a category; False when it was not in it."""
    cursor = conn.execute(
        "DELETE FROM product_categories WHERE product_id = ? AND category_id = ?", (product_id, category_id)
    )
    return cursor.rowcount > 0
