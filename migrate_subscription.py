from app import app, db
from sqlalchemy import text
from services.subscription_service import seed_plans

def migrate_subscription():
    with app.app_context():
        print("🔄 Running subscription migrations...")
        
        # 1. Create tables first (via db.create_all() which creates the plans table safely if it doesn't exist)
        try:
            db.create_all()
            print("✅ Checked/Created database tables (including plans).")
        except Exception as e:
            print(f"⚠️ db.create_all failed: {e}")

        # 2. Check and migrate users table for missing columns
        try:
            # Get current columns
            columns = []
            
            # Detect SQLite or MySQL
            try:
                result = db.session.execute(text("DESCRIBE users"))
                columns = [row[0] for row in result.fetchall()]
                is_mysql = True
                print("Detected MySQL database")
            except:
                result = db.session.execute(text("PRAGMA table_info(users)"))
                columns = [row[1] for row in result.fetchall()]
                is_mysql = False
                print("Detected SQLite database")

            modified = False
            
            # Check for current_plan_id
            if 'current_plan_id' not in columns:
                print("Adding 'current_plan_id' column to users...")
                db.session.execute(text("ALTER TABLE users ADD COLUMN current_plan_id VARCHAR(50) DEFAULT 'free'"))
                modified = True
            
            # Check for subscription_status
            if 'subscription_status' not in columns:
                print("Adding 'subscription_status' column to users...")
                db.session.execute(text("ALTER TABLE users ADD COLUMN subscription_status VARCHAR(50) DEFAULT 'active'"))
                modified = True

            # Check for subscription_started_at
            if 'subscription_started_at' not in columns:
                print("Adding 'subscription_started_at' column to users...")
                if is_mysql:
                    db.session.execute(text("ALTER TABLE users ADD COLUMN subscription_started_at DATETIME NULL"))
                else:
                    db.session.execute(text("ALTER TABLE users ADD COLUMN subscription_started_at TIMESTAMP NULL"))
                modified = True

            # Check for subscription_expires_at
            if 'subscription_expires_at' not in columns:
                print("Adding 'subscription_expires_at' column to users...")
                if is_mysql:
                    db.session.execute(text("ALTER TABLE users ADD COLUMN subscription_expires_at DATETIME NULL"))
                else:
                    db.session.execute(text("ALTER TABLE users ADD COLUMN subscription_expires_at TIMESTAMP NULL"))
                modified = True

            if modified:
                db.session.commit()
                print("✅ Successfully updated users table schema for subscriptions.")
            else:
                print("✅ All user subscription columns already exist. No changes needed.")

        except Exception as e:
            print(f"❌ User table migration failed: {e}")
            db.session.rollback()

        # 3. Seed default plans
        print("🌱 Seeding subscription plans...")
        seed_plans()

if __name__ == "__main__":
    migrate_subscription()
