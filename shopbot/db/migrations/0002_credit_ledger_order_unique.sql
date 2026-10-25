-- At most one credit debit and one credit return per order, enforced by the database in addition to the
-- application's check-then-insert (defense in depth), and an index for order_id lookups.
CREATE UNIQUE INDEX uq_credit_ledger_order_kind ON credit_ledger (order_id, kind)
  WHERE kind IN ('order_debit', 'order_return');
