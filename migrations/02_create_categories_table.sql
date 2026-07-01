DROP TABLE IF EXISTS categories CASCADE;
CREATE TABLE categories (
  id         SERIAL PRIMARY KEY,
  name       VARCHAR(100) NOT NULL UNIQUE,
  icon       VARCHAR(50)  NOT NULL DEFAULT 'cash',
  color      VARCHAR(20)  NOT NULL DEFAULT '#64748B',
  cat_type   VARCHAR(20)  NOT NULL DEFAULT 'spending',
  is_default BOOLEAN      DEFAULT FALSE
);
