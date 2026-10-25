-- migrate: foreign_keys=off
-- Orders: two new statuses for the shop's order flow, and the shipment details staff enter when an order ships.
--   'payment_submitted' - the customer submitted their payment (a gift card); our team is verifying it.
--   'shipped'           - the order is on its way (carrier and tracking number below); 'fulfilled' now means
--                          delivered.
-- SQLite cannot change a CHECK constraint in place, so the orders table is rebuilt with the same columns and every
-- row copied unchanged. Other tables refer to orders by name, so their references resolve to the new table; the
-- migration runs with foreign keys off (dropping the old table must not cascade) and the runner checks every
-- reference afterwards.
CREATE TABLE orders_v2 (
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
  UNIQUE (guild_id, number),
  CHECK (total_minor = subtotal_minor + fulfillment_fee_minor + fees_minor - credits_applied_minor),
  CHECK (refunded_minor <= total_minor)
);
INSERT INTO orders_v2 (
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
DROP TABLE orders;
ALTER TABLE orders_v2 RENAME TO orders;
CREATE INDEX idx_orders_user ON orders (guild_id, user_id, status);
CREATE INDEX idx_orders_status ON orders (guild_id, status, created_at);

-- The shipment: carrier, tracking number and an optional tracking link (entered with Mark shipped). The free-text
-- tracking details staff could already record stay as they are.
ALTER TABLE order_handling ADD COLUMN carrier TEXT CHECK (carrier IS NULL OR length(carrier) BETWEEN 1 AND 100);
ALTER TABLE order_handling ADD COLUMN tracking_number TEXT
  CHECK (tracking_number IS NULL OR length(tracking_number) BETWEEN 1 AND 100);
ALTER TABLE order_handling ADD COLUMN tracking_url TEXT
  CHECK (tracking_url IS NULL OR (length(tracking_url) <= 500 AND tracking_url LIKE 'https://%'));
ALTER TABLE order_handling ADD COLUMN shipped_at INTEGER;
