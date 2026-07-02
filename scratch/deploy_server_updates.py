import os
import sys
import sqlite3

# Adjust path to find backend modules
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(backend_dir)

from database import app, db
from decorator.helpers import seed_categories

print("1. Seeding new default categories...")
seed_categories()
print("✅ Categories seeded successfully.")

print("\n2. Migrating Therapy category to User 1...")
db_path = os.path.join(backend_dir, 'aurestra.db')
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    # Find global Therapy category
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id IS NULL")
    global_therapy = cursor.fetchone()
    
    # Find user 1 Therapy category
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id = 1")
    user_therapy = cursor.fetchone()
    
    if global_therapy:
        global_id = global_therapy['id']
        print(f"   -> Found global Therapy category: id={global_id}")
        
        if user_therapy:
            user_id_therapy = user_therapy['id']
            print(f"   -> Found user 1 Therapy category: id={user_id_therapy}")
            
            # Re-associate any transactions using the global category
            cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (user_id_therapy, global_id))
            print(f"   -> Re-associated transactions to user 1 category {user_id_therapy}")
            
            # Re-associate categorization rules
            cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (user_id_therapy, global_id))
            print(f"   -> Re-associated categorization rules to user 1 category {user_id_therapy}")
            
            # Delete global category
            cursor.execute("DELETE FROM categories WHERE id = ?", (global_id,))
            print(f"   -> Deleted global Therapy category {global_id}")
        else:
            # Just assign the global one to user 1
            cursor.execute("UPDATE categories SET user_id = 1, is_default = 0 WHERE id = ?", (global_id,))
            print(f"   -> Reassigned global Therapy category {global_id} directly to user 1")
    else:
        print("   -> No global Therapy category remaining.")
        if user_therapy:
            cursor.execute("UPDATE categories SET is_default = 0 WHERE id = ?", (user_therapy['id'],))
            print(f"   -> Ensured user 1 Therapy category {user_therapy['id']} is non-default")
            
    conn.commit()
    print("✅ Therapy category successfully migrated.")
except Exception as e:
    conn.rollback()
    print(f"❌ Error migrating Therapy category: {e}")
finally:
    conn.close()

print("\n✨ All server updates successfully completed!")
