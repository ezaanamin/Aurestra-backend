# services/transaction_service.py  —  Transaction business logic (Phase 3: user-scoped)

from datetime import datetime, timedelta, date
from sqlalchemy import func, extract, case, desc
from dateutil.relativedelta import relativedelta
from database import db
from model import Transaction, Category, AccountBalance, Budget, UploadedReceipt
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from ledger_sync import (
    apply_pending_transaction_ledger,
    ensure_account_balance_row,
    log_wallet_attribution,
    reverse_transaction_ledger,
    INSUFFICIENT_BALANCE_MSG,
)
from decorator.helpers import calculate_month_expenses, sum_month_expenses, sum_month_income
from utils.money import to_money, add_money, subtract_money, abs_money

import os
from werkzeug.utils import secure_filename
from services.ocr_service import perform_ocr
from services.receipt_parser import parse_receipt_text

# ── Read ──────────────────────────────────────────────────────────────────────

def get_latest_transactions(user_id: int, limit: int = 4):
    _SELF_TRANSFER_PURPOSES = ('Self-transfer', 'Self transfer', 'self transfer', 'Self Transfer')
    return (
        Transaction.query
        .filter(
            Transaction.user_id   == user_id,
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            Transaction.purpose.notin_(_SELF_TRANSFER_PURPOSES),
        )
        .order_by(desc(Transaction.date), desc(Transaction.id))  # id tiebreaker: most recently added first
        .limit(limit)
        .all()
    )


def get_uncategorized(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id              == user_id,
        Transaction.categorization_status == 'pending',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).order_by(desc(Transaction.date), desc(Transaction.id)).all()


def get_spam(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id    == user_id,
        Transaction.is_spam    == True,
        Transaction.is_deleted.isnot(True),
    ).order_by(desc(Transaction.date), desc(Transaction.id)).all()


def search_transactions(user_id: int, account_source: str = None, q: str = None, tx_type: str = None, period: str = None, category_id: int = None, limit: int = 100, offset: int = 0):
    """
    Search and filter transactions strictly for the authenticated user.
    Supports filtering by: account_source, text search (q), type (credit/debit), period (week/month/year/YYYY-MM), category_id.
    """
    query = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    )

    if account_source and account_source != 'all':
        query = query.filter(Transaction.account_balance_source == account_source)

    if q and q.strip():
        search_pattern = f"%{q.strip().lower()}%"
        query = query.filter(
            func.lower(Transaction.purpose).like(search_pattern) |
            func.lower(Transaction.sender).like(search_pattern) |
            func.lower(Transaction.receiver).like(search_pattern) |
            func.lower(Transaction.notes).like(search_pattern)
        )

    if tx_type and tx_type.lower() in ('credit', 'debit'):
        query = query.filter(Transaction.type == tx_type.lower())

    if category_id:
        query = query.filter(Transaction.category_id == category_id)

    if period:
        p_str = period.lower().strip()
        now = datetime.now()
        if p_str == 'this_week' or p_str == 'week':
            query = query.filter(Transaction.date >= now - timedelta(days=7))
        elif p_str == 'this_month' or p_str == 'month':
            query = query.filter(
                extract('year', Transaction.date) == now.year,
                extract('month', Transaction.date) == now.month
            )
        elif p_str == 'this_year' or p_str == 'year':
            query = query.filter(extract('year', Transaction.date) == now.year)
        elif len(p_str) == 7 and p_str[4] == '-':
            try:
                dt = datetime.strptime(p_str, "%Y-%m")
                query = query.filter(
                    extract('year', Transaction.date) == dt.year,
                    extract('month', Transaction.date) == dt.month
                )
            except ValueError:
                pass

    total_count = query.count()
    txs = query.order_by(desc(Transaction.date), desc(Transaction.id)).offset(offset).limit(limit).all()
    return txs, total_count


