-- Operations: staff handling of orders (claims, tracking details), internal order activity (claims, notes,
-- correction requests, tracking, rejection reasons), support-ticket reply tracking and reminder state.

-- At most one row per order; at most one claimer at a time (claiming is a conditional upsert).
CREATE TABLE order_handling (
  order_id INTEGER PRIMARY KEY REFERENCES orders (id) ON DELETE CASCADE,
  claimed_by INTEGER,
  claimed_at INTEGER,
  -- When the last claim was released (unclaimed reminders count from here instead of the submission time).
  unclaimed_at INTEGER,
  tracking TEXT CHECK (tracking IS NULL OR length(tracking) BETWEEN 1 AND 1000),
  tracking_by INTEGER,
  tracking_at INTEGER,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  updated_at INTEGER NOT NULL,
  CHECK ((claimed_by IS NULL) = (claimed_at IS NULL)),
  CHECK ((tracking IS NULL) = (tracking_at IS NULL))
);

-- Internal activity on an order, shown to staff only (the #orders embed and the details view). Status changes stay
-- in order_events; the text of 'rejection' and 'correction' rows is also sent to the customer by design.
CREATE TABLE order_activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('claim', 'unclaim', 'note', 'correction', 'tracking', 'rejection')),
  actor_id INTEGER,
  text TEXT CHECK (text IS NULL OR length(text) BETWEEN 1 AND 1000),
  created_at INTEGER NOT NULL,
  CHECK (kind IN ('claim', 'unclaim') OR text IS NOT NULL)
);
CREATE INDEX idx_order_activity_order ON order_activity (order_id, id);

-- Staff notes used to be order_events rows without a status change: move them, so order_events holds only status
-- changes (customer-facing timelines are built from it) and internal notes live with the other staff activity.
INSERT INTO order_activity (order_id, kind, actor_id, text, created_at)
  SELECT order_id, 'note', actor_id, substr(note, 1, 1000), created_at FROM order_events
  WHERE from_status IS NOT NULL AND from_status = to_status AND note IS NOT NULL AND length(note) > 0
  ORDER BY id;
DELETE FROM order_events WHERE from_status IS NOT NULL AND from_status = to_status AND note IS NOT NULL
  AND length(note) > 0;

-- Support tickets: whether the customer is waiting for a staff reply. No row = nobody from staff replied since the
-- ticket was opened (waiting since its creation); awaiting_since NULL = answered.
CREATE TABLE ticket_replies (
  ticket_id INTEGER PRIMARY KEY REFERENCES tickets (id) ON DELETE CASCADE,
  awaiting_since INTEGER,
  last_customer_message_at INTEGER,
  last_staff_reply_at INTEGER,
  updated_at INTEGER NOT NULL
);

-- Staff reminders already sent for one waiting period ('since') of a record (order or ticket id).
CREATE TABLE operations_reminders (
  kind TEXT NOT NULL CHECK (kind IN ('unclaimed', 'unpaid', 'unfulfilled', 'unanswered')),
  target_id INTEGER NOT NULL CHECK (target_id > 0),
  since INTEGER NOT NULL,
  sent_count INTEGER NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
  last_sent_at INTEGER,
  PRIMARY KEY (kind, target_id)
);
