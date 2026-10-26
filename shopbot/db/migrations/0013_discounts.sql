-- migrate: foreign_keys=off
-- Discount codes: a code customers type on the review step (or apply with a coupon they claimed), the products it is
-- limited to, the customers who claimed it, and on every order the code it used and the amount it took off.
--
-- code: normalized (upper case; A-Z, 0-9, '-' and '_'; 3 to 32 characters). kind and value: 'percent' (value in basis
-- points, 100 = 1%, up to 10000 = 100%), 'amount' (value in minor units) or 'bogo' (buy one, get one free; value 0).
-- min_subtotal_minor: the items subtotal the cart needs. max_uses (NULL = no limit) and once_per_customer are counted
-- from the orders that used the code (orders.discount_code_id), never kept as a counter. A code is never deleted:
-- ending it sets ended_at, and the same code can then be created again.
CREATE TABLE discount_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  code TEXT NOT NULL CHECK (length(code) BETWEEN 3 AND 32 AND code NOT GLOB '*[^A-Z0-9_-]*'),
  kind TEXT NOT NULL CHECK (kind IN ('percent', 'amount', 'bogo')),
  value INTEGER NOT NULL,
  min_subtotal_minor INTEGER NOT NULL DEFAULT 0 CHECK (min_subtotal_minor >= 0),
  max_uses INTEGER CHECK (max_uses IS NULL OR max_uses > 0),
  once_per_customer INTEGER NOT NULL DEFAULT 1 CHECK (once_per_customer IN (0, 1)),
  expires_at INTEGER,
  created_by INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  ended_at INTEGER,
  ended_by INTEGER,
  CHECK (kind != 'percent' OR value BETWEEN 100 AND 10000),
  CHECK (kind != 'amount' OR value BETWEEN 1 AND 1000000000000),
  CHECK (kind != 'bogo' OR value = 0)
);
-- One live code per name; an ended code no longer counts, so the name can be used again.
CREATE UNIQUE INDEX uq_discount_codes_live ON discount_codes (guild_id, code) WHERE ended_at IS NULL;
CREATE INDEX idx_discount_codes_guild ON discount_codes (guild_id, ended_at);

