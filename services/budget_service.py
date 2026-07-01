# services/budget_service.py  —  Budget business logic (Phase 3: user-scoped)

from datetime import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, extract, case
from database import db
from model import Budget, MonthlyBalance, Transaction, AccountBalance
from transfer_matching import exclude_own_account_transfer_sql


def get_current_budget(user_id: int):
    month  = datetime.now().strftime("%Y-%m")
    budget = Budget.query.filter_by(user_id=user_id, month=month).first()
    if not budget:
        raise LookupError(f"No budget found for {month}.")
    spending_limit = (budget.needs or 0) + (budget.wants or 0)
    return {
        "month":          budget.month,
        "total_budget":   budget.total_budget,
        "needs":          budget.needs,
        "wants":          budget.wants,
        "saving":         budget.saving,
        "spending_limit": spending_limit,
        "created_at":     budget.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_budget(user_id: int, data: dict):
    month = datetime.now().strftime("%Y-%m")
    for field in ('income', 'needs', 'wants', 'saving'):
        if field not in data:
            raise ValueError(f"Missing '{field}' in request body.")

    total  = float(data['income'])
    needs  = float(data['needs'])
    wants  = float(data['wants'])
    saving = float(data['saving'])

    existing = Budget.query.filter_by(user_id=user_id, month=month).first()
    if existing:
        existing.total_budget = total
        existing.needs  = needs
        existing.wants  = wants
        existing.saving = saving
        db.session.commit()
        created = False
    else:
        db.session.add(Budget(
            user_id=user_id,
            month=month, total_budget=total,
            needs=needs, wants=wants, saving=saving,
        ))
        db.session.commit()
        created = True

    return {
        "month": month, "total_budget": total,
        "needs": needs, "wants": wants, "saving": saving,
    }, created


def set_salary(user_id: int, amount: float, month_str: str = None):
    if not month_str:
        month_str = datetime.now().strftime("%Y-%m")
    needs          = amount * 0.50
    wants          = amount * 0.30
    savings        = amount * 0.20
    spending_limit = needs + wants

    budget = Budget.query.filter_by(user_id=user_id, month=month_str).first()
    if not budget:
        budget = Budget(user_id=user_id, month=month_str, total_budget=spending_limit)
        db.session.add(budget)
    else:
        budget.total_budget = spending_limit
    budget.needs  = needs
    budget.wants  = wants
    budget.saving = savings
    db.session.commit()
    return budget


def get_budget_history(user_id: int, months_to_fetch: int = 4):
    today = datetime.now()
    months_list = [
        (today - relativedelta(months=i)).strftime("%Y-%m")
        for i in range(months_to_fetch)
    ]

    budget_map  = {b.month: b for b in Budget.query.filter(
        Budget.user_id == user_id, Budget.month.in_(months_list)
    ).all()}
    balance_map = {m.month: m for m in MonthlyBalance.query.filter(
        MonthlyBalance.user_id == user_id, MonthlyBalance.month.in_(months_list)
    ).all()}

    history = []
    for month_str in months_list:
        dt         = datetime.strptime(month_str, "%Y-%m")
        budget_rec = budget_map.get(month_str)
        balance_rec = balance_map.get(month_str)

        fresh_expense = db.session.query(
            func.sum(case(
                (Transaction.type == 'debit',  Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0,
            ))
        ).filter(
            Transaction.user_id == user_id,
            extract('year',  Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.is_deleted != True,
            Transaction.is_spam    != True,
            Transaction.categorization_status != 'pending',
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        fresh_income = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            extract('year',  Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type   == 'credit',
            Transaction.is_deleted != True,
            Transaction.is_spam    != True,
            Transaction.categorization_status != 'pending',
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        stored_expense = getattr(balance_rec, 'expense', 0.0) or 0.0
        final_expense  = fresh_expense if fresh_expense > 0 else stored_expense
        final_savings  = fresh_income - final_expense

        history.append({
            "month": month_str,
            "budget": {
                "total_budget": getattr(budget_rec, 'total_budget', 0.0),
                "needs":        getattr(budget_rec, 'needs',  0.0),
                "wants":        getattr(budget_rec, 'wants',  0.0),
                "saving":       getattr(budget_rec, 'saving', 0.0),
            },
            "actual": {
                "expense":         final_expense,
                "savings":         final_savings,
                "closing_balance": getattr(balance_rec, 'closing_balance', 0.0),
            },
        })
    return history


def get_monthly_summary(user_id: int):
    current_month = datetime.now().strftime("%Y-%m")
    dt            = datetime.now()

    dynamic_expense = db.session.query(
        func.sum(case(
            (Transaction.type == 'debit',  Transaction.amount),
            (Transaction.type == 'credit', -Transaction.amount),
            else_=0,
        ))
    ).filter(
        Transaction.user_id == user_id,
        extract('year',  Transaction.date) == dt.year,
        extract('month', Transaction.date) == dt.month,
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    dynamic_income = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        extract('year',  Transaction.date) == dt.year,
        extract('month', Transaction.date) == dt.month,
        Transaction.type   == 'credit',
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    ).scalar() or 0.0

    budget_entry  = Budget.query.filter_by(user_id=user_id, month=current_month).first()
    final_income  = budget_entry.total_budget if (budget_entry and budget_entry.total_budget > 0) else dynamic_income
    final_savings = final_income - dynamic_expense

    total_current_balance = sum(
        acc.current_balance
        for acc in AccountBalance.query.filter_by(user_id=user_id).all()
    )

    summary = MonthlyBalance.query.filter_by(user_id=user_id, month=current_month).first()
    if not summary:
        summary = MonthlyBalance(
            user_id=user_id, source="auto-dynamic", month=current_month,
            opening_balance=0, closing_balance=total_current_balance,
            expense=dynamic_expense, savings=final_savings, fetched_at=dt,
        )
        db.session.add(summary)
    else:
        summary.closing_balance = total_current_balance
        summary.expense  = dynamic_expense
        summary.savings  = final_savings
        summary.fetched_at = dt

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return {
        "month":           current_month,
        "opening_balance": summary.opening_balance,
        "closing_balance": summary.closing_balance,
        "total_expense":   dynamic_expense,
        "total_income":    final_income,
        "total_savings":   final_savings,
        "fetched_at":      dt.strftime("%d %b %Y %H:%M:%S"),
    }
