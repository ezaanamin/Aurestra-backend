DROP TABLE IF EXISTS monthly_balances CASCADE;
CREATE TABLE monthly_balances (
  id              SERIAL PRIMARY KEY,
  source          VARCHAR(20) NOT NULL,
  month           VARCHAR(7)  NOT NULL UNIQUE,
  opening_balance FLOAT       NOT NULL,
  closing_balance FLOAT       NOT NULL,
  expense         FLOAT       NULL,
  savings         FLOAT       NULL,
  fetched_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
