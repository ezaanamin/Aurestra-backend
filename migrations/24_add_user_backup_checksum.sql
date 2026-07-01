-- Add checksum column to user_backups table for integrity verification.
ALTER TABLE user_backups ADD COLUMN checksum VARCHAR(64) DEFAULT NULL;
