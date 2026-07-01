DROP TABLE IF EXISTS transactions CASCADE;
CREATE TABLE transactions (
  id                   SERIAL PRIMARY KEY,
  source               VARCHAR(20)  NOT NULL,
  date                 TIMESTAMP    NOT NULL,
  purpose              VARCHAR(255) NULL,
  amount               FLOAT        NOT NULL,
  sender               VARCHAR(255) NULL,
  receiver             VARCHAR(255) NULL,
  transaction_id       VARCHAR(50)  NULL UNIQUE,
  transaction_hash     VARCHAR(64)  NULL UNIQUE,
  notes                VARCHAR(255) NULL,
  type                 VARCHAR(10)  NOT NULL,
  categorization_status VARCHAR(20) DEFAULT 'pending',
  category_id          INTEGER      NULL REFERENCES categories(id) ON DELETE SET NULL ON UPDATE CASCADE,
  created_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_transactions_category_id ON transactions(category_id);
