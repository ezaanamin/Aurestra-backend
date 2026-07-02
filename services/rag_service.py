import json
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from database import db
from model import Transaction, Budget, FinancialInsight, Category

LLM_URL   = 'https://boring-fell-sentence-clearing.trycloudflare.com/api/generate'
LLM_MODEL = 'qwen2.5:3b'


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
    """
    if not month_str:
        month_str = datetime.utcnow().strftime('%Y-%m')

    from model import User
    user = User.query.get(user_id)
    user_name = user.full_name.split()[0] if user and user.full_name else "there"

    current  = _get_month_totals(user_id, month_str)

    year, month    = map(int, month_str.split('-'))
    prev_date      = datetime(year, month, 1) - relativedelta(months=1)
    prev_month_str = prev_date.strftime('%Y-%m')
    previous       = _get_month_totals(user_id, prev_month_str)

    income_change_pct  = _pct_change(current['total_income'],  previous['total_income'])
    expense_change_pct = _pct_change(current['total_expense'], previous['total_expense'])

    budget = Budget.query.filter_by(user_id=user_id, month=month_str).first()
    budget_info = ""
    if budget:
        budget_info = (
            f"- Total Budget: PKR {budget.total_budget}\n"
            f"- Total Expenses recorded in budget: PKR {budget.total_expenses}\n"
        )

    prompt = f"""You are a helpful financial AI assistant. Write a short, conversational paragraph summarizing the financial performance for {month_str}. Start by warmly greeting the user by their name ("{user_name}").
Use short, natural sentences. Mention the total income, total expense, savings, and biggest expense category.
All amounts are in Pakistani Rupees — write "PKR" directly before every number, every time. Never use the dollar sign.

Here is {user_name}'s data you must summarize:
- Total Income: PKR {current['total_income']}
- Total Expense: PKR {current['total_expense']}
- Net Savings: PKR {current['savings']}
- Transaction Count: {current['transaction_count']}
{budget_info}\
- Expense Categories: {json.dumps(current['expense_categories'])}
- Income Sources: {json.dumps(current['income_sources'])}
- Largest Single Expense: {json.dumps(current['largest_expense']) if current['largest_expense'] else "None"}

Comparison to last month ({prev_month_str}):
- Income Change: {f"{income_change_pct}%" if income_change_pct is not None else "N/A"}
- Expense Change: {f"{expense_change_pct}%" if expense_change_pct is not None else "N/A"}

Now, write exactly 3 to 4 natural sentences summarizing {user_name}'s performance. Do NOT just copy the list. Write it like a story:
"""

    try:
        response = requests.post(LLM_URL, json={
            "model": LLM_MODEL, "prompt": prompt, "stream": False
        }, timeout=30)

        if response.status_code == 200:
            data         = response.json()
            summary_text = data.get('response', '').strip()

            # Upsert insight for this user+month
            insight = FinancialInsight.query.filter_by(user_id=user_id, month=month_str).first()
            if not insight:
                insight = FinancialInsight(user_id=user_id, month=month_str)
                db.session.add(insight)

            insight.content     = summary_text
            insight.metrics_json = json.dumps({
                "total_income":       current['total_income'],
                "total_expense":      current['total_expense'],
                "savings":            current['savings'],
                "transaction_count":  current['transaction_count'],
                "largest_expense":    current['largest_expense'],
                "income_change_pct":  income_change_pct,
                "expense_change_pct": expense_change_pct,
                "budget":             budget.total_budget if budget else 0,
            })
            insight.tags = "monthly_summary"
            db.session.commit()
            print(f"✅ Generated and saved monthly summary for user={user_id} {month_str}")
            print("\n====== GENERATED RAG SUMMARY ======")
            print(summary_text)
            print("===================================\n")
            return summary_text

        else:
            print(f"❌ Failed to generate summary. Status: {response.status_code}, {response.text}")

    except Exception as e:
        import traceback
        print(f"❌ Error generating RAG summary: {e}")
        print(traceback.format_exc())

    return None


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