-- The products a code is limited to (no row: every item counts).
CREATE TABLE discount_code_products (
  code_id INTEGER NOT NULL REFERENCES discount_codes (id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products (id),
  PRIMARY KEY (code_id, product_id)
);

-- Coupons customers claimed (the claim button of an announcement): offered as a one-click button on the review step.
CREATE TABLE discount_claims (
  code_id INTEGER NOT NULL REFERENCES discount_codes (id) ON DELETE CASCADE,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  claimed_at INTEGER NOT NULL,
  PRIMARY KEY (code_id, user_id)
);
CREATE INDEX idx_discount_claims_user ON discount_claims (guild_id, user_id, claimed_at);

-- The code an unfinished order (checkout session) applied; it is checked again every time the review is shown.
ALTER TABLE checkout_sessions ADD COLUMN discount_code_id INTEGER REFERENCES discount_codes (id);

-- Orders keep the code they used: its id, the code and its kind as they were (a snapshot), and the amount taken off
-- the items. SQLite cannot change a CHECK constraint in place, so the orders table is rebuilt with the same columns
-- plus these, and every row copied unchanged (existing orders get no discount). Other tables refer to orders by
-- name, so their references resolve to the new table; the migration runs with foreign keys off (dropping the old
-- table must not cascade) and the runner checks every reference afterwards.
CREATE TABLE orders_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  number INTEGER NOT NULL CHECK (number > 0),
  user_id INTEGER NOT NULL,
  session_id INTEGER UNIQUE REFERENCES checkout_sessions (id),
  ticket_id INTEGER REFERENCES tickets (id),
  status TEXT NOT NULL CHECK (status IN (
    'pending_payment', 'payment_submitted', 'paid', 'processing', 'shipped', 'fulfilled', 'cancelled', 'rejected',
    'expired', 'refunded'
  )),
  currency TEXT NOT NULL CHECK (length(currency) = 3),
  subtotal_minor INTEGER NOT NULL CHECK (subtotal_minor >= 0),
  fulfillment_fee_minor INTEGER NOT NULL DEFAULT 0 CHECK (fulfillment_fee_minor >= 0),
  fees_minor INTEGER NOT NULL DEFAULT 0 CHECK (fees_minor >= 0),
  credits_applied_minor INTEGER NOT NULL DEFAULT 0 CHECK (credits_applied_minor >= 0),
  total_minor INTEGER NOT NULL CHECK (total_minor >= 0),
  refunded_minor INTEGER NOT NULL DEFAULT 0 CHECK (refunded_minor >= 0),
  fulfillment_option TEXT,
  details TEXT NOT NULL DEFAULT '{}',
  payment_provider TEXT NOT NULL,
  customer_message_id INTEGER,
  staff_channel_id INTEGER,
  staff_message_id INTEGER,
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  paid_at INTEGER,
  fulfilled_at INTEGER,
  closed_at INTEGER,
  discount_code_id INTEGER REFERENCES discount_codes (id),
  discount_code TEXT,
  discount_kind TEXT CHECK (discount_kind IS NULL OR discount_kind IN ('percent', 'amount', 'bogo')),
  discount_minor INTEGER NOT NULL DEFAULT 0 CHECK (discount_minor >= 0 AND discount_minor <= subtotal_minor),
  UNIQUE (guild_id, number),
  CHECK ((discount_code IS NULL) = (discount_kind IS NULL)),
  CHECK (discount_minor = 0 OR discount_code IS NOT NULL),
  CHECK (total_minor = subtotal_minor + fulfillment_fee_minor + fees_minor - discount_minor - credits_applied_minor),
  CHECK (refunded_minor <= total_minor)
);
INSERT INTO orders_v3 (
  id, guild_id, number, user_id, session_id, ticket_id, status, currency, subtotal_minor, fulfillment_fee_minor,
  fees_minor, credits_applied_minor, total_minor, refunded_minor, fulfillment_option, details, payment_provider,
  customer_message_id, staff_channel_id, staff_message_id, version, created_at, updated_at, paid_at, fulfilled_at,
  closed_at
)
SELECT
  id, guild_id, number, user_id, session_id, ticket_id, status, currency, subtotal_minor, fulfillment_fee_minor,
  fees_minor, credits_applied_minor, total_minor, refunded_minor, fulfillment_option, details, payment_provider,
  customer_message_id, staff_channel_id, staff_message_id, version, created_at, updated_at, paid_at, fulfilled_at,
  closed_at
FROM orders;
-- Keep the id sequence: new orders continue after every id ever used (ids are never reused, also after /reset or when
-- the newest orders were deleted). Dropping the old table deletes its sequence row; the rename carries this one over.
DELETE FROM sqlite_sequence WHERE name = 'orders_v3';
INSERT INTO sqlite_sequence (name, seq) SELECT 'orders_v3', seq FROM sqlite_sequence WHERE name = 'orders';
DROP TABLE orders;
ALTER TABLE orders_v3 RENAME TO orders;
CREATE INDEX idx_orders_user ON orders (guild_id, user_id, status);
CREATE INDEX idx_orders_status ON orders (guild_id, status, created_at);
-- Counting a code's uses (and a customer's uses of it).
CREATE INDEX idx_orders_discount ON orders (discount_code_id, user_id) WHERE discount_code_id IS NOT NULL;

-- /announce presets ([[announcements.presets]]): an announcement posted from a preset keeps its preset id and the
-- discount code its claim button claims, so the button can be switched to "offer ended" once the code ends or
-- expires (offer_ended_at: when that was done). A preset's preview (a draft) keeps the code it showed, and Post
-- refuses another one; one preset announces one code once (a second Post of the same offer is refused while the first
-- is posting or published). /reset may delete discount codes while announcements are kept: the reference is then
-- cleared.
ALTER TABLE announcements ADD COLUMN preset_id TEXT;
ALTER TABLE announcements ADD COLUMN discount_code_id INTEGER REFERENCES discount_codes (id) ON DELETE SET NULL;
ALTER TABLE announcements ADD COLUMN offer_ended_at INTEGER;
CREATE UNIQUE INDEX uq_announcements_offer ON announcements (guild_id, preset_id, discount_code_id)
  WHERE discount_code_id IS NOT NULL AND status IN ('posting', 'published');
CREATE INDEX idx_announcements_code ON announcements (discount_code_id) WHERE discount_code_id IS NOT NULL;
