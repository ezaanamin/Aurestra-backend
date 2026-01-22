
from app import app, db, fetch_latest_bank_email, Transaction, AccountBalance, SavingsGoal
from datetime import datetime

with app.app_context():
    print("--- Manual Sync Started ---")
    
    # 1. Fetch Bank Data
    bank_data = fetch_latest_bank_email()
    
    if "error" in bank_data:
        print(f"Error: {bank_data['error']}")
    else:
        # Update Balance
        if "balances" in bank_data:
            closing_bal = bank_data["balances"].get("closing_balance", 0.0)
            bank_acc = AccountBalance.query.filter_by(source="bank").first()
            if not bank_acc:
                bank_acc = AccountBalance(source="bank", current_balance=0.0)
                db.session.add(bank_acc)
            
            bank_acc.current_balance = closing_bal
            bank_acc.last_updated = datetime.utcnow()
            print(f"Updated Bank Balance: {closing_bal}")

        # Save Transactions
        if "transactions" in bank_data:
            count = 0
            for tx in bank_data["transactions"]:
                try:
                    tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
                except:
                    tx_date = datetime.utcnow()

                exists = Transaction.query.filter(
                    Transaction.date == tx_date,
                    Transaction.amount == tx["amount"],
                    Transaction.type == tx["type"]
                ).first()
                
                if not exists:
                    new_tx = Transaction(
                        source="bank",
                        date=tx_date,
                        amount=tx["amount"],
                        type=tx["type"],
                        purpose="Uncategorized",
                        sender="Bank Statement",
                        receiver="Me",
                        notes=tx["description"][:250]
                    )
                    db.session.add(new_tx)
                    count += 1
            
            db.session.commit()
            print(f"Saved {count} new transactions.")
            
    print("--- Sync Complete ---")
