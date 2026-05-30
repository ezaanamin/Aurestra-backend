# services/transaction_service.py  —  Transaction business logic

from datetime import datetime, timedelta, date
from sqlalchemy import func, extract, case, desc
from dateutil.relativedelta import relativedelta
from database import db
from model import Transaction, Category, AccountBalance, Budget
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from ledger_sync import apply_pending_transaction_ledger, ensure_account_balance_row, log_wallet_attribution
from decorator.helpers import calculate_month_expenses

# ── Read ──────────────────────────────────────────────────────────────────────

def get_latest_transactions(limit: int = 4):
    return (
        Transaction.query
        .filter(Transaction.is_deleted != True, Transaction.is_spam != True)
        .order_by(desc(Transaction.date))
        .limit(limit)
        .all()
    )


def get_uncategorized():
    return Transaction.query.filter(
        Transaction.categorization_status == 'pending',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
    ).order_by(desc(Transaction.date)).all()


def get_spam():
    return Transaction.query.filter(
        Transaction.is_spam    == True,
        Transaction.is_deleted != True,
    ).order_by(desc(Transaction.date)).all()


def get_categorized():
    return Transaction.query.filter(
        Transaction.categorization_status != 'pending',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
    ).order_by(desc(Transaction.date)).all()


def get_top_categories(period: str = 'month'):
    query = db.session.query(
        Transaction.purpose.label("category"),
        func.sum(Transaction.amount).label("total_spent"),
    ).filter(
        Transaction.type == 'debit',
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    )
    if period == 'week':
        query = query.filter(Transaction.date >= datetime.now() - timedelta(days=7))
    elif period == 'month':
        dt = datetime.now()
        query = query.filter(
            extract('year',  Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
        )
    elif period == 'year':
        query = query.filter(extract('year', Transaction.date) == datetime.now().year)

    rows = (
        query.group_by(Transaction.purpose)
        .having(func.sum(Transaction.amount) > 0)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10)
        .all()
    )
    return [{"category": r.category, "total_spent": r.total_spent} for r in rows]


