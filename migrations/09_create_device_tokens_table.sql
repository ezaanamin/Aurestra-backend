DROP TABLE IF EXISTS device_tokens CASCADE;
CREATE TABLE device_tokens (
  id         SERIAL PRIMARY KEY,
  token      VARCHAR(255) NOT NULL UNIQUE,
  user_id    INTEGER      NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  last_seen  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_device_tokens_user_id ON device_tokens(user_id);

-- Trigger to auto-update last_seen on row change
CREATE OR REPLACE FUNCTION update_device_tokens_last_seen()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_seen = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_device_tokens_updated ON device_tokens;
CREATE TRIGGER trg_device_tokens_updated
  BEFORE UPDATE ON device_tokens
  FOR EACH ROW EXECUTE FUNCTION update_device_tokens_last_seen();
