# services/live_state_service.py  —  LIVE_STATE financial snapshot logic
#
# Answers "what is happening RIGHT NOW" questions.
# Every function queries only current/today/this-pay-cycle data.
# Gracefully returns zero/empty instead of raising when no data exists.

from datetime import datetime, timedelta, date
from sqlalchemy import func, desc
from database import db
from model import Transaction, AccountBalance
from transfer_matching import exclude_own_account_transfer_sql


# ── Shared base filter ────────────────────────────────────────────────────────

def _safe_balance(raw: float) -> float:
    """Clamp account balance to 0 — negative DB values are treated as zero."""
    return max(0.0, float(raw or 0.0))


def _active_txns():
    """Base query: exclude deleted, spam, and internal transfers."""
    return Transaction.query.filter(
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        exclude_own_account_transfer_sql(),
    )


def _today_range():
    """Return (start_of_today, end_of_today) as datetime objects."""
    today = date.today()
    start = datetime(today.year, today.month, today.day, 0, 0, 0)
    end   = datetime(today.year, today.month, today.day, 23, 59, 59, 999999)
    return start, end


# ── Helper: serialise a Transaction row to a plain dict ──────────────────────

def _txn_snapshot(t: Transaction) -> dict:
    """Minimal live-state representation of a transaction."""
    return {
        "id":          t.id,
        "amount":      round(t.amount, 2),
        "type":        t.type,
        "source":      t.source,
        "sender":      t.sender,
        "receiver":    t.receiver,
        "purpose":     t.purpose,
        "notes":       t.notes,
        "date":        t.date.isoformat()       if t.date       else None,
        "created_at":  t.created_at.isoformat() if t.created_at else None,
        "account_balance_source": t.account_balance_source,
        "categorization_status":  t.categorization_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Current balance (sum of all AccountBalance rows)
# ─────────────────────────────────────────────────────────────────────────────

def get_current_balance() -> dict:
    """
    LIVE_STATE: What is my current balance?
    Returns the live balance for every account plus a combined total.
    """
    accounts = AccountBalance.query.order_by(
        AccountBalance.sort_order, AccountBalance.id
    ).all()

    account_list = [
        {
            "source":       a.source,
            "display_name": a.display_name or a.source,
            "account_kind": a.account_kind,
            "balance":      round(_safe_balance(a.current_balance), 2),
            "last_updated": a.last_updated.isoformat() if a.last_updated else None,
        }
        for a in accounts
    ]

    total = round(sum(_safe_balance(a.current_balance) for a in accounts), 2)

    return {
        "total_balance": total,
        "account_count": len(accounts),
        "accounts":      account_list,
        "as_of":         datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Available balance (total minus pending/held debits)
# ─────────────────────────────────────────────────────────────────────────────

def get_available_balance() -> dict:
    """
    LIVE_STATE: How much is actually available right now?
    Deducts any pending (not yet settled) debit transactions from the
    current balance so the user sees cleared + uncleared exposure.
    """
    accounts  = AccountBalance.query.all()
    total_bal = round(sum(_safe_balance(a.current_balance) for a in accounts), 2)

    # Pending debits that haven't cleared yet (categorization_status = 'pending', type = debit)
    pending_debits = (
        Transaction.query
        .filter(
            Transaction.is_deleted             != True,
            Transaction.is_spam                != True,
            Transaction.type                   == "debit",
            Transaction.categorization_status  == "pending",
        )
        .all()
    )

    held_total = round(sum(t.amount for t in pending_debits), 2)
    available  = round(total_bal - held_total, 2)

    return {
        "total_balance":   total_bal,
        "held_amount":     held_total,
        "available_balance": available,
        "pending_count":   len(pending_debits),
        "as_of":           datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Last transaction
# ─────────────────────────────────────────────────────────────────────────────

def get_last_transaction() -> dict:
    """
    LIVE_STATE: What was my last transaction?
    Returns the single most-recent non-deleted, non-spam transaction.
    """
    txn = (
        _active_txns()
        .order_by(desc(Transaction.date), desc(Transaction.id))
        .first()
    )

    if not txn:
        return {"transaction": None, "found": False}

    return {"transaction": _txn_snapshot(txn), "found": True}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pending transactions
# ─────────────────────────────────────────────────────────────────────────────

def get_pending_transactions() -> dict:
    """
    LIVE_STATE: Are there any pending charges on my account?
    Returns all transactions still in categorization_status = 'pending'.
    These have not yet been fully processed/categorised.
    """
    pending = (
        Transaction.query
        .filter(
            Transaction.is_deleted            != True,
            Transaction.is_spam               != True,
            Transaction.categorization_status == "pending",
        )
        .order_by(desc(Transaction.date))
        .all()
    )

    total_pending_amount = round(sum(t.amount for t in pending), 2)

    return {
        "count":        len(pending),
        "total_amount": total_pending_amount,
        "transactions": [_txn_snapshot(t) for t in pending],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Today's transactions
# ─────────────────────────────────────────────────────────────────────────────

def get_todays_transactions() -> dict:
    """
    LIVE_STATE: What transactions happened today?
    Filters by Transaction.date falling within today's calendar day (UTC).
    """
    start, end = _today_range()

    txns = (
        _active_txns()
        .filter(Transaction.date >= start, Transaction.date <= end)
        .order_by(desc(Transaction.date))
        .all()
    )

    credits = [t for t in txns if t.type == "credit"]
    debits  = [t for t in txns if t.type == "debit"]

    return {
        "date":           date.today().isoformat(),
        "total_count":    len(txns),
        "credit_count":   len(credits),
        "debit_count":    len(debits),
        "total_in":       round(sum(t.amount for t in credits), 2),
        "total_out":      round(sum(t.amount for t in debits),  2),
        "transactions":   [_txn_snapshot(t) for t in txns],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. Has salary arrived this pay period?
# ─────────────────────────────────────────────────────────────────────────────

_SALARY_KEYWORDS = [
    "salary", "salry", "payroll", "pay slip", "payslip", "wage",
    "stipend", "emolument", "remuneration", "monthly pay",
]


def has_salary_arrived() -> dict:
    """
    LIVE_STATE: Did my salary arrive yet this month?
    Looks for a credit transaction this calendar month whose purpose/notes/sender
    contains any common salary or payroll keyword (case-insensitive).
    Returns a boolean plus the matching transaction if found.
    """
    today = date.today()
    month_start = datetime(today.year, today.month, 1, 0, 0, 0)

    candidates = (
        _active_txns()
        .filter(
            Transaction.type   == "credit",
            Transaction.date   >= month_start,
        )
        .order_by(desc(Transaction.amount))  # biggest credit first
        .all()
    )

    def _is_salary(t: Transaction) -> bool:
        haystack = " ".join(filter(None, [
            t.purpose or "", t.notes or "",
            t.sender  or "", t.receiver or "",
        ])).lower()
        return any(kw in haystack for kw in _SALARY_KEYWORDS)

    salary_txn = next((t for t in candidates if _is_salary(t)), None)

    return {
        "arrived":          salary_txn is not None,
        "pay_period_start": month_start.date().isoformat(),
        "as_of":            datetime.utcnow().isoformat(),
        "transaction":      _txn_snapshot(salary_txn) if salary_txn else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. Today's spending total (debits only)
# ─────────────────────────────────────────────────────────────────────────────

def get_todays_spending_total() -> dict:
    """
    LIVE_STATE: How much have I spent today?
    Sums all debit transactions where Transaction.date is today (UTC).
    Returns 0.0 gracefully when nothing has been spent yet.
    """
    start, end = _today_range()

    total = (
        db.session.query(func.sum(Transaction.amount))
        .filter(
            Transaction.is_deleted != True,
            Transaction.is_spam    != True,
            Transaction.type       == "debit",
            Transaction.date       >= start,
            Transaction.date       <= end,
            exclude_own_account_transfer_sql(),
        )
        .scalar()
    ) or 0.0

    count = (
        _active_txns()
        .filter(
            Transaction.type == "debit",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .count()
    )

    return {
        "date":          date.today().isoformat(),
        "total_spent":   round(float(total), 2),
        "debit_count":   count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8. Subscriptions / recurring charges this week
# ─────────────────────────────────────────────────────────────────────────────

_SUBSCRIPTION_KEYWORDS = [
    "subscription", "subscribe", "netflix", "spotify", "youtube premium",
    "amazon prime", "apple", "google one", "hulu", "disney", "membership",
    "renewal", "auto-renew", "auto renew", "recurring", "monthly fee",
    "annual fee", "premium", "plus plan",
]


def get_subscriptions_charged_this_week() -> dict:
    """
    LIVE_STATE: What subscriptions were charged this week (last 7 days)?
    Identifies debit transactions whose purpose/notes/sender match common
    subscription keywords.  Returns an empty list — not an error — when none found.
    """
    week_ago = datetime.utcnow() - timedelta(days=7)

    candidates = (
        _active_txns()
        .filter(
            Transaction.type == "debit",
            Transaction.date >= week_ago,
        )
        .order_by(desc(Transaction.date))
        .all()
    )

    def _is_subscription(t: Transaction) -> bool:
        haystack = " ".join(filter(None, [
            t.purpose or "", t.notes or "",
            t.sender  or "", t.receiver or "",
        ])).lower()
        return any(kw in haystack for kw in _SUBSCRIPTION_KEYWORDS)

    subs = [t for t in candidates if _is_subscription(t)]

    return {
        "period_start": week_ago.date().isoformat(),
        "period_end":   date.today().isoformat(),
        "count":        len(subs),
        "total_charged": round(sum(t.amount for t in subs), 2),
        "subscriptions": [_txn_snapshot(t) for t in subs],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. Bill / direct-debit payment status
# ─────────────────────────────────────────────────────────────────────────────

def check_bill_payment_status(bill_name: str) -> dict:
    """
    LIVE_STATE: Did my [bill_name] payment go through this month?
    Performs a case-insensitive substring search across purpose, notes,
    sender, and receiver within the current calendar month.
    Returns paid=True plus the matching transaction if found.
    """
    today       = date.today()
    month_start = datetime(today.year, today.month, 1, 0, 0, 0)
    needle      = bill_name.strip().lower()

    candidates = (
        _active_txns()
        .filter(
            Transaction.type == "debit",
            Transaction.date >= month_start,
        )
        .order_by(desc(Transaction.date))
        .all()
    )

    def _matches(t: Transaction) -> bool:
        haystack = " ".join(filter(None, [
            t.purpose or "", t.notes or "",
            t.sender  or "", t.receiver or "",
        ])).lower()
        return needle in haystack

    match = next((t for t in candidates if _matches(t)), None)

    return {
        "bill_name":        bill_name,
        "pay_period_start": month_start.date().isoformat(),
        "as_of":            datetime.utcnow().isoformat(),
        "paid":             match is not None,
        "transaction":      _txn_snapshot(match) if match else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 10. Overdraft / credit status
# ─────────────────────────────────────────────────────────────────────────────

def get_overdraft_or_credit_status() -> dict:
    """
    LIVE_STATE: Am I in overdraft or using a credit facility?
    Identifies accounts with a negative current_balance (overdraft) and
    accounts typed as 'credit' so the caller knows live credit exposure.
    """
    accounts = AccountBalance.query.all()

    overdrawn = [
        {
            "source":        a.source,
            "display_name":  a.display_name or a.source,
            "balance":       round(a.current_balance, 2),
            "overdrawn_by":  round(abs(a.current_balance), 2),
        }
        for a in accounts
        if a.current_balance < 0
    ]

    # Accounts explicitly typed as credit cards / credit lines
    credit_accounts = [
        {
            "source":            a.source,
            "display_name":      a.display_name or a.source,
            "current_balance":   round(a.current_balance, 2),
            "account_kind":      a.account_kind,
        }
        for a in accounts
        if (a.account_kind or "").lower() in ("credit", "credit_card", "line_of_credit")
    ]

    total_negative = round(sum(a.current_balance for a in accounts if a.current_balance < 0), 2)

    return {
        "is_overdrawn":         len(overdrawn) > 0,
        "overdrawn_accounts":   overdrawn,
        "total_overdrawn_amount": abs(total_negative),
        "credit_accounts":      credit_accounts,
        "as_of":                datetime.utcnow().isoformat(),
    }

def get_total_balance():
    accounts = AccountBalance.query.all()

    total = round(
        sum(_safe_balance(a.current_balance) for a in accounts),
        2
    )

    return {
        "total_balance": total,
        "account_count": len(accounts),
        "as_of": datetime.utcnow().isoformat(),
    }

def get_online_status():
    return {
        "online": True,
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }
