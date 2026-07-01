import os
import sqlite3
from database import db, app
from sqlalchemy import text

def migrate():
    print("🔄 Starting Receipt migration...")
    with app.app_context():
        # 1. Create uploaded_receipts table if not exists
        create_table_sql = """
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
        """
        db.session.execute(text(create_table_sql))
        print("✅ Checked/Created uploaded_receipts table.")

        # 2. Check if receipt_id column already exists in transactions
        # We can check using pragma table_info in sqlite
        result = db.session.execute(text("PRAGMA table_info(transactions)")).fetchall()
        columns = [row[1] for row in result]
        
        if "receipt_id" not in columns:
            print("Adding receipt_id column to transactions table...")
            db.session.execute(text("ALTER TABLE transactions ADD COLUMN receipt_id INTEGER REFERENCES uploaded_receipts(id);"))
            print("✅ Successfully added receipt_id column.")
        else:
            print("ℹ️ receipt_id column already exists in transactions table. Skipping.")
            
        db.session.commit()
        print("✨ Migration completed successfully without modifying or removing any existing data!")

if __name__ == "__main__":
    migrate()
