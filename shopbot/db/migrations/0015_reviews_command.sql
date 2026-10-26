-- migrate: foreign_keys=off
-- Reviews written with /review: a review without an order, how a review was written, and a durable copy of its photo.
--
-- SQLite cannot make a column nullable or change a CHECK constraint in place, so the reviews table is rebuilt with the
-- same columns and every row copied unchanged (all of them were written through the review request: source 'prompt').
-- order_id may now be NULL: an unverified review (no delivered order behind it), written with /review. It stays UNIQUE
-- for a real order (one review per order); several reviews without an order are allowed. An unverified review is never
-- a review request ('prompted') and always waits for a staff decision: it is never approved automatically.
-- The migration runs with foreign keys off (dropping the old table must not cascade) and the runner checks every
-- reference afterwards.
CREATE TABLE reviews_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  order_id INTEGER UNIQUE REFERENCES orders (id),
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
  -- How the review was written: 'prompt' = the review request sent after an order was delivered (rating menu and
  -- comment form), 'command' = /review (stars, description and an optional photo).
  source TEXT NOT NULL DEFAULT 'prompt' CHECK (source IN ('prompt', 'command')),
  CHECK (status NOT IN ('pending_approval', 'rejected') OR (rating IS NOT NULL AND comment IS NOT NULL)),
  CHECK (status != 'pending_approval' OR approval IS NULL),
  CHECK (status != 'rejected' OR coalesce(approval, '') = 'rejected'),
  CHECK (status != 'published' OR coalesce(approval, '') IN ('automatic', 'approved')),
  CHECK (status != 'rated' OR comment IS NULL OR coalesce(approval, '') IN ('automatic', 'approved')),
  CHECK (approval IS NULL OR (approval IN ('approved', 'rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  CHECK (decision_reason IS NULL OR coalesce(approval, '') = 'rejected'),
  CHECK ((approval_channel_id IS NULL) = (approval_message_id IS NULL)),
  CHECK (order_id IS NOT NULL OR source = 'command'),
  CHECK (order_id IS NOT NULL OR (status != 'prompted' AND coalesce(approval, '') != 'automatic'))
);

INSERT INTO reviews_v3 (
  id, guild_id, order_id, user_id, status, rating, comment, prompt_message_id, prompt_channel_id,
  published_message_id, approval, decided_by, decided_at, decision_reason, approval_channel_id, approval_message_id,
  created_at, updated_at, source
)
SELECT
  id, guild_id, order_id, user_id, status, rating, comment, prompt_message_id, prompt_channel_id,
  published_message_id, approval, decided_by, decided_at, decision_reason, approval_channel_id, approval_message_id,
  created_at, updated_at, 'prompt'
FROM reviews
ORDER BY id;
-- Keep the id sequence: review ids are part of the buttons and forms of review requests and approval messages, so an
-- id is never used twice. Dropping the old table deletes its sequence row; the rename carries this one over.
DELETE FROM sqlite_sequence WHERE name = 'reviews_v3';
INSERT INTO sqlite_sequence (name, seq) SELECT 'reviews_v3', seq FROM sqlite_sequence WHERE name = 'reviews';
DROP TABLE reviews;
ALTER TABLE reviews_v3 RENAME TO reviews;
CREATE INDEX idx_reviews_guild_status ON reviews (guild_id, status);
-- reviews.unverified_cooldown_days: a member's latest review without an order.
CREATE INDEX idx_reviews_unverified ON reviews (guild_id, user_id, created_at) WHERE order_id IS NULL;

-- The photo of a review written with /review. Discord's attachment links expire, and a review can wait days for a
-- staff decision, so the picture is downloaded when the review is submitted and kept here (like product_images) until
-- the review is published, rejected or removed: then data is cleared (released_at) and only what describes the
-- picture stays. One photo per review.
CREATE TABLE review_images (
  review_id INTEGER PRIMARY KEY REFERENCES reviews (id),
  guild_id INTEGER NOT NULL,
  content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg', 'image/gif', 'image/webp')),
  size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 10485760),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  data BLOB CHECK (data IS NULL OR (typeof(data) = 'blob' AND length(data) = size)),
  created_at INTEGER NOT NULL,
  released_at INTEGER,
  CHECK ((data IS NULL) = (released_at IS NOT NULL))
);
