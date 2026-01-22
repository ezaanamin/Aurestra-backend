from app import app, db
from model import FinancialInsight

def setup_db():
    with app.app_context():
        print("Checking for new tables...")
        db.create_all()
        print("✅ Database tables updated (FinancialInsight created if missing).")

if __name__ == "__main__":
    setup_db()
