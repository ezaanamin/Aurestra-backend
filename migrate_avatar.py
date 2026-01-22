from database import app, db
from sqlalchemy import text

with app.app_context():
    try:
        print("Migrating database...")
        # Check if column exists is hard in raw sql cross-compatibly, so we just try add and catch error
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512);"))
            conn.commit()
        print("✅ Added avatar_url column to users table.")
    except Exception as e:
        print(f"info: {e}")
        # likely already exists
