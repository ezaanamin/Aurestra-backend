# services/budget_service.py  —  Budget business logic (Phase 3: user-scoped)

from datetime import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, extract, case
from database import db
from model import Budget, MonthlyBalance, Transaction, AccountBalance
from transfer_matching import exclude_own_account_transfer_sql
from decorator.helpers import sum_month_expenses, sum_month_income
from utils.money import to_money, add_money, subtract_money, abs_money


def get_current_budget(user_id: int, month_str: str = None):
    if not month_str:
        month_str = datetime.now().strftime("%Y-%m")
    
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
    except Exception:
        dt = datetime.now()
        month_str = dt.strftime("%Y-%m")

    budget = Budget.query.filter_by(user_id=user_id, month=month_str).first()
    if not budget:
        # Fallback to general budget or default values for month
        spending_limit = 0.0
        budget_needs = 0.0
        budget_wants = 0.0
        budget_saving = 0.0
        created_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        spending_limit = (budget.needs or 0) + (budget.wants or 0)
        budget_needs = budget.needs
        budget_wants = budget.wants
        budget_saving = budget.saving
        created_at_str = budget.created_at.strftime("%Y-%m-%d %H:%M:%S")

    from model import CategoryBucketMapping, Category, Transaction
    
    # Calculate spent amounts per bucket for SPECIFIC month_str
    txns = Transaction.query.filter(
        Transaction.user_id == user_id,
        extract('year', Transaction.date) == dt.year,
        extract('month', Transaction.date) == dt.month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        Transaction.type == 'debit'
    ).all()
    
    # Get all categories and their bucket mappings
    from sqlalchemy import or_, and_
    categories = Category.query.filter(
        or_(
            Category.user_id == user_id,
            and_(Category.is_default == True, Category.user_id == None)
        )
    ).all()
    
    mappings = CategoryBucketMapping.query.filter_by(user_id=user_id).all()
    mapping_dict = {m.category_id: m.bucket for m in mappings}
    

    
    # Ensure default mapping rules apply if missing
    def assign_bucket(cat):
        if cat.id in mapping_dict:
            return mapping_dict[cat.id]
        name = cat.name.lower()
        bkt = None
        source = 'seed_default'
        if any(word in name for word in ['rent', 'utilit', 'grocer', 'insur', 'bill', 'health']):
            bkt = 'needs'
        elif any(word in name for word in ['din', 'entertainment', 'shop', 'coffee', 'subscript', 'hobby', 'fun']):
            bkt = 'wants'
        elif any(word in name for word in ['emergenc', 'invest', 'goal', 'saving']):
            bkt = 'savings'
        
        if not bkt:
            # Query LLM via centralized client
            try:
                from services.llm_client import generate_llm
                prompt = f"Classify this budget category into exactly one of: Needs, Wants, Savings. Category: '{cat.name}'. Respond with only the bucket name."
                res_data = generate_llm(prompt=prompt, options={"temperature": 0.0}, timeout=5)
                reply = res_data.get("response", "").strip().lower()
                if 'need' in reply: bkt = 'needs'
                elif 'want' in reply: bkt = 'wants'
                elif 'saving' in reply: bkt = 'savings'
            except Exception:
                pass
            
            if bkt:
                source = 'ai_suggested'
            else:
                bkt = 'wants'
                source = 'ai_suggested'
                
        # Cache it
        try:
            m = CategoryBucketMapping(user_id=user_id, category_id=cat.id, bucket=bkt, source=source)
            db.session.add(m)
            db.session.commit()
            mapping_dict[cat.id] = bkt
        except Exception:
            db.session.rollback()
            
        return bkt
    
    cat_buckets = {}
    cat_details = {}
    for c in categories:
        bkt = assign_bucket(c)
        cat_buckets[c.id] = bkt
        cat_details[c.id] = c.to_dict()
    
    spent_by_bucket = {'needs': 0.0, 'wants': 0.0, 'savings': 0.0}
    for t in txns:
        if t.category_id in cat_buckets:
            spent_by_bucket[cat_buckets[t.category_id]] += t.amount
        else:
            spent_by_bucket['wants'] += t.amount # fallback
            
    # Group categories by bucket for the UI
    grouped_cats = {'needs': [], 'wants': [], 'savings': []}
    for c in categories:
        bkt = cat_buckets.get(c.id, 'wants')
        grouped_cats[bkt].append({
            "id": c.id,
            "name": c.name,
            "icon": c.icon,
            "color": c.color
        })
        
    return {
        "month":          month_str,
        "total_budget":   (budget_needs + budget_wants + budget_saving) if budget else 0.0,
        "needs":          budget_needs,
        "wants":          budget_wants,
        "saving":         budget_saving,
        "spending_limit": spending_limit,
        "created_at":     created_at_str,
        "spent":          spent_by_bucket,
        "categories":     grouped_cats
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

        fresh_expense = sum_month_expenses(user_id, dt.year, dt.month)
        fresh_income = sum_month_income(user_id, dt.year, dt.month)

        stored_expense = abs(float(getattr(balance_rec, 'expense', 0.0) or 0.0))
        final_expense = fresh_expense if fresh_expense > 0 else stored_expense
        final_savings = fresh_income - final_expense

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

    dynamic_expense = sum_month_expenses(user_id, dt.year, dt.month)
    dynamic_income = sum_month_income(user_id, dt.year, dt.month)
    dynamic_expense = abs_money(sum_month_expenses(user_id, dt.year, dt.month))
    dynamic_income = to_money(sum_month_income(user_id, dt.year, dt.month))

    budget_entry  = Budget.query.filter_by(user_id=user_id, month=current_month).first()
    final_income  = budget_entry.total_budget if (budget_entry and budget_entry.total_budget > 0) else dynamic_income
    final_savings = final_income - dynamic_expense
    final_income  = dynamic_income
    final_savings = subtract_money(final_income, dynamic_expense)

    total_current_balance = sum(
        acc.current_balance
        for acc in AccountBalance.query.filter_by(user_id=user_id).all()
        for acc in AccountBalance.query.filter_by(user_id=user_id).filter(AccountBalance.is_deleted.isnot(True)).all()
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

    # Calculate rolling 30-day net worth change
    from model import NetWorthHistory
    from datetime import timedelta
    baseline_date = dt.date() - timedelta(days=30)
    baseline_record = NetWorthHistory.query.filter_by(user_id=user_id, date=baseline_date).first()
    
    nw_change = {"type": "new", "value": 0, "window": "past 30 days"}
    if baseline_record:
        if baseline_record.net_worth_value > 0:
            pct = ((total_current_balance - baseline_record.net_worth_value) / baseline_record.net_worth_value) * 100
            nw_change = {"type": "percentage", "value": round(pct, 1), "window": "past 30 days"}
        else:
            delta = total_current_balance - baseline_record.net_worth_value
            nw_change = {"type": "delta", "value": round(delta, 2), "window": "past 30 days"}

    return {
        "month":           current_month,
        "opening_balance": summary.opening_balance,
        "closing_balance": summary.closing_balance,
        "total_expense":   dynamic_expense,
        "total_income":    final_income,
        "total_savings":   final_savings,
        "net_cash_flow":   final_savings,
        "fetched_at":      dt.strftime("%d %b %Y %H:%M:%S"),
        "net_worth_change": nw_change,
    }
