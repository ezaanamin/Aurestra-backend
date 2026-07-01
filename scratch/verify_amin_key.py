import os
import sys
import sqlite3

sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from utils.crypto_helpers import verify_decryption_key

def test():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(backend_dir, 'aurestra.db')
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT decryption_key_hash, decryption_key_salt FROM users WHERE id=2")
    row = cursor.fetchone()
    if not row:
        print("❌ User 2 not found!")
        return
        
    hash_val = row['decryption_key_hash']
    salt = row['decryption_key_salt']
    print(f"User 2 hash: {hash_val}")
    print(f"User 2 salt: {salt}")
    
    # Try common testing keys
    keys = ["EndG*ame@201&", "EndGame@201&", "Admin@123", "Ezaan@123", "Aurestra@123", "Pass@123"]
    for k in keys:
        if verify_decryption_key(k, hash_val):
            print(f"🎉 MATCH FOUND! Key is: {k}")
            return
            
    print("❌ No match found in common keys.")

if __name__ == "__main__":
    test()
