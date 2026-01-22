"""
Database Migration Script for Transaction Categorization

Adds categorization tracking fields to transactions table:
- categorization_status (VARCHAR(20), default 'pending')
- category_id (INTEGER, FK to categories.id)

Creates new categorization_rules table for learning system.
"""

from app import app, db
from sqlalchemy import text

def migrate_categorization():
    with app.app_context():
        print("🔄 Starting categorization migration...")
        
        try:
            # ===== TRANSACTIONS TABLE =====
            print("\n📊 Updating transactions table...")
            
            # Check existing columns
            result = db.session.execute(text("PRAGMA table_info(transactions)"))
            columns = [row[1] for row in result.fetchall()]
            
            # Add categorization_status column
            if 'categorization_status' not in columns:
                print("Adding categorization_status column...")
                db.session.execute(text(
                    "ALTER TABLE transactions ADD COLUMN categorization_status VARCHAR(20) DEFAULT 'pending'"
                ))
                db.session.commit()
                print("✅ Added categorization_status column")
                
                # Mark existing categorized transactions
                print("Updating existing transactions with categories...")
                db.session.execute(text(
                    "UPDATE transactions SET categorization_status = 'categorized' WHERE purpose IS NOT NULL AND purpose != 'Uncategorized'"
                ))
                db.session.commit()
                print("✅ Updated existing categorized transactions")
            else:
                print("✓ categorization_status column already exists")
            
            # Add category_id column
            if 'category_id' not in columns:
                print("Adding category_id column...")
                db.session.execute(text(
                    "ALTER TABLE transactions ADD COLUMN category_id INTEGER REFERENCES categories(id)"
                ))
                db.session.commit()
                print("✅ Added category_id column")
                
                # Link existing transactions to categories by name
                print("Linking existing transactions to categories...")
                db.session.execute(text("""
                    UPDATE transactions 
                    SET category_id = (
                        SELECT id FROM categories 
                        WHERE categories.name = transactions.purpose
                    )
                    WHERE purpose IS NOT NULL
                """))
                db.session.commit()
                print("✅ Linked existing transactions to categories")
            else:
                print("✓ category_id column already exists")
            
            # ===== CATEGORIZATION_RULES TABLE =====
            print("\n📜 Creating categorization_rules table...")
            
            # Check if table exists
            result = db.session.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='categorization_rules'"
            ))
            table_exists = result.fetchone() is not None
            
            if not table_exists:
                print("Creating categorization_rules table...")
                db.session.execute(text("""
                    CREATE TABLE categorization_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        merchant_pattern VARCHAR(255) NOT NULL,
                        category_id INTEGER NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        hit_count INTEGER DEFAULT 0,
                        FOREIGN KEY (user_id) REFERENCES users(id),
                        FOREIGN KEY (category_id) REFERENCES categories(id)
                    )
                """))
                db.session.commit()
                print("✅ Created categorization_rules table")
            else:
                print("✓ categorization_rules table already exists")
            
            print("\n✅ Migration completed successfully!")
            print(f"\n📈 Summary:")
            
            # Count uncategorized transactions
            result = db.session.execute(text(
                "SELECT COUNT(*) FROM transactions WHERE categorization_status = 'pending'"
            ))
            uncategorized_count = result.fetchone()[0]
            
            result = db.session.execute(text(
                "SELECT COUNT(*) FROM transactions WHERE categorization_status = 'categorized'"
            ))
            categorized_count = result.fetchone()[0]
            
            print(f"   - Categorized transactions: {categorized_count}")
            print(f"   - Uncategorized transactions: {uncategorized_count}")
            
            if uncategorized_count > 0:
                print(f"\n💡 You have {uncategorized_count} uncategorized transactions ready to be organized!")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    migrate_categorization()