def get_account_statement(user_id: int, account_source: str, month_str: str = None):
    """
    Generate an account-specific financial statement for a user's wallet or bank account.
    Calculates opening balance, total credits, total debits, closing balance, and transaction history.
    """
    acc = AccountBalance.query.filter_by(user_id=user_id, source=account_source).first()
    if not acc:
        raise LookupError(f"Account '{account_source}' not found or unauthorized.")

    if not month_str:
        month_str = datetime.now().strftime("%Y-%m")

    try:
        dt = datetime.strptime(month_str, "%Y-%m")
    except ValueError:
        dt = datetime.now().replace(day=1)
        month_str = dt.strftime("%Y-%m")

    start_of_month = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_of_month = (start_of_month + relativedelta(months=1)) - timedelta(microseconds=1)

    # Calculate net cashflow AFTER the month to determine opening/closing balance relative to current_balance
    net_after = db.session.query(
        func.sum(case(
            (Transaction.type == 'credit', Transaction.amount),
            (Transaction.type == 'debit', -Transaction.amount),
            else_=0
        ))
    ).filter(
        Transaction.user_id == user_id,
        Transaction.account_balance_source == account_source,
        Transaction.date > end_of_month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True)
    ).scalar() or 0.0

    closing_balance = acc.current_balance - net_after

    # Month txs
    txs = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.account_balance_source == account_source,
        Transaction.date >= start_of_month,
        Transaction.date <= end_of_month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True)
    ).order_by(desc(Transaction.date), desc(Transaction.id)).all()

    total_credits = sum(t.amount for t in txs if t.type == 'credit')
    total_debits = sum(t.amount for t in txs if t.type == 'debit')
    opening_balance = closing_balance - total_credits + total_debits

    # Format line-by-line statement with running balance
    running_bal = opening_balance
    statement_rows = []
    # Reverse to calculate running balance chronologically
    sorted_asc = sorted(txs, key=lambda x: (x.date, x.id))
    for t in sorted_asc:
        if t.type == 'credit':
            running_bal += t.amount
        else:
            running_bal -= t.amount
        
        statement_rows.append({
            "id": t.id,
            "date": t.date.strftime("%Y-%m-%d %H:%M:%S"),
            "description": t.notes or t.sender or t.receiver or t.purpose or "Transaction",
            "purpose": t.purpose or "General",
            "type": t.type,
            "amount": t.amount,
            "money_in": t.amount if t.type == 'credit' else 0.0,
            "money_out": t.amount if t.type == 'debit' else 0.0,
            "running_balance": round(running_bal, 2),
            "account_name": acc.display_name,
            "account_source": acc.source
        })

    # Return descending order for display
    statement_rows.reverse()

    return {
        "account": {
            "id": acc.id,
            "source": acc.source,
            "display_name": acc.display_name,
            "account_kind": acc.account_kind,
            "current_balance": acc.current_balance,
        },
        "month": month_str,
        "opening_balance": round(opening_balance, 2),
        "closing_balance": round(closing_balance, 2),
        "total_credits": round(total_credits, 2),
        "total_debits": round(total_debits, 2),
        "transaction_count": len(txs),
        "transactions": statement_rows
    }


def get_categorized(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id              == user_id,
        Transaction.categorization_status != 'pending',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).order_by(desc(Transaction.date), desc(Transaction.id)).all()


def get_top_categories(user_id: int, period: str = 'month'):
    _EXCLUDED_PURPOSES = {'Self transfer', 'self transfer', 'Self Transfer'}

    query = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.type == 'debit',
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.is_deleted == False,
        Transaction.is_spam    == False,
        Transaction.categorization_status != 'pending',
        Transaction.categorization_status != 'spam',
        Transaction.categorization_status != 'deleted',
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

    txns = query.all()

    category_totals = {}
    for t in txns:
        if t.purpose in _EXCLUDED_PURPOSES:
            continue
        if is_own_account_transfer_row(t):
            continue
        
        cat_name = t.purpose
        category_totals[cat_name] = category_totals.get(cat_name, 0.0) + t.amount

    sorted_categories = sorted(
        [{"category": cat, "total_spent": float(total)} for cat, total in category_totals.items() if total > 0],
        key=lambda x: x["total_spent"],
        reverse=True
    )
    return sorted_categories[:10]


