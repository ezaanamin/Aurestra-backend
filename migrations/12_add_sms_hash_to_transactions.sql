-- Add sms_hash column to transactions (idempotent)
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS sms_hash VARCHAR(64) NULL;

-- Index for fast sms_hash lookups
CREATE INDEX IF NOT EXISTS idx_transactions_sms_hash ON transactions(sms_hash);

-- Compound index for deduplication queries
CREATE INDEX IF NOT EXISTS idx_transactions_date_amount_type ON transactions(date, amount, type);
