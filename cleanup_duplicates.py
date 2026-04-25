from app import app, db
from model import Transaction
from sqlalchemy import func
from sms_parser import generate_transaction_hash

def cleanup_duplicates():
    with app.app_context():
        print("🧹 Starting Duplicate Cleanup for 'Bank Statement' entries...")
        
        # 1. Backfill Hashes for transactions that don't have them
        print("🔄 Backfilling hashes for legacy transactions...")
        all_txs = Transaction.query.filter(Transaction.transaction_hash == None).all()
        for tx in all_txs:
            tx.transaction_hash = generate_transaction_hash({
                'date': tx.date,
                'amount': tx.amount,
                'type': tx.type,
                'source': tx.source
            })
        db.session.commit()
        print(f"   Hashes generated for {len(all_txs)} transactions.")

        # 2. Find Duplicates based on the newly generated Hash
        duplicates_query = db.session.query(
            Transaction.transaction_hash,
            func.count(Transaction.id)
        ).group_by(
            Transaction.transaction_hash
        ).having(func.count(Transaction.id) > 1).all()

        print(f"🔍 Found {len(duplicates_query)} unique transactions that have duplicates.")
        
        deleted_count = 0
        
        for tx_hash, count in duplicates_query:
            # Get all copies
            copies = Transaction.query.filter_by(transaction_hash=tx_hash).order_by(Transaction.id.asc()).all()
            
            # Keep the first one (oldest ID)
            keep_tx = copies[0]
            duplicates = copies[1:]
            
            print(f"   👉 Group Hash: {tx_hash[:10]}... | Count: {len(copies)}")
            print(f"      ✅ Keeping ID: {keep_tx.id} ({keep_tx.amount} {keep_tx.type})")
            
            for dup in duplicates:
                print(f"      ❌ Deleting ID: {dup.id}")
                db.session.delete(dup)
                deleted_count += 1
        
        try:
            db.session.commit()
            print(f"✨ Cleanup Complete. Deleted {deleted_count} duplicate records.")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error during commit: {e}")

if __name__ == "__main__":
    cleanup_duplicates()
