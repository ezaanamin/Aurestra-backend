"""
Migration: Add processing status fields to StatementAnalysis table
"""

from app import app, db
from sqlalchemy import text

def migrate_processing_status():
    """Add processing_status and processing_notes columns"""
    with app.app_context():
        try:
            # Check if columns already exist
            result = db.session.execute(text("PRAGMA table_info(statement_analysis)"))
            columns = [row[1] for row in result]
            
            # Add processing_status column if it doesn't exist
            if 'processing_status' not in columns:
                print("Adding processing_status column...")
                db.session.execute(text(
                    "ALTER TABLE statement_analysis ADD COLUMN processing_status VARCHAR(20) DEFAULT 'success'"
                ))
                print("✅ Added processing_status column")
            else:
                print("⏭️  processing_status column already exists")
            
            # Add processing_notes column if it doesn't exist
            if 'processing_notes' not in columns:
                print("Adding processing_notes column...")
                db.session.execute(text(
                    "ALTER TABLE statement_analysis ADD COLUMN processing_notes TEXT"
                ))
                print("✅ Added processing_notes column")
            else:
                print("⏭️  processing_notes column already exists")
            
            db.session.commit()
            print("\n✅ Migration completed successfully!")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Migration error: {e}")

if __name__ == "__main__":
    print("🔄 Running migration: Add processing status fields")
    print("=" * 60)
    migrate_processing_status()
