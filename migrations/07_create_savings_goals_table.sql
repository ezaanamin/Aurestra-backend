DROP TABLE IF EXISTS savings_goals CASCADE;
CREATE TABLE savings_goals (
  id             SERIAL PRIMARY KEY,
  name           VARCHAR(100) NOT NULL,
  target_amount  FLOAT        NOT NULL,
  current_amount FLOAT        DEFAULT 0.0,
  emoji          VARCHAR(20)  DEFAULT '💰',
  deadline       DATE         NULL,
  created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
