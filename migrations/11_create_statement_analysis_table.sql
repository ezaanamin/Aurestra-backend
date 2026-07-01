DROP TABLE IF EXISTS statement_analysis CASCADE;
CREATE TABLE statement_analysis (
  id                SERIAL PRIMARY KEY,
  month             VARCHAR(7)  NOT NULL UNIQUE,
  opening_balance   FLOAT       DEFAULT 0.0,
  closing_balance   FLOAT       DEFAULT 0.0,
  total_income      FLOAT       DEFAULT 0.0,
  total_expense     FLOAT       DEFAULT 0.0,
  net_result        FLOAT       DEFAULT 0.0,
  status            VARCHAR(20) NULL,
  breakdown_json    TEXT        NULL,
  analysis_date     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
  balance_applied   BOOLEAN     DEFAULT FALSE,
  reviewed_at       TIMESTAMP   NULL,
  statement_id      VARCHAR(64) NULL,
  transaction_ids   TEXT        NULL,
  processing_status VARCHAR(20) DEFAULT 'success',
  processing_notes  TEXT        NULL
);
