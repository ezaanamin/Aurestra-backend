-- Migration 23: Create user_backups table for per-user encrypted backup metadata
-- Each row tracks one backup file. File paths are never exposed via the API.

CREATE TABLE IF NOT EXISTS user_backups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    app_version TEXT    NOT NULL DEFAULT '1.0.0',
    db_version  INTEGER NOT NULL DEFAULT 1,
    enc_version TEXT    NOT NULL DEFAULT 'AES256GCM-v1',
    status      TEXT    NOT NULL DEFAULT 'completed',
    table_counts TEXT   NULL,      -- JSON: {"transactions": 42, "categories": 8, ...}
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_backups_user_id ON user_backups(user_id);
CREATE INDEX IF NOT EXISTS idx_user_backups_created_at ON user_backups(user_id, created_at DESC);
