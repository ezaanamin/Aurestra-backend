#!/usr/bin/env python3
"""
Nuclear cleanup: Delete ALL transactions, statements, and summaries to start fresh.
"""

from app import app, db
from model import Transaction, SMSMessage, StatementAnalysis, MonthlyBalance, AccountBalance

def cleanup_all():
    with app.app_context():
        print("🧹 NUCLEAR CLEANUP - Deleting ALL data\n")
        
        models_to_clear = [Transaction, SMSMessage, StatementAnalysis, MonthlyBalance]
        
        print(f"📊 Current state:")
        for m in models_to_clear:
            try:
                print(f"   {m.__name__}: {m.query.count()}")
            except:
                print(f"   {m.__name__}: Error counting")
        
        # Confirm
        # print("\n⚠️  This will DELETE ALL transactions, SMS, and Statements!")
        # print("   The system will start fresh with proper deduplication.")
        # No input needed for automation if run via command line pipe
        
        for m in models_to_clear:
            print(f"🔥 Deleting all from {m.__tablename__}...")
            try:
                db.session.query(m).delete()
            except Exception as e:
                print(f"   Error deleting {m.__name__}: {e}")
        
        print("\n💰 Resetting account balances...")
        try:
            db.session.query(AccountBalance).update({AccountBalance.current_balance: 0.0})
        except Exception as e:
            print(f"   Error resetting balances: {e}")
            
        db.session.commit()
        print("✅ DATABASE RESET COMPLETE")

if __name__ == '__main__':
    cleanup_all()
