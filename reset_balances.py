#!/usr/bin/env python3
"""
Reset account balance to 0 after cleanup
"""

from app import app, db
from model import AccountBalance

def reset_balances():
    with app.app_context():
        print("💰 Resetting account balances to 0\n")
        
        balances = AccountBalance.query.all()
        
        for balance in balances:
            print(f"   {balance.source}: {balance.current_balance} → 0")
            balance.current_balance = 0.0
        
        db.session.commit()
        print("\n✅ All balances reset to 0")

if __name__ == '__main__':
    reset_balances()
