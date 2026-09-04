import sqlite3
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(backend_dir, 'aurestra.db')

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT id, name, user_id, is_default, cat_type FROM categories WHERE user_id IS NULL")
rows = cursor.fetchall()
print(f"Total global default categories (user_id IS NULL): {len(rows)}")
for r in rows:
    print(f"  - [{r['id']}] {r['name']} ({r['cat_type']}) - is_default: {r['is_default']}")

conn.close()
