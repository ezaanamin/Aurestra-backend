from datetime import datetime
from app import app, db
from model import Transaction

def test_sync():
    with app.app_context():
        # Replicating the EXACT logic from the endpoint
        latest_tx = Transaction.query.filter(
            Transaction.source.in_(['bank_sms', 'sms'])
        ).order_by(Transaction.created_at.desc()).first()
        
        if latest_tx:
            print(f"✅ Found Latest Transaction: {latest_tx.created_at.isoformat()}")
            print(f"   Source: database (ID: {latest_tx.id})")
        else:
            # Replicating the FALLBACK logic
            fallback_time = datetime.utcnow().isoformat()
            print(f"⚠️ No SMS Transaction Found in DB.")
            print(f"   Returning FALLBACK (NOW): {fallback_time}")
            print(f"   Source: fallback_now")

if __name__ == "__main__":
    test_sync()
