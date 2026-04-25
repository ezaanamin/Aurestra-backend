-- Add is_deleted and is_spam flags to transactions (idempotent)
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_spam    BOOLEAN DEFAULT FALSE;

-- Remove duplicate transactions, keeping the row with the lowest id
DELETE FROM transactions
WHERE id NOT IN (
  SELECT MIN(id)
  FROM transactions
  GROUP BY date, amount, type, source
);
