-- Initial schema. All timestamps are UNIX seconds (UTC). All money is integer minor units.

CREATE TABLE kv_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

-- Roles, categories, and channels created or adopted by /setup, keyed by the internal settings key.
CREATE TABLE guild_resources (
  guild_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('role', 'category', 'channel')),
  key TEXT NOT NULL,
  discord_id INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (guild_id, kind, key)
);

-- Messages the bot maintains (panels, catalog listings). Edited in place; never duplicated.
CREATE TABLE published_messages (
  guild_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (guild_id, key)
);

-- Content values entered through /content (take precedence over config/content.toml).
CREATE TABLE content_overrides (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_by INTEGER,
  updated_at INTEGER NOT NULL
);

CREATE TABLE counters (
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  value INTEGER NOT NULL DEFAULT 0 CHECK (value >= 0),
  PRIMARY KEY (guild_id, name)
);

CREATE TABLE users (
  user_id INTEGER PRIMARY KEY,
  first_seen_at INTEGER NOT NULL,
  dm_unavailable_at INTEGER
);

-- Private channels: support tickets (kind='support') and checkout/order channels (kind='order').
CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  number INTEGER NOT NULL CHECK (number > 0),
  kind TEXT NOT NULL CHECK (kind IN ('support', 'order')),
  category_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  channel_id INTEGER UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('creating', 'open', 'closed', 'deleted', 'failed')),
  assigned_staff_id INTEGER,
  form_answers TEXT NOT NULL DEFAULT '{}',
  control_message_id INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_activity_at INTEGER NOT NULL,
  closed_at INTEGER,
  closed_by INTEGER,
  close_reason TEXT,
  deleted_at INTEGER,
  transcript_status TEXT NOT NULL DEFAULT 'none'
    CHECK (transcript_status IN ('none', 'pending', 'done', 'failed', 'skipped')),
  transcript_message_id INTEGER,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (guild_id, number)
);
CREATE INDEX idx_tickets_owner ON tickets (guild_id, user_id, kind, status);
CREATE INDEX idx_tickets_status ON tickets (status, kind);

CREATE TABLE ticket_participants (
  ticket_id INTEGER NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  added_by INTEGER NOT NULL,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (ticket_id, user_id)
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
  price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
  image_url TEXT CHECK (image_url IS NULL OR length(image_url) <= 2048),
  stock_on_hand INTEGER CHECK (stock_on_hand IS NULL OR stock_on_hand >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  max_per_order INTEGER CHECK (max_per_order IS NULL OR max_per_order > 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  CHECK (stock_on_hand IS NULL OR reserved <= stock_on_hand)
);
CREATE INDEX idx_products_listing ON products (guild_id, archived, active, sort_order, id);

CREATE TABLE checkout_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  ticket_id INTEGER NOT NULL REFERENCES tickets (id),
  status TEXT NOT NULL CHECK (status IN ('active', 'submitted', 'cancelled', 'expired')),
  step TEXT NOT NULL CHECK (step IN ('browse', 'cart', 'fulfillment', 'details', 'review', 'done')),
  page INTEGER NOT NULL DEFAULT 0 CHECK (page >= 0),
  fulfillment_option TEXT,
  details TEXT NOT NULL DEFAULT '{}',
  apply_credits INTEGER NOT NULL DEFAULT 0 CHECK (apply_credits IN (0, 1)),
  message_id INTEGER,
  order_id INTEGER REFERENCES orders (id),
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER
);
CREATE UNIQUE INDEX uq_active_session_per_ticket ON checkout_sessions (ticket_id) WHERE status = 'active';
CREATE INDEX idx_sessions_user ON checkout_sessions (guild_id, user_id, status);

CREATE TABLE cart_items (
  session_id INTEGER NOT NULL REFERENCES checkout_sessions (id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products (id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  added_at INTEGER NOT NULL,
  PRIMARY KEY (session_id, product_id)
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  number INTEGER NOT NULL CHECK (number > 0),
  user_id INTEGER NOT NULL,
  session_id INTEGER UNIQUE REFERENCES checkout_sessions (id),
  ticket_id INTEGER REFERENCES tickets (id),
  status TEXT NOT NULL CHECK (status IN (
    'pending_payment', 'paid', 'processing', 'fulfilled', 'cancelled', 'rejected', 'expired', 'refunded'
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
CREATE INDEX idx_orders_user ON orders (guild_id, user_id, status);
CREATE INDEX idx_orders_status ON orders (guild_id, status, created_at);

CREATE TABLE order_items (
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products (id),
  name_snapshot TEXT NOT NULL,
  unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  line_total_minor INTEGER NOT NULL CHECK (line_total_minor = unit_price_minor * quantity),
  PRIMARY KEY (order_id, product_id)
);

CREATE TABLE order_fees (
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  fee_id TEXT NOT NULL,
  amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
  PRIMARY KEY (order_id, fee_id)
);

CREATE TABLE order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  from_status TEXT,
  to_status TEXT NOT NULL,
  actor_id INTEGER,
  note TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_order_events_order ON order_events (order_id, id);

CREATE TABLE order_adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('refund', 'credit_return')),
  method TEXT NOT NULL CHECK (method IN ('provider', 'manual', 'credits')),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  reason TEXT,
  actor_id INTEGER,
  external_ref TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE inventory_reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products (id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  status TEXT NOT NULL CHECK (status IN ('held', 'committed', 'released')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (order_id, product_id)
);
CREATE INDEX idx_reservations_product ON inventory_reservations (product_id, status);

CREATE TABLE payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'cancelled')),
  amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
  currency TEXT NOT NULL,
  external_ref TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (provider, external_ref)
);
CREATE INDEX idx_payments_order ON payments (order_id);

CREATE TABLE credit_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  delta_minor INTEGER NOT NULL CHECK (delta_minor != 0),
  kind TEXT NOT NULL CHECK (kind IN ('admin_adjust', 'order_debit', 'order_return', 'refund_to_credit')),
  order_id INTEGER REFERENCES orders (id),
  actor_id INTEGER,
  note TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_credit_ledger_user ON credit_ledger (guild_id, user_id);

CREATE TABLE reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders (id),
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('prompted', 'rated', 'published', 'declined')),
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 10),
  comment TEXT,
  prompt_message_id INTEGER,
  prompt_channel_id INTEGER,
  published_message_id INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE scheduled_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  run_at INTEGER NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
  dedupe_key TEXT UNIQUE,
  last_error TEXT,
  locked_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX idx_tasks_due ON scheduled_tasks (status, run_at);

CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT NOT NULL UNIQUE,
  user_id INTEGER,
  channel_id INTEGER,
  kind TEXT NOT NULL,
  delivery TEXT NOT NULL CHECK (delivery IN ('dm', 'channel', 'none')),
  status TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
  error TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  actor_id INTEGER,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  details TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_audit_guild_time ON audit_events (guild_id, created_at);
CREATE INDEX idx_audit_target ON audit_events (target_type, target_id);