def get_analytics_trend(period: str = 'month'):
    data_points = []
    expense_expr = func.sum(case(
        (Transaction.type == 'debit',  Transaction.amount),
        (Transaction.type == 'credit', -Transaction.amount),
        else_=0,
    ))
    base_filters = [
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    ]

    if period == 'week':
        for i in range(6, -1, -1):
            d = datetime.now().date() - timedelta(days=i)
            val = db.session.query(expense_expr).filter(
                func.date(Transaction.date) == d, *base_filters
            ).scalar() or 0.0
            data_points.append({"label": d.strftime("%a"), "value": float(val)})

    elif period == 'month':
        for i in range(5, -1, -1):
            dt = datetime.now() - relativedelta(months=i)
            val = db.session.query(expense_expr).filter(
                extract('year',  Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                *base_filters,
            ).scalar() or 0.0
            data_points.append({"label": dt.strftime("%b"), "value": float(val)})

    elif period == 'year':
        for m in range(1, 13):
            yr = datetime.now().year
            val = db.session.query(expense_expr).filter(
                extract('year',  Transaction.date) == yr,
                extract('month', Transaction.date) == m,
                *base_filters,
            ).scalar() or 0.0
            data_points.append({"label": date(yr, m, 1).strftime("%b"), "value": float(val)})

    elif period == 'all':
        for i in range(4, -1, -1):
            yr = datetime.now().year - i
            val = db.session.query(expense_expr).filter(
                extract('year', Transaction.date) == yr, *base_filters
            ).scalar() or 0.0
            data_points.append({"label": str(yr), "value": float(val)})

    return data_points


def get_monthly_category_totals(month_str: str):
    start_date = datetime.strptime(f"{month_str}-01", "%Y-%m-%d")
    end_date = (
        start_date.replace(year=start_date.year + 1, month=1)
        if start_date.month == 12
        else start_date.replace(month=start_date.month + 1)
    )
    rows = db.session.query(
        Transaction.purpose.label('category'),
        func.sum(case(
            (Transaction.type == 'debit',  Transaction.amount),
            (Transaction.type == 'credit', -Transaction.amount),
            else_=0,
        )).label('total'),
    ).filter(
        Transaction.date >= start_date,
        Transaction.date <  end_date,
        Transaction.is_deleted == False,
        Transaction.purpose.isnot(None),
        Transaction.purpose.ilike('Uncategorized') == False,
        exclude_own_account_transfer_sql(),
    ).group_by(Transaction.purpose).all()

    return sorted(
        [{"category": r.category, "total": float(r.total or 0)} for r in rows],
        key=lambda x: x["total"],
        reverse=True,
    )


# ── Write ─────────────────────────────────────────────────────────────────────

def create_manual_transaction(data: dict) -> tuple:
    """Returns (Transaction, accounts_list)."""
    amount = float(data.get("amount", 0))
    if amount <= 0:
        raise ValueError("Amount must be positive")

    t_type = (data.get("type") or "debit").strip().lower()
    if t_type not in ("debit", "credit"):
        raise ValueError("type must be debit or credit")

    slug = (
        (data.get("account_balance_source") or data.get("wallet_slug") or data.get("balance_account_slug") or "")
        .strip().lower()
    )
    if not slug:
        raise ValueError("account_balance_source is required (wallet slug, e.g. bank, easypaisa, cash)")

    date_str = data.get("date")
    tx_date = datetime.utcnow()
    if date_str:
        try:
            tx_date = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            pass

    new_tx = Transaction(
        source="manual", date=tx_date, amount=amount, type=t_type,
        purpose=data.get("category", "Uncategorized"),
        sender="Manual Entry", receiver="Me" if t_type == "credit" else "Merchant",
        notes=data.get("notes", ""),
        categorization_status="confirmed",
        account_balance_source=slug,
        balance_applied=False,
    )
    db.session.add(new_tx)
    db.session.flush()

    ensure_account_balance_row(slug)
    try:
        log_wallet_attribution(
            "MANUAL_TXN_CREATED", transaction_id=new_tx.id,
            resolved_wallet_slug=slug, txn_type=t_type,
            amount=amount, purpose=new_tx.purpose,
            note="Manual entry: ledger apply next",
        )
    except Exception:
        pass

    apply_pending_transaction_ledger(new_tx, respect_manual_lock=False)
    db.session.commit()

    accounts = [
        acc.to_dict()
        for acc in AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
    ]
    return new_tx, accounts


def soft_delete_transaction(txn_id: int):
    tx = Transaction.query.get(txn_id)
    if not tx:
        raise LookupError("Transaction not found")
    tx.is_deleted = True
    tx.categorization_status = 'deleted'
    db.session.commit()


def mark_spam(txn_id: int):
    tx = Transaction.query.get(txn_id)
    if not tx:
        raise LookupError("Transaction not found")
    tx.is_spam = True
    tx.categorization_status = 'spam'
    db.session.commit()


def update_transaction_category(txn_id: int, data: dict):
    txn = Transaction.query.get(txn_id)
    if not txn:
        raise LookupError("Transaction not found")

    prev_status = txn.categorization_status
    slug_hint = (
        (data.get("account_balance_source") or data.get("balance_account_slug") or "")
        .strip().lower()
    )

    will_finalize = ("category_id" in data) or ("purpose" in data)
    if prev_status == "pending" and will_finalize and not getattr(txn, "balance_applied", False):
        apply_pending_transaction_ledger(txn, balance_slug_override=slug_hint or None)

    if "category_id" in data:
        txn.category_id = data["category_id"]
        cat = Category.query.get(data["category_id"])
        if cat:
            txn.purpose = cat.name
        txn.categorization_status = 'manual'

    if "purpose" in data:
        txn.purpose = data["purpose"]
        cat = Category.query.filter_by(name=data["purpose"]).first()
        if cat:
            txn.category_id = cat.id
        txn.categorization_status = 'manual'

    if "notes" in data:
        txn.notes = data["notes"]

    db.session.commit()
    return txn


def bulk_categorize(transaction_ids: list, category_id: int, slug_hint: str = ""):
    cat = Category.query.get(category_id)
    if not cat:
        raise LookupError("Category not found")
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.get(tx_id)
        if tx:
            if tx.categorization_status == 'pending' and not getattr(tx, "balance_applied", False):
                apply_pending_transaction_ledger(tx, balance_slug_override=slug_hint or None)
            tx.category_id = category_id
            tx.purpose = cat.name
            tx.categorization_status = 'manual'
            updated += 1
    db.session.commit()
    return updated


def bulk_delete(transaction_ids: list) -> int:
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.get(tx_id)
        if tx:
            tx.is_deleted = True
            updated += 1
    db.session.commit()
    return updated


def bulk_spam(transaction_ids: list) -> int:
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.get(tx_id)
        if tx:
            tx.is_spam = True
            updated += 1
    db.session.commit()
    return updated


def get_total_expenses_for_current_month():
    dt = datetime.now()
    year, month = dt.year, dt.month
    month_str = dt.strftime("%Y-%m")

    total_expenses = calculate_month_expenses(year, month)

    total_debits = db.session.query(func.sum(Transaction.amount)).filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == 'debit',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    total_credits = db.session.query(func.sum(Transaction.amount)).filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type == 'credit',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    # Persist to Budget
    budget_entry = Budget.query.filter_by(month=month_str).first()
    if budget_entry:
        budget_entry.total_expenses = total_expenses
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()

    return {
        "month": month_str,
        "total_expense":  total_expenses,
        "total_debits":   total_debits,
        "total_credits":  total_credits,
    }
