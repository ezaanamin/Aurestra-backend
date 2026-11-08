-- Migration: Add is_retracted column to categories table
ALTER TABLE categories ADD COLUMN is_retracted BOOLEAN DEFAULT 0;
