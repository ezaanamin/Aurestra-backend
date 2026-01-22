"""
Create statement_analysis table if it doesn't exist
"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'aurestra.db')

print(f"🔄 Checking database: {db_path}")

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check existing tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"📋 Existing tables: {tables}")
    
    # Create statement_analysis table if it doesn't exist
    if 'statement_analysis' not in tables:
        print("➕ Creating statement_analysis table...")
        cursor.execute("""
            CREATE TABLE statement_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month VARCHAR(7) NOT NULL UNIQUE,
                opening_balance FLOAT DEFAULT 0.0,
                closing_balance FLOAT DEFAULT 0.0,
                total_income FLOAT DEFAULT 0.0,
                total_expense FLOAT DEFAULT 0.0,
                net_result FLOAT DEFAULT 0.0,
                status VARCHAR(20),
                breakdown_json TEXT,
                analysis_date DATETIME,
                balance_applied BOOLEAN DEFAULT 0,
                reviewed_at DATETIME,
                statement_id VARCHAR(64),
                processing_status VARCHAR(20) DEFAULT 'success',
                processing_notes TEXT
            )
        """)
        print("✅ Created statement_analysis table")
    else:
        print("⏭️  statement_analysis table already exists")
        
        # Check if new columns exist
        cursor.execute("PRAGMA table_info(statement_analysis)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'processing_status' not in columns:
            cursor.execute("ALTER TABLE statement_analysis ADD COLUMN processing_status VARCHAR(20) DEFAULT 'success'")
            print("✅ Added processing_status column")
        
        if 'processing_notes' not in columns:
            cursor.execute("ALTER TABLE statement_analysis ADD COLUMN processing_notes TEXT")
            print("✅ Added processing_notes column")
    
    conn.commit()
    conn.close()
    
    print("\n✅ Database schema updated successfully!")
    print("🔄 Now restart your backend and try 'Calculate Statement' again")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.close()
