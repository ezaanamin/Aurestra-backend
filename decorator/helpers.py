# utils/helpers.py  —  Pure helper functions shared across services

from database import app, db
from model import Category
from transfer_matching import is_own_account_transfer_row


def calculate_month_expenses(year: int, month: int) -> float:
    """
    Running-balance expense calculation (date-ordered).

    Rules:
      - Debit  → adds to running expense total.
      - Credit → reduces running expense only if spending already exists (clamped at 0).

    Example A — credit arrives before any spending:
      Mar 01  Credit 13,000 → running=0
      Mar 05  Debit   5,400 → running=5,400
      Result: 5,400 ✅

    Example B — refund after purchase:
      Mar 01  Debit  5,400  → running=5,400
      Mar 05  Credit   100  → running=5,300
      Result: 5,300 ✅
    """
    from model import Transaction
    from sqlalchemy import extract

    transactions = Transaction.query.filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
    ).order_by(Transaction.date.asc()).all()

    running = 0.0
    for txn in transactions:
        if is_own_account_transfer_row(txn):
            continue
        if txn.type == 'debit':
            running += txn.amount
        elif txn.type == 'credit':
            running = max(0.0, running - txn.amount)

    return running


def seed_categories():
    """Seed the database with default categories (idempotent)."""
    defaults = [
        {"name": "Food & Snacks",          "icon": "food",        "color": "#FF6B6B", "type": "spending"},
        {"name": "Movies",                  "icon": "movie",       "color": "#EC4899", "type": "spending"},
        {"name": "Tea",                     "icon": "coffee",      "color": "#F59E0B", "type": "spending"},
        {"name": "Therapy",                 "icon": "brain",       "color": "#8B5CF6", "type": "spending"},
        {"name": "Uber",                    "icon": "car",         "color": "#4ECDC4", "type": "spending"},
        {"name": "Audible Subscription",    "icon": "headphones",  "color": "#A78BFA", "type": "spending"},
        {"name": "Google One Subscription", "icon": "google",      "color": "#3B82F6", "type": "spending"},
        {"name": "Ride / Transport",        "icon": "car",         "color": "#4ECDC4", "type": "spending"},
        {"name": "Bills & Utilities",       "icon": "receipt",     "color": "#3B82F6", "type": "spending"},
        {"name": "Shopping",                "icon": "shopping",    "color": "#A78BFA", "type": "spending"},
        {"name": "Healthcare",              "icon": "hospital",    "color": "#10B981", "type": "spending"},
        {"name": "Education",               "icon": "school",      "color": "#F59E0B", "type": "spending"},
        {"name": "Groceries",               "icon": "cart",        "color": "#10B981", "type": "spending"},
        {"name": "Personal Care",           "icon": "sparkles",    "color": "#8B5CF6", "type": "spending"},
        {"name": "Online Services",         "icon": "web",         "color": "#3B82F6", "type": "spending"},
        {"name": "Gym & Fitness",           "icon": "dumbbell",    "color": "#FF6B6B", "type": "spending"},
        {"name": "Income",                  "icon": "cash",        "color": "#10B981", "type": "income"},
        {"name": "Bonus",                   "icon": "gift",        "color": "#F59E0B", "type": "income"},
        {"name": "Investment",              "icon": "trending-up", "color": "#3B82F6", "type": "income"},
        {"name": "Uncategorized",           "icon": "help-circle", "color": "#64748B", "type": "both"},
    ]

    with app.app_context():
        for cat_data in defaults:
            cat = Category.query.filter_by(name=cat_data["name"]).first()
            if not cat:
                cat = Category(
                    name=cat_data["name"],
                    icon=cat_data["icon"],
                    color=cat_data["color"],
                    cat_type=cat_data["type"],
                    is_default=True,
                )
                db.session.add(cat)
            else:
                cat.cat_type = cat_data["type"]
        db.session.commit()
        print("✅ Categories seeded")


def save_monthly_summary(month: str, total_open: float, total_close: float):
    from model import MonthlyBalance
    with app.app_context():
        existing = MonthlyBalance.query.filter_by(month=month).first()
        if existing:
            return
        summary = MonthlyBalance(
            source="combined",
            month=month,
            opening_balance=total_open,
            closing_balance=total_close,
        )
        db.session.add(summary)
        db.session.commit()
        print(f"✅ Saved monthly summary for {month} in DB.")
