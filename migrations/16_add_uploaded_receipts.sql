-- Migration 16: Add uploaded_receipts table and link to transactions
CREATE TABLE IF NOT EXISTS uploaded_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    mime_type VARCHAR(50),
    ocr_status VARCHAR(20) DEFAULT 'pending',
    ocr_raw_text TEXT,
    created_at DATETIME,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

ALTER TABLE transactions ADD COLUMN receipt_id INTEGER REFERENCES uploaded_receipts(id);
