import sqlite3
import os

# You must run this script on your actual server running the backend!
DB_PATH = "aurestra.db" # Make sure this runs in /home/ezaanamin/Aurestra-backend

def migrate_db():
    print(f"Connecting to {DB_PATH}...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Step 1: Add the missing columns to the existing SQLite database
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN name VARCHAR(100);")
            print("✅ Added 'name' column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("ℹ️ 'name' column already exists")
            else:
                raise e

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_picture VARCHAR(512);")
            print("✅ Added 'profile_picture' column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("ℹ️ 'profile_picture' column already exists")
            else:
                raise e

        # NOTE: Changing 'id' to UUID in SQLite requires a full table rebuild.
        # Since Aurestra-backend uses Integer IDs for foreign keys (transactions, etc.),
        # doing so will break Aurestra's internal relationships.
        # If the Auth API absolutely needs a UUID, it's highly recommended to let Aurestra 
        # keep its integer ID and let the Auth API match by `email` or `google_id` instead!

        conn.commit()
        conn.close()
        print("✅ Migration complete! Please restart your Flask server.")

    except Exception as e:
        print(f"❌ Error during migration: {e}")

if __name__ == "__main__":
    migrate_db()
