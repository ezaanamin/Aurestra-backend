# services/transaction_service.py  —  Transaction business logic (Phase 3: user-scoped)

from datetime import datetime, timedelta, date
from sqlalchemy import func, extract, case, desc
from dateutil.relativedelta import relativedelta
from database import db
from model import Transaction, Category, AccountBalance, Budget, UploadedReceipt
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from ledger_sync import apply_pending_transaction_ledger, ensure_account_balance_row, log_wallet_attribution
from decorator.helpers import calculate_month_expenses

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
        .order_by(desc(Transaction.date))
        .limit(limit)
        .all()
    )


def get_uncategorized(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id              == user_id,
        Transaction.categorization_status == 'pending',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).order_by(desc(Transaction.date)).all()


def get_spam(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id    == user_id,
        Transaction.is_spam    == True,
        Transaction.is_deleted.isnot(True),
    ).order_by(desc(Transaction.date)).all()


def get_categorized(user_id: int):
    return Transaction.query.filter(
        Transaction.user_id              == user_id,
        Transaction.categorization_status != 'pending',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).order_by(desc(Transaction.date)).all()


def get_top_categories(user_id: int, period: str = 'month'):
    _EXCLUDED_PURPOSES = {'Self transfer', 'self transfer', 'Self Transfer'}

    query = db.session.query(
        Transaction.category_id.label("category_id"),
        Transaction.purpose.label("category"),
        func.sum(Transaction.amount).label("total_spent"),
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'debit',
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.purpose.notin_(_EXCLUDED_PURPOSES),
        Transaction.is_deleted == False,
        Transaction.is_spam    == False,
        Transaction.categorization_status != 'pending',
        Transaction.categorization_status != 'spam',
        Transaction.categorization_status != 'deleted',
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
        query
        .group_by(Transaction.category_id, Transaction.purpose)
        .having(func.sum(Transaction.amount) > 0)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10)
        .all()
    )
    return [{"category": r.category, "total_spent": r.total_spent} for r in rows]


def get_analytics_trend(user_id: int, period: str = 'month'):
    data_points = []
    expense_expr = func.sum(case(
        (Transaction.type == 'debit',  Transaction.amount),
        (Transaction.type == 'credit', -Transaction.amount),
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
    rows = db.session.query(
        Transaction.category_id.label('category_id'),
        Transaction.purpose.label('category'),
        func.sum(Transaction.amount).label('total'),
    ).filter(
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
        Transaction.purpose.notin_(_EXCLUDED_PURPOSES),
        Transaction.purpose.ilike('Uncategorized') == False,
        exclude_own_account_transfer_sql(),
    ).group_by(Transaction.category_id, Transaction.purpose).all()

    return sorted(
        [
            {"category": r.category, "total": float(r.total or 0)}
            for r in rows
            if (r.total or 0) > 0
        ],
        key=lambda x: x["total"],
        reverse=True,
    )


# ── Write ─────────────────────────────────────────────────────────────────────

def create_manual_transaction(user_id: int, data: dict) -> tuple:
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

    if t_type == "debit":
        account = ensure_account_balance_row(slug)
        if float(account.current_balance or 0.0) < amount:
            raise ValueError(f"Insufficient funds in wallet '{account.display_name or slug}'. Balance is {float(account.current_balance or 0.0)}.")

    date_str = data.get("date")
    tx_date = datetime.utcnow()
    if date_str:
        try:
            tx_date = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            pass

    new_tx = Transaction(
        user_id=user_id,
        source="manual", date=tx_date, amount=amount, type=t_type,
        purpose=data.get("category", "Uncategorized"),
        sender=data.get("sender") or "Manual Entry",
        receiver=data.get("receiver") or data.get("recipient") or ("Me" if t_type == "credit" else "Merchant"),
        transaction_id=data.get("transaction_id"),
        notes=data.get("notes", ""),
        categorization_status="confirmed",
        account_balance_source=slug,
        balance_applied=False,
        receipt_id=data.get("receipt_id"),
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
        for acc in AccountBalance.query
        .filter_by(user_id=user_id)
        .order_by(AccountBalance.sort_order, AccountBalance.id).all()
    ]
    return new_tx, accounts


def soft_delete_transaction(user_id: int, txn_id: int):
    tx = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if not tx:
        raise LookupError("Transaction not found")
    tx.is_deleted = True
    tx.categorization_status = 'deleted'
    db.session.commit()


def mark_spam(user_id: int, txn_id: int):
    tx = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
    if not tx:
        raise LookupError("Transaction not found")
    tx.is_spam = True
    tx.categorization_status = 'spam'
    db.session.commit()


def update_transaction_category(user_id: int, txn_id: int, data: dict):
    txn = Transaction.query.filter_by(id=txn_id, user_id=user_id).first()
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
            tx.is_deleted = True
            updated += 1
    db.session.commit()
    return updated


def bulk_spam(user_id: int, transaction_ids: list) -> int:
    updated = 0
    for tx_id in transaction_ids:
        tx = Transaction.query.filter_by(id=tx_id, user_id=user_id).first()
        if tx:
            tx.is_spam = True
            updated += 1
    db.session.commit()
    return updated


def get_total_expenses_for_current_month(user_id: int):
    dt = datetime.now()
    year, month = dt.year, dt.month
    month_str = dt.strftime("%Y-%m")

    total_expenses = calculate_month_expenses(year, month, user_id=user_id)

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