-- Catalog categories (/category): groups of products that catalog managers make and customers pick from the menu on
-- the #catalog header card to see those products in a private message. The built-in "All" choice is not stored.
--
-- name: 1 to 50 characters, unique per server ignoring case (the service compares case-folded names; the index on
-- lower(name) backs it for ASCII names). emoji: one of the bot's icons written as {icon_<name>}, or a unicode emoji
-- ('' = none). description: shown under the name in the menu ('' = none). position orders the menu (then id).
CREATE TABLE catalog_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 50),
  emoji TEXT NOT NULL DEFAULT '' CHECK (length(emoji) <= 40),
  description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 100),
  position INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_catalog_categories_name ON catalog_categories (guild_id, lower(name));

-- The categories of each product (a product can be in several). Deleting a category removes only its links: the
-- products stay.
CREATE TABLE product_categories (
  product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES catalog_categories (id) ON DELETE CASCADE,
  PRIMARY KEY (product_id, category_id)
);
CREATE INDEX idx_product_categories_category ON product_categories (category_id);
