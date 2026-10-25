-- The bot's custom icons, uploaded as application emojis (owned by the bot application, usable in every channel and
-- direct message). One row per icon name: the emoji id Discord assigned and the sha256 of the picture uploaded, so a
-- restart reuses the uploaded emoji and a changed picture is uploaded again.
CREATE TABLE app_emojis (
  name TEXT NOT NULL PRIMARY KEY CHECK (length(name) BETWEEN 2 AND 32),
  emoji_id INTEGER NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  updated_at INTEGER NOT NULL
);
