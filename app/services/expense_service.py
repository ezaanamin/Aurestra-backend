"""Expense calculation service."""
from datetime import datetime
from sqlalchemy import extract
from app.extensions import db
from app.models import Transaction, Budget
from transfer_matching import is_own_account_transfer_row

def calculate_month_expenses(year, month):
    """
    Running-balance expense calculation (date-ordered).

    Rules:
      - Transactions are processed in chronological order.
      - Debit  → adds to running expense total.
      - Credit → reduces running expense, but ONLY what has already
                 been accumulated. It CANNOT go below 0.

    This means:
      - A bonus/income that arrives BEFORE any spending has NO effect.
      - A refund that arrives AFTER a purchase correctly reduces it.

    Example A (your case):
      Mar 01  Credit Rs 13,000  → running=0  (nothing to reduce)
      Mar 05  Debit  Rs  5,400  → running=5,400
      Result: Rs 5,400  ✅

    Example B (refund case):
      Mar 01  Debit  Rs 5,400   → running=5,400
      Mar 05  Credit Rs   100   → running=5,300
      Result: Rs 5,300  ✅

    Example C (credit wipes all spending):
      Mar 01  Debit  Rs 5,400   → running=5,400
      Mar 05  Credit Rs 6,000   → running=0  (clamped)
      Result: Rs 0  ✅
    """
    transactions = Transaction.query.filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).order_by(Transaction.date.asc()).all()   # ← chronological order is key

    running = 0.0
    for txn in transactions:
        if is_own_account_transfer_row(txn):
            continue
        if txn.type == 'debit':
            running += txn.amount
        elif txn.type == 'credit':
            running = max(0.0, running - txn.amount)  # only reduce existing spending

    return running