def get_analytics_trend(user_id: int, period: str = 'month'):
    data_points = []
    expense_expr = func.sum(case(
        (Transaction.type == 'debit', Transaction.amount),
        else_=0,
    ))
    base_filters = [
        Transaction.user_id    == user_id,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
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


def get_monthly_category_totals(user_id: int, month_str: str):
    _EXCLUDED_PURPOSES = {'Self transfer', 'self transfer', 'Self Transfer'}

    start_date = datetime.strptime(f"{month_str}-01", "%Y-%m-%d")
    end_date = (
        start_date.replace(year=start_date.year + 1, month=1)
        if start_date.month == 12
        else start_date.replace(month=start_date.month + 1)
    )
    txns = Transaction.query.filter(
        Transaction.user_id    == user_id,
        Transaction.date       >= start_date,
        Transaction.date       <  end_date,
        Transaction.type       == 'debit',
        Transaction.is_deleted == False,
        Transaction.is_spam    == False,
        Transaction.categorization_status != 'pending',
        Transaction.categorization_status != 'spam',
        Transaction.categorization_status != 'deleted',
        Transaction.purpose.isnot(None),
    ).all()

    category_totals = {}
    for t in txns:
        if t.purpose in _EXCLUDED_PURPOSES:
            continue
        if t.purpose.lower() == 'uncategorized':
            continue
        if is_own_account_transfer_row(t):
            continue
            
        cat_name = t.purpose
        category_totals[cat_name] = category_totals.get(cat_name, 0.0) + t.amount

    return sorted(
        [
            {"category": cat, "total": float(total)}
            for cat, total in category_totals.items()
            if total > 0
        ],
        key=lambda x: x["total"],
        reverse=True,
    )


# ── Write ─────────────────────────────────────────────────────────────────────

def create_manual_transaction(user_id: int, data: dict) -> tuple:
    """Returns (Transaction, accounts_list)."""
    try:
        amount = to_money(data.get("amount", 0))
    except (TypeError, ValueError):
        raise ValueError("Malformed monetary value")
    if amount <= 0:
        raise ValueError("Amount must be greater than 0")

    raw_type = (data.get("type") or "debit").strip().lower()
    if raw_type in ("expense", "debit"):
        t_type = "debit"
    elif raw_type in ("income", "credit"):
        t_type = "credit"
    else:
        raise ValueError("type must be debit or credit")

    acc_id = data.get("financial_account_id") or data.get("account_id")
    slug = (
        (data.get("account_balance_source") or data.get("wallet_slug") or data.get("balance_account_slug") or "")
        .strip().lower()
    )

    account = None
    if acc_id is not None and str(acc_id).strip():
        try:
            account = AccountBalance.query.filter_by(
                id=int(acc_id), user_id=user_id
            ).filter(AccountBalance.is_deleted.isnot(True)).with_for_update().first()
        except (ValueError, TypeError):
            raise ValueError("Invalid account ID")
        if not account:
            raise ValueError("Selected account not found")
    elif slug:
        account = AccountBalance.query.filter(
            AccountBalance.user_id == user_id,
            AccountBalance.is_deleted.isnot(True),
            (func.lower(AccountBalance.source) == slug) | (func.lower(AccountBalance.display_name) == slug)
        ).with_for_update().first()
        if not account:
            raise ValueError(f"Account '{slug}' not found")
    else:
        raise ValueError("account_balance_source is required (wallet slug, e.g. bank, easypaisa, cash)")

    curr_balance = to_money(account.current_balance or 0)
    if t_type == "debit" and curr_balance < amount:
        raise ValueError(INSUFFICIENT_BALANCE_MSG)

    # Atomically apply balance effect to the selected account only
    if t_type == "credit":
        account.current_balance = to_money(add_money(curr_balance, amount))
    else:
        account.current_balance = to_money(subtract_money(curr_balance, amount))
    account.last_updated = datetime.now()

    date_str = data.get("date")
    tx_date = datetime.utcnow()
    if date_str:
        try:
            tx_date = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            pass

    purpose_val = data.get("category") or data.get("purpose") or "Uncategorized"
    bank_reason = data.get("bank_reduction_reason")

    if purpose_val and purpose_val.strip().lower() == "bank reduction":
        if not bank_reason or not str(bank_reason).strip():
            raise ValueError("Bank Reduction reason is required")
        bank_reason = str(bank_reason).strip()
    else:
        bank_reason = None

    cat_id = data.get("category_id")
    if not cat_id and purpose_val:
        cat_obj = Category.query.filter_by(name=purpose_val).first()
        if cat_obj:
            cat_id = cat_obj.id

    new_tx = Transaction(
        user_id=user_id,
        source="manual",
        date=tx_date,
        amount=amount,
        type=t_type,
        purpose=purpose_val,
        category_id=cat_id,
        bank_reduction_reason=bank_reason,
        sender=data.get("sender") or ("Self" if t_type == "debit" else (data.get("notes") or "Manual Entry")),
        receiver=data.get("receiver") or data.get("recipient") or ("Me" if t_type == "credit" else "Merchant"),
        transaction_id=data.get("transaction_id"),
        notes=data.get("notes", ""),
        categorization_status="confirmed",
        account_balance_source=account.source,
        balance_applied=True,
        receipt_id=data.get("receipt_id"),
    )
    db.session.add(new_tx)
    db.session.flush()

    ensure_account_balance_row(slug, user_id=user_id)
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

    from services.account_service import get_all_accounts
    return new_tx, get_all_accounts(user_id)


def soft_delete_transaction(user_id: int, txn_id: int):
    tx = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if not tx:
        raise LookupError("Transaction not found")
    reverse_transaction_ledger(tx, respect_manual_lock=False)
    tx.is_deleted = True
    tx.categorization_status = 'deleted'
    db.session.commit()

    from services.account_service import get_all_accounts
    return get_all_accounts(user_id)


def mark_spam(user_id: int, txn_id: int):
    tx = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if not tx:
        raise LookupError("Transaction not found")
    reverse_transaction_ledger(tx, respect_manual_lock=False)
    tx.is_spam = True
    tx.categorization_status = 'spam'
    db.session.commit()


def update_transaction_category(user_id: int, txn_id: int, data: dict):
    txn = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if not txn:
        raise LookupError("Transaction not found")

    acc_id = data.get("financial_account_id") or data.get("account_id")
    slug_hint = ""
    if acc_id is not None and str(acc_id).strip():
        try:
            acc_obj = AccountBalance.query.filter_by(id=int(acc_id), user_id=user_id).filter(AccountBalance.is_deleted.isnot(True)).first()
            if acc_obj:
                slug_hint = acc_obj.source
        except (ValueError, TypeError):
            pass

    if not slug_hint:
        slug_hint = (
            (data.get("account_balance_source") or data.get("balance_account_slug") or "")
            .strip().lower()
        )

    prev_status = txn.categorization_status
    will_finalize = ("category_id" in data) or ("purpose" in data)
    was_applied = bool(getattr(txn, "balance_applied", False))

    new_amount = to_money(data["amount"]) if ("amount" in data and data["amount"] is not None) else to_money(txn.amount)
    if "amount" in data and data["amount"] is not None and new_amount <= 0:
        raise ValueError("Amount must be greater than 0")

    raw_type = (data.get("type") or txn.type or "debit").strip().lower()
    if raw_type in ("expense", "debit"):
        new_type = "debit"
    elif raw_type in ("income", "credit"):
        new_type = "credit"
    else:
        raise ValueError("type must be debit or credit")

    new_slug = slug_hint or (txn.account_balance_source or "").strip().lower() or "bank"
    target_acc = ensure_account_balance_row(new_slug, user_id=user_id)

    will_be_active = (
        not getattr(txn, "is_deleted", False)
        and not getattr(txn, "is_spam", False)
        and (txn.categorization_status != "pending" or will_finalize)
    )

    if will_be_active and new_type == "debit" and target_acc:
        available = to_money(target_acc.current_balance or 0)
        if was_applied and (txn.account_balance_source or "").strip().lower() == target_acc.source:
            if txn.type == "debit":
                available = to_money(add_money(available, txn.amount))
            elif txn.type == "credit":
                available = to_money(subtract_money(available, txn.amount))
        if available < new_amount:
            raise ValueError(INSUFFICIENT_BALANCE_MSG)

    if was_applied:
        reverse_transaction_ledger(txn, respect_manual_lock=False)

    if "amount" in data and data["amount"] is not None:
        txn.amount = new_amount

    if "type" in data and data["type"] in ["debit", "credit"]:
        txn.type = data["type"]

    if slug_hint:
        txn.account_balance_source = slug_hint

    if "date" in data and data["date"]:
        try:
            if isinstance(data["date"], str):
                from dateutil.parser import parse
                txn.date = parse(data["date"])
            elif isinstance(data["date"], datetime):
                txn.date = data["date"]
        except Exception:
            pass

    if "sender" in data:
        txn.sender = data["sender"]

    if "receiver" in data:
        txn.receiver = data["receiver"]

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

    current_purpose = txn.purpose or ""
    if current_purpose.strip().lower() == "bank reduction":
        if "bank_reduction_reason" in data:
            reason = data["bank_reduction_reason"]
            if not reason or not str(reason).strip():
                raise ValueError("Bank Reduction reason is required")
            txn.bank_reduction_reason = str(reason).strip()
        elif not txn.bank_reduction_reason:
            raise ValueError("Bank Reduction reason is required")
    else:
        if "purpose" in data or "category_id" in data:
            txn.bank_reduction_reason = None

    should_apply_ledger = (
        not getattr(txn, "is_deleted", False)
        and not getattr(txn, "is_spam", False)
        and txn.categorization_status != "pending"
    )

    if should_apply_ledger and (was_applied or (prev_status == "pending" and will_finalize)):
        apply_pending_transaction_ledger(txn, balance_slug_override=slug_hint or None, respect_manual_lock=False)

    db.session.commit()
    return txn


def bulk_categorize(user_id: int, transaction_ids: list, category_id: int, slug_hint: str = ""):
    cat = Category.query.get(category_id)
    if not cat:
        raise LookupError("Category not found")
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.filter_by(id=tx_id, user_id=user_id).first()
        if tx:
            if tx.categorization_status == 'pending' and not getattr(tx, "balance_applied", False):
                apply_pending_transaction_ledger(tx, balance_slug_override=slug_hint or None)
            tx.category_id = category_id
            tx.purpose = cat.name
            tx.categorization_status = 'manual'
            updated += 1
    db.session.commit()
    return updated


def bulk_delete(user_id: int, transaction_ids: list) -> int:
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.filter_by(id=tx_id, user_id=user_id).first()
        if tx:
            reverse_transaction_ledger(tx, respect_manual_lock=False)
            tx.is_deleted = True
            updated += 1
    db.session.commit()
    return updated


def bulk_spam(user_id: int, transaction_ids: list) -> int:
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.filter_by(id=tx_id, user_id=user_id).first()
        if tx:
            reverse_transaction_ledger(tx, respect_manual_lock=False)
            tx.is_spam = True
            updated += 1
    db.session.commit()
    return updated


def get_total_expenses_for_current_month(user_id: int):
    dt = datetime.now()
    year, month = dt.year, dt.month
    month_str = dt.strftime("%Y-%m")

    total_expenses = sum_month_expenses(user_id, year, month)

    total_debits = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id    == user_id,
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type       == 'debit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    total_credits = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id    == user_id,
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.type       == 'credit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    # Persist to user's Budget
    budget_entry = Budget.query.filter_by(user_id=user_id, month=month_str).first()
    if budget_entry:
        budget_entry.total_expenses = total_expenses
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return {
        "month":          month_str,
        "total_expense":  total_expenses,
        "total_debits":   total_debits,
        "total_credits":  total_credits,
    }


def process_receipt_upload(file, current_user):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    if not file or not file.filename:
        raise ValueError("No file provided")

    if not allowed_file(file.filename):
        raise ValueError("File type not allowed")

    upload_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'attachments', 'receipts')
    os.makedirs(upload_folder, exist_ok=True)

    timestamp_str = str(datetime.utcnow().timestamp()).replace('.', '')
    filename  = secure_filename(f"{timestamp_str}_{current_user.id}_{file.filename}")
    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    receipt = UploadedReceipt(
        user_id=current_user.id,
        filename=filename,
        file_path=file_path,
        mime_type=file.mimetype,
        ocr_status='pending'
    )
    db.session.add(receipt)
    db.session.commit()

    try:
        raw_text = perform_ocr(file_path)
        print("====== RAW TEXT FROM OCR ======")
        print(repr(raw_text))
        print("===============================")
        receipt.ocr_raw_text = raw_text
        receipt.ocr_status   = 'completed'
        extracted_data       = parse_receipt_text(raw_text)
    except Exception as e:
        import traceback
        print("====== OCR / PARSER PIPELINE FAILED ======")
        print(traceback.format_exc())
        print("==========================================")
        receipt.ocr_status    = 'failed'
        receipt.ocr_raw_text  = None
        extracted_data        = {}

    db.session.commit()

    return {
        "success":        True,
        "receipt_id":     receipt.id,
        "ocr_status":     receipt.ocr_status,
        "extracted_data": extracted_data,
    }