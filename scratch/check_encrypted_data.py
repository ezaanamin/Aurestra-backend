import os
import sys
import sqlite3

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(backend_dir, 'aurestra.db')

if not os.path.exists(db_path):
    print(f"❌ Database not found at {db_path}")
    sys.exit(1)
    
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    cursor.execute("SELECT id, purpose, sender, receiver FROM transactions WHERE user_id = 1 LIMIT 20")
    txs = cursor.fetchall()
    print(f"Total transactions for user_id=1: {len(txs)}")
    
    enc_count = 0
    plain_count = 0
    
    for tx in txs:
        purpose = tx['purpose'] or ""
        sender = tx['sender'] or ""
        receiver = tx['receiver'] or ""
        
        is_enc = purpose.startswith("__enc__") or sender.startswith("__enc__") or receiver.startswith("__enc__")
        if is_enc:
            enc_count += 1
        else:
            plain_count += 1
            
        print(f"Tx id={tx['id']}:")
        print(f"  - purpose: {purpose[:30]}")
        print(f"  - sender: {sender[:30]}")
        print(f"  - receiver: {receiver[:30]}")
        
    print(f"\nSummary: Encrypted={enc_count}, Plain={plain_count}")
        
except Exception as e:
    print(f"❌ Error: {e}")
finally:
    conn.close()
