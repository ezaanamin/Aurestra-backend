-- Create sms_history table directly (skips intermediate sms_messages name)
CREATE TABLE IF NOT EXISTS sms_history (
  id               SERIAL PRIMARY KEY,
  device_sms_id    VARCHAR(100) NULL,
  sender           VARCHAR(50)  NULL,
  body             TEXT         NULL,
  device_timestamp TIMESTAMP    NULL,
  sms_hash         VARCHAR(64)  NOT NULL UNIQUE,
  status           VARCHAR(20)  DEFAULT 'pending',
  created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sms_history_status ON sms_history(status);
