import os
import sys
import sqlite3
from werkzeug.security import generate_password_hash

def run():
    if len(sys.argv) < 3:
        print("Usage: venv/bin/python3 scratch/reset_user_password.py <email> <new_password>")
        return
        
    email = sys.argv[1].strip().lower()
    new_password = sys.argv[2]
    
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(backend_dir, 'aurestra.db')
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return
        
    # Generate password hash
    p_hash = generate_password_hash(new_password)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if user exists
        cursor.execute("SELECT id FROM users WHERE email=?", (email,))
        row = cursor.fetchone()
        if not row:
            print(f"❌ User with email '{email}' not found in SQLite database.")
            return
            
        # Update password hash and set auth_method to email/both so they can login
        cursor.execute(
            "UPDATE users SET password_hash=?, auth_method='email', is_email_verified=1 WHERE email=?",
            (p_hash, email)
        )
        conn.commit()
        print(f"✅ Successfully reset password for {email} to: {new_password}")
        print(f"✅ Auth method set to 'email' and verified status set to True.")
    except Exception as e:
        print(f"❌ Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run()
