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
    cursor.execute("SELECT id, name, is_default, user_id, cat_type FROM categories")
    categories = cursor.fetchall()
    print(f"Total categories in SQLite DB: {len(categories)}")
    default_cats = [c for c in categories if c['is_default']]
    user_cats = [c for c in categories if not c['is_default']]
    print(f"Default categories count: {len(default_cats)}")
    print(f"User categories count: {len(user_cats)}")
    
    print("\nSample default categories:")
    for c in default_cats[:5]:
        print(f"  - id={c['id']}, name={c['name']}, user_id={c['user_id']}")
        
    print("\nSample user categories:")
    for c in user_cats[:5]:
        print(f"  - id={c['id']}, name={c['name']}, user_id={c['user_id']}")
        
except Exception as e:
    print(f"❌ Error querying categories table: {e}")
finally:
    conn.close()
