from app import app, db
from sqlalchemy import text

def migrate_category_columns():
    with app.app_context():
        print("🔄 Checking categories table for missing columns...")
        
        try:
            # 1. Get current columns
            # Result format for SQLite and MySQL differs, so we'll try a safe approach
            columns = []
            
            # For MySQL/MariaDB
            try:
                result = db.session.execute(text("DESCRIBE categories"))
                columns = [row[0] for row in result.fetchall()]
                is_mysql = True
                print("Detected MySQL database")
            except:
                # For SQLite
                result = db.session.execute(text("PRAGMA table_info(categories)"))
                columns = [row[1] for row in result.fetchall()]
                is_mysql = False
                print("Detected SQLite database")

            # 2. Add columns if missing
            modified = False
            
            # Check for 'color'
            if 'color' not in columns:
                print("Adding 'color' column to categories...")
                if is_mysql:
                    db.session.execute(text("ALTER TABLE categories ADD COLUMN color VARCHAR(20) DEFAULT '#64748B'"))
                else:
                    db.session.execute(text("ALTER TABLE categories ADD COLUMN color VARCHAR(20) DEFAULT '#64748B'"))
                modified = True
            
            # Check for 'cat_type'
            if 'cat_type' not in columns:
                print("Adding 'cat_type' column to categories...")
                if is_mysql:
                    db.session.execute(text("ALTER TABLE categories ADD COLUMN cat_type VARCHAR(20) DEFAULT 'spending'"))
                else:
                    db.session.execute(text("ALTER TABLE categories ADD COLUMN cat_type VARCHAR(20) DEFAULT 'spending'"))
                modified = True

            if modified:
                db.session.commit()
                print("✅ Successfully updated categories table schema.")
            else:
                print("✅ All columns already exist. No changes needed.")

        except Exception as e:
            print(f"❌ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    migrate_category_columns()
