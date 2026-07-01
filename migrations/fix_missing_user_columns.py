#!/usr/bin/env python3
import sqlite3
import os
import sys

def get_db_path():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(backend_dir, '.env')
    sqlite_path = None
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith('SQLITE_PATH='):
                    sqlite_path = line.split('=', 1)[1].strip()
                    break
    if not sqlite_path:
        sqlite_path = 'aurestra.db'
    if not os.path.isabs(sqlite_path):
        sqlite_path = os.path.join(backend_dir, sqlite_path)
    return sqlite_path

def migrate():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable WAL mode for safety
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    
    cursor.execute("PRAGMA table_info(users)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    
    expected_cols = {
        "full_name": "VARCHAR(100)",
        "avatar_url": "VARCHAR(512)",
        "name": "VARCHAR(100)",
        "profile_picture": "VARCHAR(512)",
        "google_id": "VARCHAR(50)",
        "google_email": "VARCHAR(120)",
        "google_refresh_token": "VARCHAR(255)",
        "created_at": "DATETIME",
        "notifications_enabled": "BOOLEAN DEFAULT 1"
    }
    
    added = []
    for col, ddl in expected_cols.items():
        if col not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                conn.commit()
                added.append(col)
                print(f"✅ Added users.{col}")
            except Exception as e:
                print(f"❌ Error adding {col}: {e}")
                
    if not added:
        print("✅ All columns already exist in users table.")
    else:
        print(f"✅ Successfully added columns: {', '.join(added)}")
        
    conn.close()

if __name__ == '__main__':
    migrate()
