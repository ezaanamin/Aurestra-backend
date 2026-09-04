import json
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from database import db
from model import Transaction, Budget, FinancialInsight, Category
import os

LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://llm.elyestra.org')
LLM_URL   = os.getenv('LLM_API_URL', 'https://llm.elyestra.org/api/generate')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:3b')


def _get_month_bounds(month_str):
    year, month = map(int, month_str.split('-'))
    start_date  = datetime(year, month, 1)
    end_date    = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start_date, end_date


def _get_month_totals(user_id: int, month_str: str):
    """Returns income, expense, category breakdowns, and txn count for a given month."""
    start_date, end_date = _get_month_bounds(month_str)

    transactions = Transaction.query.filter(
        Transaction.user_id    == user_id,
        Transaction.date       >= start_date,
        Transaction.date       <  end_date,
        Transaction.is_deleted == False,
        Transaction.is_spam    == False,
    ).all()

    category_map      = {c.id: c.name for c in Category.query.filter_by(user_id=user_id).all()}
    expense_categories = {}
    income_sources     = {}
    largest_expense    = None

    for t in transactions:
        cat_name = category_map.get(t.category_id, 'Uncategorized') if t.category_id else 'Uncategorized'
        if t.type == 'debit':
            expense_categories[cat_name] = expense_categories.get(cat_name, 0) + t.amount
            if largest_expense is None or t.amount > largest_expense['amount']:
                largest_expense = {'amount': t.amount, 'category': cat_name, 'date': t.date.strftime('%Y-%m-%d')}
        else:
            income_sources[cat_name] = income_sources.get(cat_name, 0) + t.amount

    total_expense = sum(expense_categories.values())
    total_income  = sum(income_sources.values())

    return {
        'total_income':      total_income,
        'total_expense':     total_expense,
        'savings':           total_income - total_expense,
        'expense_categories': expense_categories,
        'income_sources':    income_sources,
        'transaction_count': len(transactions),
        'largest_expense':   largest_expense,
    }


def _pct_change(current, previous):
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def generate_monthly_rag_summary(user_id: int, month_str: str = None):
    """
    Generates a concise monthly financial summary using LLM and stores it
    in FinancialInsight scoped to the given user.
    month_str format: 'YYYY-MM'
    If month_str is not provided, defaults to the previous (completed) month.
    """
    import datetime as dt_mod
    tz_karachi = dt_mod.timezone(dt_mod.timedelta(hours=5))
    now_karachi = dt_mod.datetime.now(tz_karachi)

    if not month_str:
        # Default to the previous completed month
        first_of_this_month = datetime(now_karachi.year, now_karachi.month, 1)
        prev_month_dt = first_of_this_month - relativedelta(months=1)
        month_str = prev_month_dt.strftime('%Y-%m')

    from model import User
    user = User.query.get(user_id)
    if user and user.ai_feed is False:
        print(f"⏩ Skipping monthly summary generation for user={user_id} (ai_feed disabled).")
        return None

    user_name = user.full_name.split()[0] if (user and user.full_name) else "there"

    current = _get_month_totals(user_id, month_str)

    year, month = map(int, month_str.split('-'))
    prev_date = datetime(year, month, 1) - relativedelta(months=1)
    prev_month_str = prev_date.strftime('%Y-%m')
    previous = _get_month_totals(user_id, prev_month_str)

    income_change_pct = _pct_change(current['total_income'], previous['total_income'])
    expense_change_pct = _pct_change(current['total_expense'], previous['total_expense'])

    budget = Budget.query.filter_by(user_id=user_id, month=month_str).first()
    budget_info = ""
    if budget:
        budget_info = (
            f"- Total Budget: PKR {budget.total_budget}\n"
            f"- Total Expenses recorded in budget: PKR {budget.total_expenses}\n"
        )

    prompt = f"""You are a helpful financial AI assistant. Write a short, dynamic, conversational paragraph summarizing the user's financial performance for the completed month {month_str}. Start by warmly greeting the user by their name ("{user_name}").
Analyze ONLY the supplied user data. Do NOT invent transactions, income sources, or unsupported assumptions.
All amounts are in Pakistani Rupees — write "PKR" directly before every number. Never use internal IDs or backend metadata.

Here is {user_name}'s structured data for {month_str}:
- Total Income: PKR {current['total_income']}
- Total Expense: PKR {current['total_expense']}
- Net Savings / Cashflow: PKR {current['savings']}
- Transaction Count: {current['transaction_count']}
{budget_info}\
- Expense Categories: {json.dumps(current['expense_categories'])}
- Income Sources: {json.dumps(current['income_sources'])}
- Largest Single Expense: {json.dumps(current['largest_expense']) if current['largest_expense'] else "None"}

Comparison to previous month ({prev_month_str}):
- Income Change: {f"{income_change_pct}%" if income_change_pct is not None else "N/A"}
- Expense Change: {f"{expense_change_pct}%" if expense_change_pct is not None else "N/A"}

Write 3 to 4 natural, engaging sentences summarizing {user_name}'s performance. Do NOT copy raw JSON. Focus on key spending categories, income highlights, and notable month-over-month changes."""

    summary_text = None
    try:
        from services.llm_client import generate_llm
        res_data = generate_llm(prompt=prompt, model=LLM_MODEL, timeout=10)
        summary_text = res_data.get('response', '').strip()
        if not summary_text:
            print(f"⚠️ [AI_INSIGHT] LLM returned empty response, falling back to rule-based summary.")
    except Exception as e:
        print(f"⚠️ [AI_INSIGHT] LLM request unreachable ({e}), generating structured fallback narrative.")

    if not summary_text:
        # Fallback narrative synthesis if LLM service is offline
        summary_text = (
            f"Hello {user_name}! In {month_str}, you recorded total income of PKR {current['total_income']:,.2f} "
            f"and total expenses of PKR {current['total_expense']:,.2f}, resulting in net cashflow of PKR {current['savings']:,.2f}. "
            f"You logged {current['transaction_count']} transactions across your accounts."
        )

    # Upsert insight for this user+month (Idempotent per user_id + month)
    insight = FinancialInsight.query.filter_by(user_id=user_id, month=month_str).first()
    if not insight:
        insight = FinancialInsight(user_id=user_id, month=month_str)
        db.session.add(insight)

    insight.content = summary_text
    insight.metrics_json = json.dumps({
        "total_income": current['total_income'],
        "total_expense": current['total_expense'],
        "savings": current['savings'],
        "transaction_count": current['transaction_count'],
        "largest_expense": current['largest_expense'],
        "income_change_pct": income_change_pct,
        "expense_change_pct": expense_change_pct,
        "budget": budget.total_budget if budget else 0,
    })
    insight.tags = "monthly_summary"
    db.session.commit()
    print(f"✅ [AI_INSIGHT] Saved monthly summary for user={user_id} {month_str}")
    return summary_text


def get_insights_for_user(user_id: int, limit: int = 6):
    """Return the most recent financial insights for this user."""
    return (
        FinancialInsight.query
        .filter_by(user_id=user_id)
        .order_by(FinancialInsight.created_at.desc())
        .limit(limit)
        .all()
    )


def get_insight_by_month(user_id: int, month: str):
    return (
        FinancialInsight.query
        .filter_by(user_id=user_id, month=month)
        .order_by(FinancialInsight.created_at.desc())
        .first()
    )