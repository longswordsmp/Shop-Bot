-- Tips for our team: an unfinished order (checkout session) keeps the tip the customer chose on the review step, and
-- an order keeps the tip it was placed with ([checkout.tips]). Amounts in minor units; 0 = no tip.
--
-- The tip is counted INSIDE orders.fees_minor (the extras and fees plus the tip), so the total CHECK of the orders
-- table (total = subtotal + delivery + fees - discount - credit) holds without rebuilding the table; tip_minor is the
-- tip's part of fees_minor, which the displays list on its own line. SQLite checks a CHECK of an added column against
-- the existing rows: they all get 0, which satisfies it.
ALTER TABLE checkout_sessions ADD COLUMN tip_minor INTEGER NOT NULL DEFAULT 0 CHECK (tip_minor >= 0);
ALTER TABLE orders ADD COLUMN tip_minor INTEGER NOT NULL DEFAULT 0 CHECK (tip_minor >= 0 AND tip_minor <= fees_minor);
