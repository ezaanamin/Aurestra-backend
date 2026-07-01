import os
import sqlite3

def inspect_db(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check if users table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            return
            
        cursor.execute("SELECT id, email, decryption_key, decryption_key_hash, decryption_key_salt FROM users")
        users = cursor.fetchall()
        
        if users:
            print(f"\n📂 Database: {db_path}")
            print("-" * 80)
            for u in users:
                print(f"  User ID: {u['id']}")
                print(f"  Email:   {u['email']}")
                print(f"  Key:     {u['decryption_key']}")
                print(f"  Hash:    {u['decryption_key_hash']}")
                print(f"  Salt:    {u['decryption_key_salt']}")
                print("-" * 40)
    except Exception as e:
        # Ignore errors for files that are not valid SQLite DBs
        pass

def search():
    # Start searching from the repository root/backend directory
    start_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"🔍 Searching for SQLite databases starting from: {start_dir}")
    
    for root, dirs, files in os.walk(start_dir):
        # Skip virtual environments and hidden directories
        if any(p in root for p in ['venv', '.git', '__pycache__', 'node_modules']):
            continue
            
        for file in files:
            if file.endswith('.db') or file.endswith('.sqlite') or file.endswith('.sqlite3'):
                db_path = os.path.join(root, file)
                inspect_db(db_path)

if __name__ == "__main__":
    search()
