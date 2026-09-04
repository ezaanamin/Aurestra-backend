import os
import sys
import sqlite3

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(backend_dir, 'aurestra.db')

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    # 1. Find global Therapy category
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id IS NULL")
    global_therapy = cursor.fetchone()
    
    # 2. Find user 1 Therapy category
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id = 1")
    user_therapy = cursor.fetchone()
    
    if global_therapy:
        global_id = global_therapy['id']
        print(f"found global Therapy category: id={global_id}")
        
        if user_therapy:
            user_id_therapy = user_therapy['id']
            print(f"found user 1 Therapy category: id={user_id_therapy}")
            
            # Re-associate any transactions using the global category
            cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (user_id_therapy, global_id))
            print(f"updated transactions referencing global category {global_id} to user category {user_id_therapy}")
            
            # Re-associate categorization rules
            cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (user_id_therapy, global_id))
            print(f"updated categorization rules referencing global category {global_id} to user category {user_id_therapy}")
            
            # Delete global category
            cursor.execute("DELETE FROM categories WHERE id = ?", (global_id,))
            print(f"deleted global Therapy category {global_id}")
        else:
            # Just assign the global one to user 1 and set is_default to False
            cursor.execute("UPDATE categories SET user_id = 1, is_default = 0 WHERE id = ?", (global_id,))
            print(f"assigned global Therapy category {global_id} to user 1 and marked as non-default")
            
    else:
        print("no global Therapy category found")
        if user_therapy:
            # Ensure it is marked as non-default
            cursor.execute("UPDATE categories SET is_default = 0 WHERE id = ?", (user_therapy['id'],))
            print(f"ensured user 1 Therapy category {user_therapy['id']} is marked as non-default")
            
    conn.commit()
    print("✨ Successfully completed Therapy category migration.")
except Exception as e:
    conn.rollback()
    print(f"❌ Error migrating Therapy category: {e}")
finally:
    conn.close()
