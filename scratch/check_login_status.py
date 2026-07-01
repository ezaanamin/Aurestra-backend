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
        cursor.execute("SELECT id, email, is_email_verified, password_hash IS NOT NULL as has_password, auth_method FROM users")
        users = cursor.fetchall()
        print("\n🔒 User Login/Auth Status:")
        print("-" * 80)
        for u in users:
            print(f"User ID: {u['id']}")
            print(f"  Email:             {u['email']}")
            print(f"  Auth Method:       {u['auth_method']}")
            print(f"  Has Password:      {bool(u['has_password'])}")
            print(f"  Is Email Verified: {bool(u['is_email_verified'])}")
            print("-" * 80)
    except Exception as e:
        print(f"❌ Error querying users table: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run()
