-- migrate: foreign_keys=off
-- Gift card payments (payments.provider = "gift_card"): every card a customer submits for an order, and a store
-- credit kind for the value of cards that is not used by an order (an overpayment, or cards redeemed for an order
-- that was cancelled afterwards).
--
-- The code is stored only encrypted (sealed, a Fernet token; NULL once deleted) next to a keyed fingerprint that
-- refuses the same card while it is waiting or was redeemed. last4: the code's last characters, shown to tell cards
-- apart. status: 'submitted' (waiting for staff), 'redeemed', 'refused' (reason: shown to the customer) or
-- 'withdrawn' (the order ended before staff decided).
CREATE TABLE gift_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL REFERENCES orders (id),
  user_id INTEGER NOT NULL,
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
  sealed BLOB,
  last4 TEXT NOT NULL CHECK (length(last4) BETWEEN 1 AND 4),
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'redeemed', 'refused', 'withdrawn')),
  reason TEXT CHECK (reason IS NULL OR length(reason) <= 500),
  submitted_at INTEGER NOT NULL,
  reveal_count INTEGER NOT NULL DEFAULT 0,
  revealed_by INTEGER,
  revealed_at INTEGER,
  decided_by INTEGER,
  decided_at INTEGER,
  erased_at INTEGER,
  CHECK (status = 'submitted' OR sealed IS NULL)
);
CREATE INDEX idx_gift_cards_order ON gift_cards (order_id, status);
CREATE UNIQUE INDEX uq_gift_cards_fingerprint ON gift_cards (guild_id, fingerprint)
  WHERE status IN ('submitted', 'redeemed');
CREATE INDEX idx_gift_cards_stored ON gift_cards (submitted_at) WHERE sealed IS NOT NULL;

-- The credit ledger gains the 'gift_card' kind (positive, for one order at most once). SQLite cannot change a CHECK
-- constraint in place: the table is rebuilt with the same columns and every row copied unchanged.
CREATE TABLE credit_ledger_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  delta_minor INTEGER NOT NULL CHECK (delta_minor != 0),
  kind TEXT NOT NULL CHECK (kind IN ('admin_adjust', 'order_debit', 'order_return', 'refund_to_credit', 'gift_card')),
  order_id INTEGER REFERENCES orders (id),
  actor_id INTEGER,
  note TEXT,
  created_at INTEGER NOT NULL
);
INSERT INTO credit_ledger_v2 (id, guild_id, user_id, delta_minor, kind, order_id, actor_id, note, created_at)
SELECT id, guild_id, user_id, delta_minor, kind, order_id, actor_id, note, created_at FROM credit_ledger;
DROP TABLE credit_ledger;
ALTER TABLE credit_ledger_v2 RENAME TO credit_ledger;
CREATE INDEX idx_credit_ledger_user ON credit_ledger (guild_id, user_id);
CREATE UNIQUE INDEX uq_credit_ledger_order_kind ON credit_ledger (order_id, kind)
  WHERE kind IN ('order_debit', 'order_return', 'gift_card');
