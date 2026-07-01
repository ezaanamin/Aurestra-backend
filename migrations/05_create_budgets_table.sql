DROP TABLE IF EXISTS budgets CASCADE;
CREATE TABLE budgets (
  id             SERIAL PRIMARY KEY,
  month          VARCHAR(7) NOT NULL UNIQUE,
  total_budget   FLOAT      NOT NULL,
  needs          FLOAT      NOT NULL DEFAULT 0.0,
  wants          FLOAT      NOT NULL DEFAULT 0.0,
  saving         FLOAT      NOT NULL DEFAULT 0.0,
  total_expenses FLOAT      NOT NULL DEFAULT 0.0,
  created_at     TIMESTAMP  DEFAULT CURRENT_TIMESTAMP
);
