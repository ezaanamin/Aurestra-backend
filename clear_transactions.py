from app import app, db
from model import Transaction

def clear_db():
    with app.app_context():
        try:
            num_deleted = db.session.query(Transaction).delete()
            db.session.commit()
            print(f"✅ Successfully deleted {num_deleted} transactions.")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error deleting transactions: {e}")

if __name__ == "__main__":
    clear_db()
