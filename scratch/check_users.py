import os
import sqlite3

def run():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(backend_dir, 'aurestra.db')
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, email, decryption_key, decryption_key_hash, decryption_key_salt FROM users")
        users = cursor.fetchall()
        print(f"Total Users in SQLite DB: {len(users)}")
        for u in users:
            print(f"User: id={u['id']}, email={u['email']}")
            print(f"  - decryption_key: {u['decryption_key']}")
            print(f"  - decryption_key_hash: {u['decryption_key_hash']}")
            print(f"  - decryption_key_salt: {u['decryption_key_salt']}")
    except Exception as e:
        print(f"❌ Error querying users table: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run()
