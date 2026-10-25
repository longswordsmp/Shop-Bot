-- /announce: one row per announcement form a staff member opened.
-- The form's custom id carries only the random token; the row holds the slash command's choices (image attachment
-- reference, mention role) so a submission still works after a restart. The status is claimed with a conditional
-- UPDATE ('draft' -> 'posting'), so one form can never be published twice. The typed text is kept only while the form
-- may still be retried after an error; it is cleared when the announcement is published, and unpublished rows are
-- deleted once they expire. Published rows keep only identifiers (channel and message), never the text.
CREATE TABLE announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE CHECK (length(token) BETWEEN 16 AND 64),
  actor_id INTEGER NOT NULL,
  mention_role TEXT,
  image_url TEXT,
  image_filename TEXT,
  image_content_type TEXT,
  image_size INTEGER CHECK (image_size IS NULL OR image_size >= 0),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'posting', 'published', 'interrupted')),
  claimed_by TEXT,
  title TEXT,
  message TEXT,
  field_values TEXT,
  channel_id INTEGER,
  message_id INTEGER UNIQUE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  published_at INTEGER,
  CHECK (expires_at > created_at),
  CHECK ((image_url IS NULL) = (image_filename IS NULL)),
  CHECK (status != 'posting' OR claimed_by IS NOT NULL),
  CHECK (status != 'published' OR (channel_id IS NOT NULL AND message_id IS NOT NULL AND published_at IS NOT NULL))
);
CREATE INDEX idx_announcements_expiry ON announcements (guild_id, status, expires_at);
