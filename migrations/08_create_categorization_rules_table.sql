DROP TABLE IF EXISTS categorization_rules CASCADE;
CREATE TABLE categorization_rules (
  id               SERIAL PRIMARY KEY,
  user_id          INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  merchant_pattern VARCHAR(255) NOT NULL,
  category_id      INTEGER      NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  hit_count        INTEGER      DEFAULT 0
);

CREATE INDEX idx_rules_user_id     ON categorization_rules(user_id);
CREATE INDEX idx_rules_category_id ON categorization_rules(category_id);
