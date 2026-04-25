
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
            from sms_parser import generate_transaction_hash
            count = 0
            
            # Pre-fetch existing hashes for efficiency? (Optional, but manual sync is infrequent)
            
            for tx in bank_data["transactions"]:
                try:
                    tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
                except:
                    tx_date = datetime.utcnow()

                # Generate Hash
                transaction_hash = generate_transaction_hash({
                    'date': tx_date,
                    'amount': tx["amount"],
                    'type': tx["type"],
                    'source': 'bank'  # Source is 'bank' for statement imports
                })

                # Deduplication Check 1: By Transaction Hash
                existing_by_hash = Transaction.query.filter_by(
                    transaction_hash=transaction_hash
                ).first()
                
                if existing_by_hash:
                    # Update description if it's better? (Statement descriptions are usually better than SMS)
                    print(f"Skipping duplicate (Hash): {transaction_hash[:10]}...")
                    continue

                # Deduplication Check 2: Fuzzy Match (Legacy support)
                # Check for same date/amount/type where transaction_hash is NULL
                # AND ensure we don't duplicate an SMS transaction that hasn't been hashed yet (though SMS parser now hashes)
                
                # Check 3: Check against SMS with similar details
                # If an SMS transaction exists for this day/amount, we should MERGE them, not duplicate.
                # However, statements have full dates, SMS might not?
                # Let's assume strict Date match + Amount + Type
                
                similar_tx = Transaction.query.filter(
                    db.func.date(Transaction.date) == tx_date.date(),
                    Transaction.amount == tx["amount"],
                    Transaction.type == tx["type"]
                ).first()
                
                if similar_tx:
                    print(f"Merging with existing transaction (Fuzzy): {similar_tx.id}")
                    # Enhance existing transaction with statement details
                    if not similar_tx.transaction_hash:
                         similar_tx.transaction_hash = transaction_hash
                         
                    # Update notes/description if statement has more info?
                    # similar_tx.notes = tx["description"][:250] 
                    # Often dangerous to overwrite if user manually edited.
                    
                    # Ensure source is tracked? 
                    # similar_tx.source = "bank" # Upgrade source from 'sms' to 'bank'?
                    
                    db.session.add(similar_tx)
                    continue

                # Create New
                new_tx = Transaction(
                    source="bank",
                    date=tx_date,
                    amount=tx["amount"],
                    type=tx["type"],
                    purpose="Uncategorized",
                    sender="Bank Statement",
                    receiver="Me",
                    notes=tx["description"][:250],
                    transaction_hash=transaction_hash,
                    sms_hash=None # No SMS hash for statement imports
                )
                db.session.add(new_tx)
                count += 1
            
            db.session.commit()
            print(f"Saved {count} new transactions.")
            
    print("--- Sync Complete ---")
