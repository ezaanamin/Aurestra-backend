DROP TABLE IF EXISTS financial_insights CASCADE;
CREATE TABLE financial_insights (
  id           SERIAL PRIMARY KEY,
  month        VARCHAR(7) NOT NULL,
  content      TEXT       NOT NULL,
  metrics_json TEXT       NULL,
  tags         VARCHAR(255) NULL,
  created_at   TIMESTAMP  DEFAULT CURRENT_TIMESTAMP
);
