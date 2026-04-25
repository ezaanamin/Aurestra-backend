-- Add device_sms_id to sms_history (idempotent — column already exists from migration 13)
ALTER TABLE sms_history
  ADD COLUMN IF NOT EXISTS device_sms_id VARCHAR(100) NULL;

CREATE INDEX IF NOT EXISTS idx_sms_history_device_sms_id ON sms_history(device_sms_id);
