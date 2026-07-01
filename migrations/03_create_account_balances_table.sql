DROP TABLE IF EXISTS account_balances CASCADE;
CREATE TABLE account_balances (
  id              SERIAL PRIMARY KEY,
  source          VARCHAR(50) NOT NULL UNIQUE,
  current_balance FLOAT       NOT NULL DEFAULT 0.0,
  last_updated    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  is_manual       BOOLEAN     DEFAULT FALSE
);

-- Trigger to auto-update last_updated on row change (replaces MySQL's ON UPDATE)
CREATE OR REPLACE FUNCTION update_account_balances_last_updated()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_account_balances_updated ON account_balances;
CREATE TRIGGER trg_account_balances_updated
  BEFORE UPDATE ON account_balances
  FOR EACH ROW EXECUTE FUNCTION update_account_balances_last_updated();
