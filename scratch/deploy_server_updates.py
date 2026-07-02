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

print("\n2. Cleaning up and migrating Therapy category...")
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
        
        cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id = ?", (other_user_id,))
        uncat = cursor.fetchone()
        if not uncat:
            cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id IS NULL")
            uncat = cursor.fetchone()
            
        uncat_id = uncat['id'] if uncat else None
        if uncat_id:
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

# Categories to be converted from global defaults to User 1 custom categories
USER_1_ONLY_CATEGORIES = [
    {"name": "Audible Subscription",    "icon": "headphones",  "color": "#A78BFA", "type": "spending"},
    {"name": "Google One Subscription", "icon": "google",      "color": "#3B82F6", "type": "spending"},
    {"name": "Ride / Transport",        "icon": "car",         "color": "#4ECDC4", "type": "spending"},
    {"name": "Bills & Utilities",       "icon": "receipt",     "color": "#3B82F6", "type": "spending"},
    {"name": "Personal Care",           "icon": "sparkles",    "color": "#8B5CF6", "type": "spending"},
]

print("\n3. Converting personal categories to User 1 private categories...")
try:
    for cat_info in USER_1_ONLY_CATEGORIES:
        name = cat_info["name"]
        
        # Find if User 1 already has this category
        cursor.execute("SELECT id FROM categories WHERE name = ? AND user_id = 1", (name,))
        user1_cat = cursor.fetchone()
        
        if user1_cat:
            user1_cat_id = user1_cat['id']
            # Make sure it's marked as custom (not default)
            cursor.execute("UPDATE categories SET is_default = 0 WHERE id = ?", (user1_cat_id,))
            print(f"   -> Set User 1 '{name}' category (ID {user1_cat_id}) is_default = 0")
        else:
            # Check if there is a global default category we can convert to User 1
            cursor.execute("SELECT id FROM categories WHERE name = ? AND user_id IS NULL", (name,))
            global_cat = cursor.fetchone()
            
            if global_cat:
                user1_cat_id = global_cat['id']
                cursor.execute("UPDATE categories SET user_id = 1, is_default = 0 WHERE id = ?", (user1_cat_id,))
                print(f"   -> Converted global '{name}' category (ID {user1_cat_id}) to User 1 custom category")
            else:
                # Create it fresh for User 1
                cursor.execute(
                    "INSERT INTO categories (name, icon, color, cat_type, is_default, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, cat_info["icon"], cat_info["color"], cat_info["type"], 0, 1)
                )
                user1_cat_id = cursor.lastrowid
                print(f"   -> Created new custom '{name}' category (ID {user1_cat_id}) for User 1")
        
        # Delete any remaining global copies of this category
        cursor.execute("SELECT id FROM categories WHERE name = ? AND user_id IS NULL AND id != ?", (name, user1_cat_id))
        global_copies = cursor.fetchall()
        for row in global_copies:
            global_copy_id = row['id']
            cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (user1_cat_id, global_copy_id))
            cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (user1_cat_id, global_copy_id))
            cursor.execute("DELETE FROM categories WHERE id = ?", (global_copy_id,))
            print(f"   -> Merged and deleted global copy of '{name}' (ID {global_copy_id})")

        # Delete any copies of this category for other users (migrate transactions to Uncategorized)
        cursor.execute("SELECT id, user_id FROM categories WHERE name = ? AND user_id != 1 AND id != ?", (name, user1_cat_id))
        other_user_copies = cursor.fetchall()
        for row in other_user_copies:
            other_cat_id = row['id']
            other_user_id = row['user_id']
            
            # Find this user's Uncategorized category
            cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id = ?", (other_user_id,))
            uncat = cursor.fetchone()
            if not uncat:
                cursor.execute("SELECT id FROM categories WHERE name = 'Uncategorized' AND user_id IS NULL")
                uncat = cursor.fetchone()
                
            uncat_id = uncat['id'] if uncat else None
            if uncat_id:
                cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (uncat_id, other_cat_id))
                cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (uncat_id, other_cat_id))
                print(f"   -> Moved transactions/rules for User {other_user_id} from '{name}' to Uncategorized")
            cursor.execute("DELETE FROM categories WHERE id = ?", (other_cat_id,))
            print(f"   -> Deleted '{name}' category for User {other_user_id}")

    conn.commit()
    print("✅ Personal categories successfully restricted to User 1.")
except Exception as e:
    conn.rollback()
    print(f"❌ Error converting personal categories: {e}")

print("\n4. Merging remaining user-specific duplicate default categories into global ones...")
try:
    # Find all categories that are marked as default but belong to a user
    cursor.execute("SELECT id, name, user_id FROM categories WHERE is_default = 1 AND user_id IS NOT NULL")
    user_default_cats = cursor.fetchall()
    
    merged_count = 0
    for cat in user_default_cats:
        user_cat_id = cat['id']
        name = cat['name']
        user_id = cat['user_id']
        
        # Find the matching global default category
        cursor.execute("SELECT id FROM categories WHERE name = ? AND user_id IS NULL AND is_default = 1", (name,))
        global_cat = cursor.fetchone()
        
        if global_cat:
            global_cat_id = global_cat['id']
            # Migrate transactions using this user-specific category to the global one
            cursor.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (global_cat_id, user_cat_id))
            # Migrate categorization rules
            cursor.execute("UPDATE categorization_rules SET category_id = ? WHERE category_id = ?", (global_cat_id, user_cat_id))
            # Delete user-specific duplicate
            cursor.execute("DELETE FROM categories WHERE id = ?", (user_cat_id,))
            merged_count += 1
            print(f"   -> Merged duplicate default category '{name}' for User {user_id} to global ID {global_cat_id}")
            
    conn.commit()
    print(f"✅ Successfully merged {merged_count} duplicate default categories.")
except Exception as e:
    conn.rollback()
    print(f"❌ Error merging duplicate default categories: {e}")
finally:
    conn.close()

print("\n✨ All server updates successfully completed!")
