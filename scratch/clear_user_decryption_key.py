"""
Clears decryption_key, decryption_key_hash, and decryption_key_salt for a user.
This forces them through the key SETUP flow instead of the UNLOCK flow on next login.

Usage:
    venv/bin/python3 scratch/clear_user_decryption_key.py <email>

Example:
    venv/bin/python3 scratch/clear_user_decryption_key.py amin.ezaan@gmail.com
"""
import os
import sys
import sqlite3


def run():
    if len(sys.argv) < 2:
        print("Usage: venv/bin/python3 scratch/clear_user_decryption_key.py <email>")
        return

    email = sys.argv[1].strip().lower()

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(backend_dir, 'aurestra.db')

    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Check user exists
        cursor.execute("SELECT id, email, decryption_key_hash FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()

        if not user:
            print(f"❌ No user found with email: {email}")
            return

        print(f"Found user: {user['email']} (ID: {user['id']})")
        print(f"  Current hash: {user['decryption_key_hash']}")

        # Clear all decryption key fields
        cursor.execute(
            "UPDATE users SET decryption_key = NULL, decryption_key_hash = NULL, decryption_key_salt = NULL WHERE email = ?",
            (email,)
        )
        conn.commit()

        print(f"✅ Decryption key fields cleared for {email}")
        print(f"   → has_decryption_key will now return false")
        print(f"   → User will be routed to SETUP flow on next login")

    except Exception as e:
        print(f"❌ Error: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    run()
