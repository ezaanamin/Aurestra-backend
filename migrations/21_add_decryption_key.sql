-- Migration 21: Add decryption_key column to users table
ALTER TABLE users ADD COLUMN decryption_key VARCHAR(255) DEFAULT NULL;
