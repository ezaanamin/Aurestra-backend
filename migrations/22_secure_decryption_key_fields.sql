-- Migration 22: Add decryption_key_hash and decryption_key_salt columns to users table
ALTER TABLE users ADD COLUMN decryption_key_hash VARCHAR(255) DEFAULT NULL;
ALTER TABLE users ADD COLUMN decryption_key_salt VARCHAR(255) DEFAULT NULL;
