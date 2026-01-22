from database import app, db
from sqlalchemy import text

with app.app_context():
    try:
        print("Migrating database...")
        with db.engine.connect() as conn:
            # MySQL syntax to add column if not exists is tricky without procedures, 
            # so we just try/catch as simple migration strategy
            conn.execute(text("ALTER TABLE users ADD COLUMN notifications_enabled BOOLEAN DEFAULT TRUE;"))
            conn.commit()
        print("✅ Added notifications_enabled column to users table.")
    except Exception as e:
        print(f"info: {e}")
