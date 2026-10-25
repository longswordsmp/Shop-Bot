-- Automatic product updates in #new-arrivals: one row per qualifying product event. The UNIQUE key makes a second
-- announcement of the same event (same product, same kind, same product version) impossible, even if the scheduled
-- task runs twice. A row is 'claimed' before the update is posted and 'posted' once its message id is recorded.
CREATE TABLE new_arrival_announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL REFERENCES products (id),
  event TEXT NOT NULL CHECK (event IN ('new', 'restock')),
  product_version INTEGER NOT NULL CHECK (product_version >= 1),
  status TEXT NOT NULL CHECK (status IN ('claimed', 'posted')),
  channel_id INTEGER,
  message_id INTEGER,
  mentioned INTEGER NOT NULL DEFAULT 0 CHECK (mentioned IN (0, 1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (guild_id, product_id, event, product_version),
  UNIQUE (message_id),
  CHECK ((status = 'posted') = (message_id IS NOT NULL AND channel_id IS NOT NULL))
);
CREATE INDEX idx_new_arrival_announcements_product ON new_arrival_announcements (guild_id, product_id);
