"""
Database Migration Script

Adds balance tracking columns to statement_analysis table:
- balance_applied (BOOLEAN, default FALSE)
- reviewed_at (DATETIME, nullable)
- statement_id (VARCHAR(64), nullable)
"""

from app import app, db
from sqlalchemy import text

def migrate_database():
    with app.app_context():
        print("🔄 Starting database migration...")
        
        try:
            # Check if columns exist
            result = db.session.execute(text("PRAGMA table_info(statement_analysis)"))
            columns = [row[1] for row in result.fetchall()]
            
            print(f"Current columns: {columns}")
            
            # Add balance_applied column if it doesn't exist
            if 'balance_applied' not in columns:
                print("Adding balance_applied column...")
                db.session.execute(text(
                    "ALTER TABLE statement_analysis ADD COLUMN balance_applied BOOLEAN DEFAULT 0"
                ))
                db.session.commit()
                print("✅ Added balance_applied column")
            else:
                print("✓ balance_applied column already exists")
            
            # Add reviewed_at column if it doesn't exist
            if 'reviewed_at' not in columns:
                print("Adding reviewed_at column...")
                db.session.execute(text(3
                    "ALTER TABLE statement_analysis ADD COLUMN reviewed_at DATETIME"
                ))
                db.session.commit()
                print("✅ Added reviewed_at column")
            else:
                print("✓ reviewed_at column already exists")
            
            # Add statement_id column if it doesn't exist
            if 'statement_id' not in columns:
                print("Adding statement_id column...")
                db.session.execute(text(
                    "ALTER TABLE statement_analysis ADD COLUMN statement_id VARCHAR(64)"
                ))
                db.session.commit()
                print("✅ Added statement_id column")
                
                # Populate statement_id for existing records (use month as ID)
                print("Populating statement_id for existing records...")
                db.session.execute(text(
                    "UPDATE statement_analysis SET statement_id = month WHERE statement_id IS NULL"
                ))
                db.session.commit()
                print("✅ Populated statement_id for existing records")
            else:
                print("✓ statement_id column already exists")
            
            print("\n✅ Migration completed successfully!")
            print("\nNOTE: All existing statements have balance_applied=False by default.")
            print("They will apply their closing balance on next review.")
            print("If you want to prevent this, run:")
            print("  UPDATE statement_analysis SET balance_applied = 1;")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    migrate_database()
