-- Problem reports (the #purchase "Report a Problem" workflow). Each report has exactly one private ticket: a support
-- ticket (tickets.kind = 'support') in the reserved category 'problem-report', created through the ticket service.
-- A report is decided exactly once: open -> resolved | rejected (conditional UPDATE in the application; the CHECKs
-- below make a half-decided row impossible). ``nonce`` identifies one run of the private report flow, so the same
-- form submitted twice can never create two reports.
CREATE TABLE problem_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  nonce TEXT NOT NULL CHECK (length(nonce) BETWEEN 8 AND 64),
  order_id INTEGER REFERENCES orders (id),
  issue_type TEXT NOT NULL CHECK (length(issue_type) BETWEEN 1 AND 32),
  answers TEXT NOT NULL DEFAULT '{}',
  ticket_id INTEGER NOT NULL UNIQUE REFERENCES tickets (id),
  panel_message_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'rejected')),
  reason TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 1000),
  decided_by INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  decided_at INTEGER,
  UNIQUE (guild_id, user_id, nonce),
  CHECK ((status = 'open') = (decided_at IS NULL)),
  CHECK ((status = 'open') = (decided_by IS NULL)),
  CHECK (status != 'open' OR reason IS NULL)
);
CREATE INDEX idx_problem_reports_user ON problem_reports (guild_id, user_id, status);
CREATE INDEX idx_problem_reports_order ON problem_reports (order_id);
