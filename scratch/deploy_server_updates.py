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

print("\n2. Cleaning up Therapy category...")
db_path = os.path.join(backend_dir, 'aurestra.db')
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    # 2a. Update User 1's Therapy category to be is_default = 0
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id = 1")
    user1_therapy = cursor.fetchone()
    
    if user1_therapy:
        user1_therapy_id = user1_therapy['id']
        cursor.execute("UPDATE categories SET is_default = 0 WHERE id = ?", (user1_therapy_id,))
        print(f"   -> Set User 1 Therapy category (ID {user1_therapy_id}) is_default = 0")
    else:
        # Create it as a custom category for User 1 if it doesn't exist
        cursor.execute(
            "INSERT INTO categories (name, icon, color, cat_type, is_default, user_id) VALUES (?, ?, ?, ?, ?, ?)",
            ('Therapy', 'brain', '#8B5CF6', 'spending', 0, 1)
        )
        user1_therapy_id = cursor.lastrowid
        print(f"   -> Created new custom Therapy category (ID {user1_therapy_id}) for User 1")

    # 2b. Find any global Therapy category (user_id IS NULL)
    cursor.execute("SELECT id FROM categories WHERE name = 'Therapy' AND user_id IS NULL")
    global_therapies = cursor.fetchall()
    for row in global_therapies:
        global_id = row['id']
        # Point transactions to User 1's category
        cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (user1_therapy_id, global_id))
        cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (user1_therapy_id, global_id))
        cursor.execute("DELETE FROM categories WHERE id = ?", (global_id,))
        print(f"   -> Migrated and deleted global Therapy category (ID {global_id})")

    # 2c. Clean up Therapy categories for users other than User 1
    cursor.execute("SELECT id, user_id FROM categories WHERE name = 'Therapy' AND user_id != 1")
    other_therapies = cursor.fetchall()
    
    for row in other_therapies:
        other_cat_id = row['id']
        other_user_id = row['user_id']
        
        # Find this user's Uncategorized category
        cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id = ?", (other_user_id,))
        uncat = cursor.fetchone()
        
        # Fallback to global Uncategorized if no user-specific one
        if not uncat:
            cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id IS NULL")
            uncat = cursor.fetchone()
            
        uncat_id = uncat['id'] if uncat else None
        
        if uncat_id:
            # Re-associate their transactions/rules to Uncategorized
            cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (uncat_id, other_cat_id))
            cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (uncat_id, other_cat_id))
            print(f"   -> Moved transactions/rules for User {other_user_id} from Therapy to Uncategorized")
            
        cursor.execute("DELETE FROM categories WHERE id = ?", (other_cat_id,))
        print(f"   -> Deleted Therapy category for User {other_user_id}")

    conn.commit()
    print("✅ Therapy category successfully cleaned up.")
except Exception as e:
    conn.rollback()
    print(f"❌ Error during Therapy category cleanup: {e}")
finally:
    conn.close()

print("\n✨ All server updates successfully completed!")
