-- Verification (#welcome "I agree" button): one row per member who accepted the server rules.
-- The Member role in Discord stays the source of truth; this table is history (first/last acceptance and how
-- many times the role had to be granted, e.g. after leaving and rejoining) and keys the optional confirmation DM
-- so it is sent at most once per acceptance.
CREATE TABLE verification_acceptances (
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  first_accepted_at INTEGER NOT NULL CHECK (first_accepted_at >= 0),
  last_accepted_at INTEGER NOT NULL CHECK (last_accepted_at >= first_accepted_at),
  acceptances INTEGER NOT NULL DEFAULT 1 CHECK (acceptances >= 1),
  PRIMARY KEY (guild_id, user_id)
);
