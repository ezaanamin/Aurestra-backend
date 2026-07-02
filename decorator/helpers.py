# utils/helpers.py  —  Pure helper functions shared across services

from database import app, db
from model import Category
from transfer_matching import is_own_account_transfer_row


def calculate_month_expenses(year: int, month: int, user_id: int = None) -> float:
    """
    Running-balance expense calculation (date-ordered), optionally scoped to a user.

    Rules:
      - Debit  → adds to running expense total.
      - Credit → reduces running expense only if spending already exists (clamped at 0).
    """
    from model import Transaction
    from sqlalchemy import extract

    q = Transaction.query.filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    )
    if user_id is not None:
        q = q.filter(Transaction.user_id == user_id)

    transactions = q.order_by(Transaction.date.asc()).all()

    running = 0.0
    for txn in transactions:
        if is_own_account_transfer_row(txn):
            continue
        if txn.type == 'debit':
            running += txn.amount
        elif txn.type == 'credit':
            running = max(0.0, running - txn.amount)

    return running



DEFAULT_CATEGORIES = [
    {"name": "Food & Snacks",    "icon": "food",        "color": "#FF6B6B", "type": "spending"},
    {"name": "Movies",           "icon": "movie",       "color": "#EC4899", "type": "spending"},
    {"name": "Tea",              "icon": "coffee",      "color": "#F59E0B", "type": "spending"},
    {"name": "Uber",             "icon": "car",         "color": "#4ECDC4", "type": "spending"},
    {"name": "Shopping",         "icon": "shopping",    "color": "#A78BFA", "type": "spending"},
    {"name": "Healthcare",       "icon": "hospital",    "color": "#10B981", "type": "spending"},
    {"name": "Education",        "icon": "school",      "color": "#F59E0B", "type": "spending"},
    {"name": "Groceries",        "icon": "cart",        "color": "#10B981", "type": "spending"},
    {"name": "Online Services",  "icon": "web",         "color": "#3B82F6", "type": "spending"},
    {"name": "Gym & Fitness",    "icon": "dumbbell",    "color": "#FF6B6B", "type": "spending"},
    {"name": "Rent / Housing",   "icon": "home",        "color": "#EC4899", "type": "spending"},
    {"name": "Fuel / Gas",       "icon": "gas-station", "color": "#F59E0B", "type": "spending"},
    {"name": "Cash Withdrawal",  "icon": "cash-minus",  "color": "#10B981", "type": "spending"},
    {"name": "Travel & Vacation", "icon": "airplane",    "color": "#3B82F6", "type": "spending"},
    {"name": "Gifts & Donations", "icon": "gift",        "color": "#EC4899", "type": "spending"},
    {"name": "Income",           "icon": "cash",        "color": "#10B981", "type": "income"},
    {"name": "Bonus",            "icon": "gift",        "color": "#F59E0B", "type": "income"},
    {"name": "Investment",       "icon": "trending-up", "color": "#3B82F6", "type": "income"},
    {"name": "Uncategorized",    "icon": "help-circle", "color": "#64748B", "type": "both"},
]


def seed_categories():
    """Seed the database with global default categories (idempotent, backward-compat)."""
    with app.app_context():
        for cat_data in DEFAULT_CATEGORIES:
            cat = Category.query.filter_by(name=cat_data["name"], user_id=None).first()
            if not cat:
                cat = Category(
                    name=cat_data["name"],
                    icon=cat_data["icon"],
                    color=cat_data["color"],
                    cat_type=cat_data["type"],
                    is_default=True,
                    user_id=None,
                )
                db.session.add(cat)
            else:
                cat.cat_type = cat_data["type"]
        db.session.commit()
        print("✅ Categories seeded")


def seed_categories_for_user(user_id: int):
    """No-op: default categories are now stored globally with user_id=None."""
    print(f"  ⏭️  No-op: User {user_id} uses global default categories.")
    return


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
