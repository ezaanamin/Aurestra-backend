from database import db, app
from sqlalchemy import text
import os

def migrate():
    print("Starting migration...")
    try:
        with app.app_context():
            # Check DB Type
            db_uri = app.config['SQLALCHEMY_DATABASE_URI']
            print(f"Database URI: {db_uri}")
            
            commands = [
                "ALTER TABLE users ADD COLUMN google_id VARCHAR(50);",
                "ALTER TABLE users ADD COLUMN google_email VARCHAR(120);",
                "ALTER TABLE users ADD COLUMN google_refresh_token VARCHAR(255);"
            ]
            
            for cmd in commands:
                try:
                    db.session.execute(text(cmd))
                    print(f"✅ Executed: {cmd}")
                except Exception as e:
                    print(f"⚠️  Skipped (likely exists): {cmd}")
                    # print(f"   Error: {e}")
            
            db.session.commit()
            print("🎉 Migration complete.")
            
    except Exception as e:
        print(f"❌ Migration Failed: {e}")

if __name__ == "__main__":
    migrate()
