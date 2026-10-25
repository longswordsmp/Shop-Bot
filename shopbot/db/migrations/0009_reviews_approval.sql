-- Reviews: staff approval (moderation) of submitted reviews and the decision record.
-- SQLite cannot change a CHECK constraint in place, so the reviews table is rebuilt with the two new statuses
-- ('pending_approval' = waiting for a staff decision, 'rejected' = staff rejected it) and the moderation columns.
-- Every existing row is copied unchanged. Reviews that were submitted or published before this migration were
-- published without moderation, so they are recorded as approved automatically.
CREATE TABLE reviews_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders (id),
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('prompted', 'rated', 'pending_approval', 'published', 'rejected', 'declined')),
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 10),
  comment TEXT,
  prompt_message_id INTEGER,
  prompt_channel_id INTEGER,
  published_message_id INTEGER,
  -- NULL = not decided yet, 'automatic' = approved without moderation (reviews.require_approval off),
  -- 'approved' / 'rejected' = a staff decision (decided_by, decided_at).
  approval TEXT CHECK (approval IS NULL OR approval IN ('automatic', 'approved', 'rejected')),
  decided_by INTEGER,
  decided_at INTEGER,
  decision_reason TEXT CHECK (decision_reason IS NULL OR length(decision_reason) BETWEEN 1 AND 500),
  -- The staff approval message (in reviews.approval_channel) of a review that needed approval.
  approval_channel_id INTEGER,
  approval_message_id INTEGER UNIQUE,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  CHECK (status NOT IN ('pending_approval', 'rejected') OR (rating IS NOT NULL AND comment IS NOT NULL)),
  CHECK (status != 'pending_approval' OR approval IS NULL),
  CHECK (status != 'rejected' OR coalesce(approval, '') = 'rejected'),
  CHECK (status != 'published' OR coalesce(approval, '') IN ('automatic', 'approved')),
  CHECK (status != 'rated' OR comment IS NULL OR coalesce(approval, '') IN ('automatic', 'approved')),
  CHECK (approval IS NULL OR (approval IN ('approved', 'rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  CHECK (decision_reason IS NULL OR coalesce(approval, '') = 'rejected'),
  CHECK ((approval_channel_id IS NULL) = (approval_message_id IS NULL))
);

INSERT INTO reviews_v2 (
  id, guild_id, order_id, user_id, status, rating, comment, prompt_message_id, prompt_channel_id,
  published_message_id, approval, created_at, updated_at
)
SELECT
  id, guild_id, order_id, user_id, status, rating, comment, prompt_message_id, prompt_channel_id,
  published_message_id,
  CASE WHEN status = 'published' OR (status = 'rated' AND comment IS NOT NULL) THEN 'automatic' END,
  created_at, updated_at
FROM reviews
ORDER BY id;

DROP TABLE reviews;

ALTER TABLE reviews_v2 RENAME TO reviews;

CREATE INDEX idx_reviews_guild_status ON reviews (guild_id, status);
