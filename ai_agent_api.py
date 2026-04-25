"""
Aurestra AI Agent API — Analytics-Focused Endpoints
=====================================================
Blueprint providing high-utility, analytics-focused API endpoints
designed for consumption by a personalized LLM agent.

Auth: Fixed JWT token in Authorization header (Bearer <token>).
"""

import os
import jwt
import json
import hashlib
from datetime import datetime, timedelta, date
from functools import wraps
from collections import defaultdict, Counter

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import extract, func, and_, or_, case, text

from database import db
from model import (
    Transaction, MonthlyBalance, Budget, AccountBalance,
    SavingsGoal, Category, CategorizationRule, User,
    FinancialInsight, StatementAnalysis, SMSHistory, DeviceToken
)

# ---------------------------------------------------------------------------
# Blueprint & Auth
# ---------------------------------------------------------------------------

ai_agent_bp = Blueprint("ai_agent", __name__, url_prefix="/api/agent")

# Fixed JWT secret — read from env, fall back to app SECRET_KEY
AGENT_JWT_SECRET = os.getenv("AGENT_JWT_SECRET", os.getenv("SECRET_KEY", "0aefb44af279f5bb0ad9ecce393be138"))
AGENT_JWT_ALGORITHM = "HS256"

# Pre-generate a fixed token for the agent (never refreshes)
# The agent stores this token permanently.
FIXED_AGENT_TOKEN = os.getenv("AGENT_FIXED_TOKEN", None)


def _generate_agent_token():
    """
    One-time helper to generate a fixed agent JWT.
    Run:  python -c "from ai_agent_api import _generate_agent_token; print(_generate_agent_token())"
    """
    payload = {
        "sub": "aurestra_agent",
        "role": "ai_agent",
        "iat": datetime(2026, 1, 1),
    }
    return jwt.encode(payload, AGENT_JWT_SECRET, algorithm=AGENT_JWT_ALGORITHM)


def agent_auth_required(f):
    """
    Decorator: validates a fixed JWT token on every agent request.
    Accepts:   Authorization: Bearer <token>
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or malformed Authorization header. Expected: Bearer <token>"}), 401

        token = auth_header.split(" ", 1)[1]

        # Fast-path: if a fixed token string is configured, compare directly
        if FIXED_AGENT_TOKEN and token == FIXED_AGENT_TOKEN:
            return f(*args, **kwargs)

        # Otherwise verify JWT signature
        try:
            payload = jwt.decode(token, AGENT_JWT_SECRET, algorithms=[AGENT_JWT_ALGORITHM])
            if payload.get("role") != "ai_agent":
                return jsonify({"error": "Token role is not 'ai_agent'."}), 403
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired."}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({"error": f"Invalid token: {str(e)}"}), 401

        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(s):
    """Attempt to parse a date string in multiple formats."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _active_transactions(query=None):
    """Base query filtering out deleted & spam transactions."""
    q = query if query is not None else Transaction.query
    return q.filter(
        Transaction.is_deleted != True,
        Transaction.is_spam != True,
    )


def _month_str(dt):
    """Return 'YYYY-MM' from a datetime."""
    return dt.strftime("%Y-%m")


def _safe_div(a, b):
    return (a / b) if b else 0.0


def _category_name(cat_id):
    """Return category name by id, or 'Uncategorized'."""
    if cat_id is None:
        return "Uncategorized"
    cat = Category.query.get(cat_id)
    return cat.name if cat else "Uncategorized"


def _build_category_map():
    """Return {id: {name, icon, color, cat_type}} dict."""
    cats = Category.query.all()
    return {c.id: c.to_dict() for c in cats}


# ---------------------------------------------------------------------------
# 0.  TOKEN GENERATION (utility — not client-facing in production)
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/generate-token", methods=["GET"])
def generate_token():
    """
    DEV-ONLY endpoint to generate the fixed agent JWT.
    Should be disabled / removed in production.
    """
    token = _generate_agent_token()
    return jsonify({"token": token, "note": "Store this permanently. It does not expire."})


# ---------------------------------------------------------------------------
# 1.  FINANCIAL OVERVIEW  /api/agent/overview
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/overview", methods=["GET"])
@agent_auth_required
def financial_overview():
    """
    High-level financial snapshot: current balances, this month's income/expenses,
    budget status, active savings goals count.

    Query params:
      - month  (optional, YYYY-MM, default = current month)

    Example insight:
      "Monthly net balance increased by 12.5% compared to previous month"
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month format. Use YYYY-MM."}), 400

    # Current account balances
    balances = AccountBalance.query.all()
    balance_list = [b.to_dict() for b in balances]
    total_balance = sum(b.current_balance for b in balances)

    # Monthly balance record
    mb = MonthlyBalance.query.filter_by(month=month_param).first()

    # Transactions this month (active only)
    txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
    ).all()

    total_income = sum(t.amount for t in txns if t.type == "credit")
    total_expense = sum(t.amount for t in txns if t.type == "debit")
    net = total_income - total_expense
    tx_count = len(txns)

    # Budget
    budget = Budget.query.filter_by(month=month_param).first()

    # Savings goals
    goals = SavingsGoal.query.all()

    # Previous month comparison
    prev_dt = date(year, mon, 1) - timedelta(days=1)
    prev_month_str = _month_str(prev_dt)
    prev_mb = MonthlyBalance.query.filter_by(month=prev_month_str).first()

    mom_change_pct = None
    insight_text = None
    if mb and prev_mb and prev_mb.closing_balance:
        mom_change_pct = round(
            ((mb.closing_balance - prev_mb.closing_balance) / abs(prev_mb.closing_balance)) * 100, 2
        )
        direction = "increased" if mom_change_pct >= 0 else "decreased"
        insight_text = f"Monthly net balance {direction} by {abs(mom_change_pct)}% compared to previous month."

    return jsonify({
        "month": month_param,
        "accounts": balance_list,
        "total_balance": total_balance,
        "monthly_summary": {
            "opening_balance": mb.opening_balance if mb else None,
            "closing_balance": mb.closing_balance if mb else None,
            "expense": mb.expense if mb else None,
            "savings": mb.savings if mb else None,
        },
        "transactions": {
            "count": tx_count,
            "total_income": total_income,
            "total_expense": total_expense,
            "net": net,
        },
        "budget": {
            "total_budget": budget.total_budget if budget else None,
            "needs": budget.needs if budget else None,
            "wants": budget.wants if budget else None,
            "saving": budget.saving if budget else None,
        } if budget else None,
        "savings_goals": {
            "active_count": len(goals),
            "total_target": sum(g.target_amount for g in goals),
            "total_saved": sum(g.current_amount for g in goals),
        },
        "mom_change_pct": mom_change_pct,
        "insight": insight_text,
    })


# ---------------------------------------------------------------------------
# 2.  EXPENSE & INCOME SUMMARY  /api/agent/summary/expense-income
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/summary/expense-income", methods=["GET"])
@agent_auth_required
def expense_income_summary():
    """
    Breakdown of expenses and income by category and account, for a given period.

    Query params:
      - start_date  (YYYY-MM-DD, optional)
      - end_date    (YYYY-MM-DD, optional)
      - month       (YYYY-MM, shortcut — overrides start/end)
      - account     (e.g. 'bank', 'bank_sms', 'manual')
    """
    month_param = request.args.get("month")
    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")
    account = request.args.get("account")

    q = _active_transactions()

    if month_param:
        try:
            y, m = int(month_param[:4]), int(month_param[5:7])
            q = q.filter(extract("year", Transaction.date) == y, extract("month", Transaction.date) == m)
        except Exception:
            return jsonify({"error": "Invalid month format."}), 400
    else:
        if start_str:
            sd = _parse_date(start_str)
            if sd:
                q = q.filter(Transaction.date >= sd)
        if end_str:
            ed = _parse_date(end_str)
            if ed:
                q = q.filter(Transaction.date <= ed + timedelta(days=1))

    if account:
        q = q.filter(Transaction.source == account)

    txns = q.order_by(Transaction.date).all()
    cat_map = _build_category_map()

    expense_by_cat = defaultdict(float)
    income_by_cat = defaultdict(float)
    expense_by_account = defaultdict(float)
    income_by_account = defaultdict(float)

    for t in txns:
        cat_name = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
        if t.type == "debit":
            expense_by_cat[cat_name] += t.amount
            expense_by_account[t.source] += t.amount
        elif t.type == "credit":
            income_by_cat[cat_name] += t.amount
            income_by_account[t.source] += t.amount

    total_expense = sum(expense_by_cat.values())
    total_income = sum(income_by_cat.values())

    return jsonify({
        "period": {
            "month": month_param,
            "start_date": start_str,
            "end_date": end_str,
            "account_filter": account,
        },
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "net": round(total_income - total_expense, 2),
        "expense_by_category": dict(sorted(expense_by_cat.items(), key=lambda x: -x[1])),
        "income_by_category": dict(sorted(income_by_cat.items(), key=lambda x: -x[1])),
        "expense_by_account": dict(expense_by_account),
        "income_by_account": dict(income_by_account),
        "transaction_count": len(txns),
    })


# ---------------------------------------------------------------------------
# 3.  MONTHLY BALANCE TRENDS  /api/agent/trends/monthly-balance
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/trends/monthly-balance", methods=["GET"])
@agent_auth_required
def monthly_balance_trends():
    """
    Returns monthly balance evolution with per-account breakdown,
    transaction-level income/expense per month, cumulative liquidity,
    and MoM deltas for every recorded month.

    Query params:
      - months  (int, how many recent months to return, default=12)
      - account (str, filter by account source e.g. 'bank')
    """
    limit = int(request.args.get("months", 12))
    account_filter = request.args.get("account")

    records = (
        MonthlyBalance.query
        .order_by(MonthlyBalance.month.desc())
        .limit(limit)
        .all()
    )
    records.reverse()  # chronological order

    # Collect all distinct months from records
    month_strings = [r.month for r in records]

    # Per-account balances for each month
    all_balances = AccountBalance.query.all()
    account_sources = [b.source for b in all_balances]

    # Transaction-level income/expense per month
    cat_map = _build_category_map()
    trend_data = []
    cumulative_net = 0.0

    for i, r in enumerate(records):
        try:
            y, m = int(r.month[:4]), int(r.month[5:7])
        except (ValueError, IndexError):
            continue

        # Fetch active transactions for this month
        q = _active_transactions().filter(
            extract("year", Transaction.date) == y,
            extract("month", Transaction.date) == m,
        )
        if account_filter:
            q = q.filter(Transaction.source == account_filter)
        txns = q.all()

        tx_income = sum(t.amount for t in txns if t.type == "credit")
        tx_expense = sum(t.amount for t in txns if t.type == "debit")
        tx_net = tx_income - tx_expense
        cumulative_net += tx_net

        # Expense breakdown by category (top 5)
        cat_totals = defaultdict(float)
        for t in txns:
            if t.type == "debit":
                cname = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
                cat_totals[cname] += t.amount
        top_cats = sorted(cat_totals.items(), key=lambda x: -x[1])[:5]

        # Income breakdown by category
        inc_cats = defaultdict(float)
        for t in txns:
            if t.type == "credit":
                cname = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
                inc_cats[cname] += t.amount

        # Per-account breakdown for this month
        per_account = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
        for t in txns:
            if t.type == "credit":
                per_account[t.source]["income"] += t.amount
            elif t.type == "debit":
                per_account[t.source]["expense"] += t.amount

        entry = {
            "month": r.month,
            "source": r.source,
            "opening_balance": r.opening_balance,
            "closing_balance": r.closing_balance,
            "balance_change": round(r.closing_balance - r.opening_balance, 2),
            "recorded_expense": r.expense,
            "recorded_savings": r.savings,
            "transaction_income": round(tx_income, 2),
            "transaction_expense": round(tx_expense, 2),
            "transaction_net": round(tx_net, 2),
            "transaction_count": len(txns),
            "savings_rate": round(_safe_div(tx_net, tx_income) * 100, 1) if tx_income > 0 else 0.0,
            "cumulative_net": round(cumulative_net, 2),
            "top_expense_categories": [
                {"category": name, "amount": round(amt, 2)} for name, amt in top_cats
            ],
            "income_sources": [
                {"category": name, "amount": round(amt, 2)} for name, amt in sorted(inc_cats.items(), key=lambda x: -x[1])
            ],
            "per_account": {
                src: {"income": round(d["income"], 2), "expense": round(d["expense"], 2), "net": round(d["income"] - d["expense"], 2)}
                for src, d in per_account.items()
            },
        }

        # MoM changes
        if i > 0:
            prev = trend_data[i - 1]
            prev_close = prev.get("closing_balance", 0)
            if prev_close:
                entry["mom_balance_change"] = round(r.closing_balance - prev_close, 2)
                entry["mom_balance_change_pct"] = round(((r.closing_balance - prev_close) / abs(prev_close)) * 100, 2)
            else:
                entry["mom_balance_change"] = round(r.closing_balance - prev_close, 2)
                entry["mom_balance_change_pct"] = None

            prev_exp = prev.get("transaction_expense", 0)
            prev_inc = prev.get("transaction_income", 0)
            entry["mom_expense_change"] = round(tx_expense - prev_exp, 2)
            entry["mom_expense_change_pct"] = round(_safe_div(tx_expense - prev_exp, prev_exp) * 100, 2) if prev_exp else None
            entry["mom_income_change"] = round(tx_income - prev_inc, 2)
            entry["mom_income_change_pct"] = round(_safe_div(tx_income - prev_inc, prev_inc) * 100, 2) if prev_inc else None
        else:
            entry["mom_balance_change"] = None
            entry["mom_balance_change_pct"] = None
            entry["mom_expense_change"] = None
            entry["mom_expense_change_pct"] = None
            entry["mom_income_change"] = None
            entry["mom_income_change_pct"] = None

        trend_data.append(entry)

    # Summary insights
    insights = []
    if len(trend_data) >= 2:
        first = trend_data[0]
        last = trend_data[-1]
        if first["closing_balance"] and last["closing_balance"]:
            total_change = round(last["closing_balance"] - first["closing_balance"], 2)
            total_change_pct = round(_safe_div(total_change, abs(first["closing_balance"])) * 100, 1) if first["closing_balance"] else None
            direction = "grown" if total_change >= 0 else "declined"
            insights.append(f"Balance has {direction} by {abs(total_change):,.0f} ({'+' if total_change >= 0 else ''}{total_change_pct}%) from {first['month']} to {last['month']}.")

        avg_savings_rate = sum(e.get("savings_rate", 0) for e in trend_data) / len(trend_data)
        insights.append(f"Average savings rate over {len(trend_data)} months: {avg_savings_rate:.1f}%.")

        # Cumulative liquidity trend description
        insights.append(f"Cumulative net cash flow across all tracked months: {cumulative_net:+,.0f}.")

    return jsonify({
        "months_returned": len(trend_data),
        "account_filter": account_filter,
        "trend": trend_data,
        "summary": {
            "first_month": trend_data[0]["month"] if trend_data else None,
            "last_month": trend_data[-1]["month"] if trend_data else None,
            "total_months": len(trend_data),
            "cumulative_net": round(cumulative_net, 2),
        },
        "insights": insights,
    })


# ---------------------------------------------------------------------------
# 4.  CASH FLOW TRENDS & ROLLING AVERAGES  /api/agent/trends/cashflow
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/trends/cashflow", methods=["GET"])
@agent_auth_required
def cashflow_trends():
    """
    Daily cash-flow with weekly and monthly rolling averages.

    Query params:
      - start_date  (YYYY-MM-DD, default = 30 days ago)
      - end_date    (YYYY-MM-DD, default = today)
    """
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=30)

    if request.args.get("start_date"):
        start_dt = _parse_date(request.args["start_date"]) or start_dt
    if request.args.get("end_date"):
        end_dt = _parse_date(request.args["end_date"]) or end_dt

    txns = (
        _active_transactions()
        .filter(Transaction.date >= start_dt, Transaction.date <= end_dt + timedelta(days=1))
        .order_by(Transaction.date)
        .all()
    )

    # Aggregate by day
    daily = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for t in txns:
        day_key = t.date.strftime("%Y-%m-%d")
        if t.type == "credit":
            daily[day_key]["income"] += t.amount
        elif t.type == "debit":
            daily[day_key]["expense"] += t.amount

    sorted_days = sorted(daily.keys())
    series = []
    for d in sorted_days:
        entry = daily[d]
        series.append({
            "date": d,
            "income": round(entry["income"], 2),
            "expense": round(entry["expense"], 2),
            "net": round(entry["income"] - entry["expense"], 2),
        })

    # Rolling averages
    def rolling_avg(data, key, window):
        result = []
        for i, item in enumerate(data):
            start_i = max(0, i - window + 1)
            window_vals = [data[j][key] for j in range(start_i, i + 1)]
            result.append(round(sum(window_vals) / len(window_vals), 2))
        return result

    weekly_avg_expense = rolling_avg(series, "expense", 7) if series else []
    monthly_avg_expense = rolling_avg(series, "expense", 30) if series else []

    for i, s in enumerate(series):
        s["rolling_7d_expense"] = weekly_avg_expense[i] if i < len(weekly_avg_expense) else None
        s["rolling_30d_expense"] = monthly_avg_expense[i] if i < len(monthly_avg_expense) else None

    total_income = sum(s["income"] for s in series)
    total_expense = sum(s["expense"] for s in series)

    return jsonify({
        "period": {"start": start_dt.strftime("%Y-%m-%d"), "end": end_dt.strftime("%Y-%m-%d")},
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "net_cashflow": round(total_income - total_expense, 2),
        "daily_series": series,
        "avg_daily_expense": round(_safe_div(total_expense, len(series)), 2) if series else 0,
        "avg_daily_income": round(_safe_div(total_income, len(series)), 2) if series else 0,
    })


# ---------------------------------------------------------------------------
# 5.  MoM INCOME & EXPENSE CHANGES  /api/agent/trends/mom
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/trends/mom", methods=["GET"])
@agent_auth_required
def month_over_month():
    """
    Comprehensive month-over-month comparison with income, expenses,
    net balance changes, savings rate, cumulative totals, and trend insights.

    Query params:
      - months  (int, default=6)
      - account (str, filter by account source)
    """
    limit = int(request.args.get("months", 6))
    account_filter = request.args.get("account")
    now = datetime.utcnow()

    results = []
    cumulative_income = 0.0
    cumulative_expense = 0.0

    for offset in range(limit - 1, -1, -1):
        dt = (now.replace(day=1) - timedelta(days=1) * 30 * offset)
        y, m = dt.year, dt.month
        m_str = f"{y}-{m:02d}"

        q = _active_transactions().filter(
            extract("year", Transaction.date) == y,
            extract("month", Transaction.date) == m,
        )
        if account_filter:
            q = q.filter(Transaction.source == account_filter)
        txns = q.all()

        inc = sum(t.amount for t in txns if t.type == "credit")
        exp = sum(t.amount for t in txns if t.type == "debit")
        net_val = inc - exp
        savings_rate = round(_safe_div(net_val, inc) * 100, 1) if inc > 0 else 0.0

        cumulative_income += inc
        cumulative_expense += exp

        # Look up MonthlyBalance for this month
        mb = MonthlyBalance.query.filter_by(month=m_str).first()

        # Per-account breakdown
        acct_breakdown = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
        for t in txns:
            if t.type == "credit":
                acct_breakdown[t.source]["income"] += t.amount
            elif t.type == "debit":
                acct_breakdown[t.source]["expense"] += t.amount

        results.append({
            "month": m_str,
            "income": round(inc, 2),
            "expense": round(exp, 2),
            "net": round(net_val, 2),
            "savings_rate": savings_rate,
            "transaction_count": len(txns),
            "balance": {
                "opening": mb.opening_balance if mb else None,
                "closing": mb.closing_balance if mb else None,
                "change": round(mb.closing_balance - mb.opening_balance, 2) if mb else None,
            },
            "cumulative_income": round(cumulative_income, 2),
            "cumulative_expense": round(cumulative_expense, 2),
            "cumulative_net": round(cumulative_income - cumulative_expense, 2),
            "per_account": {
                src: {"income": round(d["income"], 2), "expense": round(d["expense"], 2), "net": round(d["income"] - d["expense"], 2)}
                for src, d in acct_breakdown.items()
            },
        })

    # Calculate MoM changes
    for i in range(1, len(results)):
        prev = results[i - 1]
        curr = results[i]
        curr["income_change"] = round(curr["income"] - prev["income"], 2)
        curr["expense_change"] = round(curr["expense"] - prev["expense"], 2)
        curr["net_change"] = round(curr["net"] - prev["net"], 2)
        curr["income_change_pct"] = round(_safe_div(curr["income_change"], prev["income"]) * 100, 2) if prev["income"] else None
        curr["expense_change_pct"] = round(_safe_div(curr["expense_change"], prev["expense"]) * 100, 2) if prev["expense"] else None
        curr["net_change_pct"] = round(_safe_div(curr["net_change"], abs(prev["net"])) * 100, 2) if prev["net"] else None
        curr["savings_rate_change"] = round(curr["savings_rate"] - prev["savings_rate"], 1)

    # Generate trend insights
    insights = []
    if len(results) >= 2:
        latest = results[-1]
        previous = results[-2]

        # Income trend
        if latest.get("income_change_pct") is not None:
            direction = "increased" if latest["income_change_pct"] >= 0 else "decreased"
            insights.append(f"Income {direction} by {abs(latest['income_change_pct'])}% month-over-month ({previous['month']} → {latest['month']}).")

        # Expense trend
        if latest.get("expense_change_pct") is not None:
            direction = "increased" if latest["expense_change_pct"] >= 0 else "decreased"
            insights.append(f"Expenses {direction} by {abs(latest['expense_change_pct'])}% month-over-month.")

        # Net savings trend
        insights.append(f"Month-over-month net savings: {latest['net']:+,.0f} (savings rate: {latest['savings_rate']}%).")

        # Cumulative summary
        insights.append(f"Cumulative over {len(results)} months — income: {cumulative_income:,.0f}, expense: {cumulative_expense:,.0f}, net: {cumulative_income - cumulative_expense:+,.0f}.")

        # Best/worst months
        best_month = max(results, key=lambda x: x["net"])
        worst_month = min(results, key=lambda x: x["net"])
        insights.append(f"Best month: {best_month['month']} (net: {best_month['net']:+,.0f}). Worst month: {worst_month['month']} (net: {worst_month['net']:+,.0f}).")

    return jsonify({
        "months_count": len(results),
        "account_filter": account_filter,
        "months": results,
        "aggregate": {
            "total_income": round(cumulative_income, 2),
            "total_expense": round(cumulative_expense, 2),
            "total_net": round(cumulative_income - cumulative_expense, 2),
            "avg_monthly_income": round(_safe_div(cumulative_income, len(results)), 2) if results else 0,
            "avg_monthly_expense": round(_safe_div(cumulative_expense, len(results)), 2) if results else 0,
            "avg_savings_rate": round(sum(r["savings_rate"] for r in results) / len(results), 1) if results else 0,
        },
        "insights": insights,
    })


# ---------------------------------------------------------------------------
# 6.  RECURRING EXPENSE DETECTION  /api/agent/analytics/recurring
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/recurring", methods=["GET"])
@agent_auth_required
def recurring_expenses():
    """
    Detects recurring/subscription-like transactions based on purpose and amount patterns.
    Looks at the last N months of data.

    Query params:
      - months  (int, default=3)
      - min_confidence  (float 0-1, default=0.6)
    """
    months_back = int(request.args.get("months", 3))
    min_confidence = float(request.args.get("min_confidence", 0.6))

    cutoff = datetime.utcnow() - timedelta(days=months_back * 31)
    txns = (
        _active_transactions()
        .filter(Transaction.date >= cutoff, Transaction.type == "debit")
        .order_by(Transaction.date)
        .all()
    )

    # Group by purpose + similar amount
    purpose_groups = defaultdict(list)
    for t in txns:
        key = (t.purpose or "Unknown").strip().lower()
        purpose_groups[key].append(t)

    recurring = []
    for purpose, tx_list in purpose_groups.items():
        if len(tx_list) < 2:
            continue

        amounts = [t.amount for t in tx_list]
        dates = sorted([t.date for t in tx_list])

        # Check amount consistency (low variance = subscription)
        avg_amount = sum(amounts) / len(amounts)
        max_dev = max(abs(a - avg_amount) for a in amounts)
        amount_consistency = 1.0 - min(max_dev / avg_amount, 1.0) if avg_amount else 0

        # Check date regularity (roughly monthly intervals)
        if len(dates) >= 2:
            intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval > 0:
                interval_variance = sum(abs(iv - avg_interval) for iv in intervals) / len(intervals)
                regularity = max(0, 1.0 - interval_variance / avg_interval)
            else:
                regularity = 0
        else:
            regularity = 0
            avg_interval = 0

        # Confidence = weighted average of consistency and regularity
        confidence = round(amount_consistency * 0.4 + regularity * 0.4 + min(len(tx_list) / 6, 1.0) * 0.2, 2)

        if confidence >= min_confidence:
            recurring.append({
                "purpose": purpose.title(),
                "avg_amount": round(avg_amount, 2),
                "occurrences": len(tx_list),
                "avg_interval_days": round(avg_interval, 1),
                "confidence": confidence,
                "last_date": dates[-1].strftime("%Y-%m-%d"),
                "next_expected": (dates[-1] + timedelta(days=round(avg_interval))).strftime("%Y-%m-%d") if avg_interval > 0 else None,
                "category": _category_name(tx_list[-1].category_id),
                "insight": f"Recurring expense to '{purpose.title()}' detected with confidence {confidence}",
            })

    recurring.sort(key=lambda x: -x["confidence"])

    return jsonify({
        "months_analyzed": months_back,
        "min_confidence": min_confidence,
        "recurring_expenses": recurring,
        "total_monthly_recurring": round(
            sum(r["avg_amount"] * (30 / max(r["avg_interval_days"], 1)) for r in recurring), 2
        ),
    })


# ---------------------------------------------------------------------------
# 7.  NET WORTH / LIQUIDITY / LOCKED ASSETS  /api/agent/analytics/net-worth
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/net-worth", methods=["GET"])
@agent_auth_required
def net_worth():
    """
    Calculates net worth, liquidity, and locked assets.
    Locked assets = savings goals' current saved amounts.
    Liquid = total balances minus locked.
    """
    balances = AccountBalance.query.all()
    total_balance = sum(b.current_balance for b in balances)

    goals = SavingsGoal.query.all()
    locked_in_goals = sum(g.current_amount for g in goals)

    liquid = total_balance - locked_in_goals

    return jsonify({
        "net_worth": round(total_balance, 2),
        "liquid_assets": round(max(liquid, 0), 2),
        "locked_assets": round(locked_in_goals, 2),
        "accounts": [b.to_dict() for b in balances],
        "savings_goals_summary": [
            {
                "name": g.name,
                "target": g.target_amount,
                "saved": g.current_amount,
                "remaining": g.remaining,
                "pct_complete": round(_safe_div(g.current_amount, g.target_amount) * 100, 1),
            }
            for g in goals
        ],
        "insight": f"Total net worth is {total_balance:,.0f}. Liquid assets: {max(liquid, 0):,.0f}."
                   + (f" {locked_in_goals:,.0f} is locked in savings goals." if locked_in_goals > 0 else " No assets locked in savings goals."),
    })


# ---------------------------------------------------------------------------
# 8.  BUDGET ADHERENCE & DAILY SAFE SPEND  /api/agent/analytics/budget
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/budget", methods=["GET"])
@agent_auth_required
def budget_adherence():
    """
    Budget adherence for a given month.
    Calculates: remaining budget, daily safe spending allowance,
    per-category usage if applicable.

    Query params:
      - month  (YYYY-MM, default = current)
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month."}), 400

    budget = Budget.query.filter_by(month=month_param).first()
    if not budget:
        return jsonify({
            "month": month_param,
            "budget": None,
            "message": "No budget found for this month.",
        })

    # Actual spending this month
    txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
        Transaction.type == "debit",
    ).all()

    total_spent = sum(t.amount for t in txns)

    # Days remaining in month
    today = datetime.utcnow()
    if year == today.year and mon == today.month:
        from calendar import monthrange
        _, days_in_month = monthrange(year, mon)
        days_remaining = max(days_in_month - today.day, 1)
        days_elapsed = today.day
    else:
        from calendar import monthrange
        _, days_in_month = monthrange(year, mon)
        days_remaining = 0
        days_elapsed = days_in_month

    remaining = budget.total_budget - total_spent
    daily_safe_spend = max(remaining / days_remaining, 0) if days_remaining > 0 else 0
    usage_pct = round(_safe_div(total_spent, budget.total_budget) * 100, 1)

    # Status
    if usage_pct > 100:
        status = "over_budget"
    elif usage_pct > 80:
        status = "warning"
    else:
        status = "on_track"

    insight_parts = []
    if status == "over_budget":
        insight_parts.append(f"⚠️ Budget exceeded by {abs(remaining):,.0f}. Total spent: {total_spent:,.0f} vs budget: {budget.total_budget:,.0f}.")
    elif status == "warning":
        insight_parts.append(f"⚡ {usage_pct}% of budget used with {days_remaining} days remaining.")
    else:
        insight_parts.append(f"✅ Budget on track. {remaining:,.0f} remaining, safe to spend {daily_safe_spend:,.0f}/day.")

    return jsonify({
        "month": month_param,
        "budget": {
            "total": budget.total_budget,
            "needs": budget.needs,
            "wants": budget.wants,
            "saving": budget.saving,
        },
        "actual": {
            "total_spent": round(total_spent, 2),
            "remaining": round(remaining, 2),
            "usage_pct": usage_pct,
        },
        "daily_safe_spend": round(daily_safe_spend, 2),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "status": status,
        "insight": " ".join(insight_parts),
    })


# ---------------------------------------------------------------------------
# 9.  TOP SPENDING CATEGORIES  /api/agent/analytics/top-categories
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/top-categories", methods=["GET"])
@agent_auth_required
def top_spending_categories():
    """
    Top N spending categories for a period.

    Query params:
      - month       (YYYY-MM)
      - start_date  (YYYY-MM-DD)
      - end_date    (YYYY-MM-DD)
      - top_n       (int, default=10)
    """
    top_n = int(request.args.get("top_n", 10))
    month_param = request.args.get("month")
    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")

    q = _active_transactions().filter(Transaction.type == "debit")

    if month_param:
        try:
            y, m = int(month_param[:4]), int(month_param[5:7])
            q = q.filter(extract("year", Transaction.date) == y, extract("month", Transaction.date) == m)
        except Exception:
            return jsonify({"error": "Invalid month."}), 400
    elif start_str or end_str:
        if start_str:
            sd = _parse_date(start_str)
            if sd:
                q = q.filter(Transaction.date >= sd)
        if end_str:
            ed = _parse_date(end_str)
            if ed:
                q = q.filter(Transaction.date <= ed + timedelta(days=1))

    txns = q.all()
    cat_map = _build_category_map()

    cat_totals = defaultdict(lambda: {"amount": 0.0, "count": 0, "color": "#64748B", "icon": "cash"})
    for t in txns:
        cat_info = cat_map.get(t.category_id, {"name": "Uncategorized", "color": "#64748B", "icon": "cash"})
        cname = cat_info.get("name", "Uncategorized")
        cat_totals[cname]["amount"] += t.amount
        cat_totals[cname]["count"] += 1
        cat_totals[cname]["color"] = cat_info.get("color", "#64748B")
        cat_totals[cname]["icon"] = cat_info.get("icon", "cash")

    total_expense = sum(v["amount"] for v in cat_totals.values())

    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1]["amount"])[:top_n]
    categories = []
    for name, data in sorted_cats:
        pct = round(_safe_div(data["amount"], total_expense) * 100, 1)
        categories.append({
            "category": name,
            "amount": round(data["amount"], 2),
            "count": data["count"],
            "percentage": pct,
            "color": data["color"],
            "icon": data["icon"],
        })

    # Generate insight for top category
    insights = []
    if categories:
        top = categories[0]
        insights.append(f"Top spending category: '{top['category']}' at {top['amount']:,.0f} ({top['percentage']}% of total).")

    return jsonify({
        "top_n": top_n,
        "total_expense": round(total_expense, 2),
        "categories": categories,
        "insights": insights,
    })


# ---------------------------------------------------------------------------
# 10.  ANOMALY / UNUSUAL TRANSACTION DETECTION  /api/agent/analytics/anomalies
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/anomalies", methods=["GET"])
@agent_auth_required
def anomaly_detection():
    """
    Detects unusual transactions by comparing each category's monthly spend
    against historical averages. Flags transactions that deviate significantly.

    Query params:
      - month       (YYYY-MM, default = current)
      - threshold   (float, multiplier above avg, default=1.25 → 25% above)
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    threshold = float(request.args.get("threshold", 1.25))

    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month."}), 400

    # Historical data: last 6 months excluding current
    history_months = []
    for offset in range(1, 7):
        dt = date(year, mon, 1) - timedelta(days=offset * 30)
        history_months.append((dt.year, dt.month))

    # Historical averages by category
    cat_history = defaultdict(list)
    for hy, hm in history_months:
        txns = _active_transactions().filter(
            extract("year", Transaction.date) == hy,
            extract("month", Transaction.date) == hm,
            Transaction.type == "debit",
        ).all()

        month_cats = defaultdict(float)
        for t in txns:
            cat_name = _category_name(t.category_id)
            month_cats[cat_name] += t.amount

        for cat, total in month_cats.items():
            cat_history[cat].append(total)

    # Current month totals
    current_txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
        Transaction.type == "debit",
    ).all()

    current_cats = defaultdict(float)
    for t in current_txns:
        cat_name = _category_name(t.category_id)
        current_cats[cat_name] += t.amount

    anomalies = []
    for cat, current_total in current_cats.items():
        hist = cat_history.get(cat, [])
        if not hist:
            continue
        avg = sum(hist) / len(hist)
        if avg > 0 and current_total > avg * threshold:
            deviation_pct = round(((current_total - avg) / avg) * 100, 1)
            anomalies.append({
                "category": cat,
                "current_month_total": round(current_total, 2),
                "historical_avg": round(avg, 2),
                "deviation_pct": deviation_pct,
                "monthly_history": [round(h, 2) for h in hist],
                "insight": f"Expenses in '{cat}' exceeded typical monthly range by {deviation_pct}%",
            })

    anomalies.sort(key=lambda x: -x["deviation_pct"])

    # Also flag individual large transactions (> 3x avg daily spend)
    total_hist_expense = sum(sum(v) for v in cat_history.values())
    total_hist_months = max(len(history_months), 1)
    avg_daily = total_hist_expense / (total_hist_months * 30) if total_hist_expense > 0 else 0

    large_txns = []
    if avg_daily > 0:
        for t in current_txns:
            if t.amount > avg_daily * 3:
                large_txns.append({
                    "id": t.id,
                    "date": t.date.strftime("%Y-%m-%d"),
                    "amount": t.amount,
                    "purpose": t.purpose,
                    "category": _category_name(t.category_id),
                    "multiple_of_daily_avg": round(t.amount / avg_daily, 1),
                })

    return jsonify({
        "month": month_param,
        "threshold": threshold,
        "category_anomalies": anomalies,
        "large_individual_transactions": large_txns,
        "avg_daily_spend_historical": round(avg_daily, 2),
    })


# ---------------------------------------------------------------------------
# 11.  SAVINGS VELOCITY & GOAL TRACKING  /api/agent/analytics/savings
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/savings", methods=["GET"])
@agent_auth_required
def savings_analytics():
    """
    Savings velocity, goal progress, and projected goal completion dates.
    """
    goals = SavingsGoal.query.all()
    if not goals:
        return jsonify({
            "goals": [],
            "message": "No savings goals found. Create a goal to start tracking.",
        })

    today = datetime.utcnow().date()

    goal_analytics = []
    for g in goals:
        pct = round(_safe_div(g.current_amount, g.target_amount) * 100, 1)
        remaining = g.remaining

        # Velocity: current_amount / days since creation
        days_active = max((today - g.created_at.date()).days, 1) if g.created_at else 1
        daily_velocity = g.current_amount / days_active if days_active > 0 else 0

        # Projected completion
        projected_days = int(remaining / daily_velocity) if daily_velocity > 0 else None
        projected_date = (today + timedelta(days=projected_days)).isoformat() if projected_days else None

        # Behind/ahead schedule
        schedule_status = None
        behind_by_pct = None
        if g.deadline:
            total_days = max((g.deadline - g.created_at.date()).days, 1) if g.created_at else 1
            expected_pct = min(((today - g.created_at.date()).days / total_days) * 100, 100) if g.created_at else 0
            if pct < expected_pct:
                behind_by_pct = round(expected_pct - pct, 1)
                schedule_status = "behind"
            else:
                schedule_status = "on_track"

        insights = []
        if schedule_status == "behind" and behind_by_pct:
            insights.append(f"Savings goal '{g.name}' is behind schedule by {behind_by_pct}%.")
        if projected_date:
            insights.append(f"At current velocity ({daily_velocity:,.0f}/day), goal will be reached by {projected_date}.")

        goal_analytics.append({
            "id": g.id,
            "name": g.name,
            "emoji": g.emoji,
            "target": g.target_amount,
            "saved": g.current_amount,
            "remaining": remaining,
            "pct_complete": pct,
            "deadline": g.deadline.isoformat() if g.deadline else None,
            "daily_velocity": round(daily_velocity, 2),
            "projected_completion_date": projected_date,
            "schedule_status": schedule_status,
            "behind_by_pct": behind_by_pct,
            "insights": insights,
        })

    # Overall savings rate this month
    now = datetime.utcnow()
    txns = _active_transactions().filter(
        extract("year", Transaction.date) == now.year,
        extract("month", Transaction.date) == now.month,
    ).all()
    inc = sum(t.amount for t in txns if t.type == "credit")
    exp = sum(t.amount for t in txns if t.type == "debit")
    savings_rate = round(_safe_div(inc - exp, inc) * 100, 1) if inc > 0 else 0

    return jsonify({
        "goals": goal_analytics,
        "current_month_savings_rate": savings_rate,
        "current_month_net_savings": round(inc - exp, 2),
    })


# ---------------------------------------------------------------------------
# 12.  PROJECTED FUTURE BALANCES  /api/agent/analytics/projections
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/projections", methods=["GET"])
@agent_auth_required
def projected_balances():
    """
    Projects future balance based on recent spending/income trends.
    Includes multi-month milestones, scenario analysis, and weekly snapshots.

    Query params:
      - horizon_days    (int, default=90, max=365 — supports multi-month planning)
      - lookback_days   (int, default=30 — period of historical data used for trend)
      - scenario        (str, 'all' returns optimistic/pessimistic/baseline, default='all')
    """
    horizon = min(int(request.args.get("horizon_days", 90)), 365)
    lookback = int(request.args.get("lookback_days", 30))
    scenario_mode = request.args.get("scenario", "all")

    # Current balance
    balances = AccountBalance.query.all()
    current_total = sum(b.current_balance for b in balances)

    # Historical data for trend calculation
    cutoff = datetime.utcnow() - timedelta(days=lookback)
    txns = _active_transactions().filter(Transaction.date >= cutoff).all()

    recent_income = sum(t.amount for t in txns if t.type == "credit")
    recent_expense = sum(t.amount for t in txns if t.type == "debit")
    days_of_data = max((datetime.utcnow() - cutoff).days, 1)

    daily_income = recent_income / days_of_data
    daily_expense = recent_expense / days_of_data
    daily_net = daily_income - daily_expense

    # Compute variance for scenario analysis
    daily_amounts = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for t in txns:
        day_key = t.date.strftime("%Y-%m-%d")
        if t.type == "credit":
            daily_amounts[day_key]["income"] += t.amount
        elif t.type == "debit":
            daily_amounts[day_key]["expense"] += t.amount

    daily_nets = [d["income"] - d["expense"] for d in daily_amounts.values()]
    if daily_nets:
        avg_daily_net = sum(daily_nets) / len(daily_nets)
        variance = sum((x - avg_daily_net) ** 2 for x in daily_nets) / len(daily_nets)
        std_dev = variance ** 0.5
    else:
        avg_daily_net = daily_net
        std_dev = 0

    # Scenario multipliers
    optimistic_daily = daily_net + std_dev * 0.5    # Spend less / earn more
    pessimistic_daily = daily_net - std_dev * 0.5   # Spend more / earn less

    # Build daily projections (baseline)
    now = datetime.utcnow()
    projections_baseline = []
    projections_optimistic = []
    projections_pessimistic = []

    for d in range(1, horizon + 1):
        proj_date = (now + timedelta(days=d)).strftime("%Y-%m-%d")
        projections_baseline.append({
            "date": proj_date,
            "projected_balance": round(current_total + daily_net * d, 2),
        })
        projections_optimistic.append({
            "date": proj_date,
            "projected_balance": round(current_total + optimistic_daily * d, 2),
        })
        projections_pessimistic.append({
            "date": proj_date,
            "projected_balance": round(current_total + pessimistic_daily * d, 2),
        })

    # Weekly milestones (every 7th day)
    weekly_milestones = [
        {
            "week": i // 7 + 1,
            "date": projections_baseline[i - 1]["date"],
            "baseline": projections_baseline[i - 1]["projected_balance"],
            "optimistic": projections_optimistic[i - 1]["projected_balance"],
            "pessimistic": projections_pessimistic[i - 1]["projected_balance"],
        }
        for i in range(7, horizon + 1, 7)
    ]

    # Month-end projections for multiple months
    from calendar import monthrange
    month_end_projections = []
    for m_offset in range(0, min(horizon // 28 + 1, 4)):
        target_month = now.month + m_offset
        target_year = now.year
        while target_month > 12:
            target_month -= 12
            target_year += 1
        _, dim = monthrange(target_year, target_month)

        if m_offset == 0:
            days_to_end = dim - now.day
        else:
            # Days from now to end of that month
            target_end = date(target_year, target_month, dim)
            days_to_end = (target_end - now.date()).days

        if days_to_end <= 0 or days_to_end > horizon:
            continue

        month_label = f"{target_year}-{target_month:02d}"
        month_end_projections.append({
            "month": month_label,
            "days_from_now": days_to_end,
            "baseline": round(current_total + daily_net * days_to_end, 2),
            "optimistic": round(current_total + optimistic_daily * days_to_end, 2),
            "pessimistic": round(current_total + pessimistic_daily * days_to_end, 2),
        })

    # Current month-end (legacy compatibility)
    _, days_in_current_month = monthrange(now.year, now.month)
    days_to_current_end = max(days_in_current_month - now.day, 0)
    month_end_balance = round(current_total + daily_net * days_to_current_end, 2)

    # Zero-balance date (when will money run out if net is negative)
    zero_balance_date = None
    if daily_net < 0 and current_total > 0:
        days_to_zero = int(current_total / abs(daily_net))
        zero_balance_date = (now + timedelta(days=days_to_zero)).strftime("%Y-%m-%d")

    # Insights
    insights = []
    insights.append(
        f"Projected liquidity at month-end: {month_end_balance:,.0f}, "
        f"assuming current spending trends (daily net: {daily_net:+,.0f})."
    )

    if len(month_end_projections) >= 2:
        m2 = month_end_projections[1]
        insights.append(f"Projected balance at end of {m2['month']}: {m2['baseline']:,.0f} (baseline).")

    if zero_balance_date:
        insights.append(f"⚠️ At current rate, balance will reach zero around {zero_balance_date}.")
    elif daily_net > 0:
        insights.append(f"✅ Positive daily net trend ({daily_net:+,.0f}/day). Balance is growing.")

    if std_dev > 0:
        insights.append(f"Spending volatility (daily std dev): {std_dev:,.0f}. Range: {pessimistic_daily:+,.0f} to {optimistic_daily:+,.0f}/day.")

    # Build response
    response = {
        "current_balance": current_total,
        "lookback_days": lookback,
        "daily_avg_income": round(daily_income, 2),
        "daily_avg_expense": round(daily_expense, 2),
        "daily_net_trend": round(daily_net, 2),
        "daily_volatility_stddev": round(std_dev, 2),
        "month_end_projected_balance": month_end_balance,
        "zero_balance_date": zero_balance_date,
        "horizon_days": horizon,
        "month_end_projections": month_end_projections,
        "weekly_milestones": weekly_milestones,
        "insights": insights,
    }

    # Include full daily projections based on scenario mode
    if scenario_mode == "all":
        response["scenarios"] = {
            "baseline": projections_baseline,
            "optimistic": projections_optimistic,
            "pessimistic": projections_pessimistic,
        }
    elif scenario_mode == "baseline":
        response["projections"] = projections_baseline
    elif scenario_mode == "optimistic":
        response["projections"] = projections_optimistic
    elif scenario_mode == "pessimistic":
        response["projections"] = projections_pessimistic
    else:
        response["projections"] = projections_baseline

    return jsonify(response)


# ---------------------------------------------------------------------------
# 13.  TRANSACTION SEARCH  /api/agent/transactions/search
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/transactions/search", methods=["GET"])
@agent_auth_required
def transaction_search():
    """
    Search/filter transactions with powerful query params.

    Query params:
      - q             (text search in purpose, notes, receiver, sender)
      - start_date    (YYYY-MM-DD)
      - end_date      (YYYY-MM-DD)
      - month         (YYYY-MM)
      - type          (credit / debit)
      - category      (category name, partial match)
      - category_id   (int)
      - source        (bank, bank_sms, manual)
      - min_amount    (float)
      - max_amount    (float)
      - include_spam  (bool, default=false)
      - include_deleted (bool, default=false)
      - sort          (date_asc, date_desc, amount_asc, amount_desc, default=date_desc)
      - limit         (int, default=50, max=200)
      - offset        (int, default=0)
    """
    q_text = request.args.get("q")
    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")
    month_param = request.args.get("month")
    tx_type = request.args.get("type")
    category = request.args.get("category")
    category_id = request.args.get("category_id")
    source = request.args.get("source")
    min_amount = request.args.get("min_amount")
    max_amount = request.args.get("max_amount")
    include_spam = request.args.get("include_spam", "false").lower() == "true"
    include_deleted = request.args.get("include_deleted", "false").lower() == "true"
    sort_by = request.args.get("sort", "date_desc")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    q = Transaction.query

    if not include_deleted:
        q = q.filter(Transaction.is_deleted != True)
    if not include_spam:
        q = q.filter(Transaction.is_spam != True)

    if month_param:
        try:
            y, m = int(month_param[:4]), int(month_param[5:7])
            q = q.filter(extract("year", Transaction.date) == y, extract("month", Transaction.date) == m)
        except Exception:
            pass
    else:
        if start_str:
            sd = _parse_date(start_str)
            if sd:
                q = q.filter(Transaction.date >= sd)
        if end_str:
            ed = _parse_date(end_str)
            if ed:
                q = q.filter(Transaction.date <= ed + timedelta(days=1))

    if tx_type:
        q = q.filter(Transaction.type == tx_type)
    if source:
        q = q.filter(Transaction.source == source)
    if min_amount:
        q = q.filter(Transaction.amount >= float(min_amount))
    if max_amount:
        q = q.filter(Transaction.amount <= float(max_amount))
    if category_id:
        q = q.filter(Transaction.category_id == int(category_id))
    if category:
        # Join with Category to search by name
        cat = Category.query.filter(Category.name.ilike(f"%{category}%")).first()
        if cat:
            q = q.filter(Transaction.category_id == cat.id)
        else:
            q = q.filter(Transaction.category_id == -1)  # No results

    if q_text:
        search = f"%{q_text}%"
        q = q.filter(
            or_(
                Transaction.purpose.ilike(search),
                Transaction.notes.ilike(search),
                Transaction.receiver.ilike(search),
                Transaction.sender.ilike(search),
            )
        )

    # Sorting
    sort_map = {
        "date_asc": Transaction.date.asc(),
        "date_desc": Transaction.date.desc(),
        "amount_asc": Transaction.amount.asc(),
        "amount_desc": Transaction.amount.desc(),
    }
    q = q.order_by(sort_map.get(sort_by, Transaction.date.desc()))

    total = q.count()
    results = q.offset(offset).limit(limit).all()
    cat_map = _build_category_map()

    txn_list = []
    for t in results:
        td = t.to_dict()
        td["category_name"] = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
        txn_list.append(td)

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": txn_list,
    })


# ---------------------------------------------------------------------------
# 14.  CATEGORY BREAKDOWN (DETAILED)  /api/agent/analytics/categories
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/categories", methods=["GET"])
@agent_auth_required
def category_breakdown():
    """
    Full category breakdown with averages, trends, and transaction lists.

    Query params:
      - month  (YYYY-MM, default = current)
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month."}), 400

    txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
    ).all()

    cat_map = _build_category_map()
    categories_data = defaultdict(lambda: {
        "income": 0.0, "expense": 0.0, "count": 0,
        "transactions": [], "info": {}
    })

    for t in txns:
        cat_info = cat_map.get(t.category_id, {"name": "Uncategorized", "color": "#64748B", "icon": "cash", "cat_type": "both"})
        cname = cat_info.get("name", "Uncategorized")
        categories_data[cname]["info"] = cat_info

        if t.type == "credit":
            categories_data[cname]["income"] += t.amount
        elif t.type == "debit":
            categories_data[cname]["expense"] += t.amount
        categories_data[cname]["count"] += 1
        categories_data[cname]["transactions"].append({
            "id": t.id,
            "date": t.date.strftime("%Y-%m-%d"),
            "amount": t.amount,
            "type": t.type,
            "purpose": t.purpose,
        })

    breakdown = []
    for name, data in sorted(categories_data.items(), key=lambda x: -(x[1]["income"] + x[1]["expense"])):
        breakdown.append({
            "category": name,
            "color": data["info"].get("color", "#64748B"),
            "icon": data["info"].get("icon", "cash"),
            "cat_type": data["info"].get("cat_type", "both"),
            "income": round(data["income"], 2),
            "expense": round(data["expense"], 2),
            "net": round(data["income"] - data["expense"], 2),
            "transaction_count": data["count"],
            "transactions": data["transactions"],
        })

    return jsonify({
        "month": month_param,
        "category_count": len(breakdown),
        "breakdown": breakdown,
    })


# ---------------------------------------------------------------------------
# 15.  INVESTMENT PERFORMANCE  /api/agent/analytics/investments
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/investments", methods=["GET"])
@agent_auth_required
def investment_performance():
    """
    Investment performance metrics.
    Currently limited to transactions categorized as 'Investment' (category_id=19).
    Returns total invested, returns, and basic metrics.
    """
    inv_cat = Category.query.filter_by(name="Investment").first()
    if not inv_cat:
        return jsonify({
            "message": "No 'Investment' category found in the database.",
            "investments": [],
            "total_invested": 0,
            "total_returns": 0,
        })

    txns = _active_transactions().filter(Transaction.category_id == inv_cat.id).order_by(Transaction.date).all()

    if not txns:
        return jsonify({
            "message": "No investment transactions found in seeders for this category.",
            "investments": [],
            "total_invested": 0,
            "total_returns": 0,
        })

    total_invested = sum(t.amount for t in txns if t.type == "debit")
    total_returns = sum(t.amount for t in txns if t.type == "credit")
    net = total_returns - total_invested
    roi = round(_safe_div(net, total_invested) * 100, 2) if total_invested else 0

    return jsonify({
        "total_invested": round(total_invested, 2),
        "total_returns": round(total_returns, 2),
        "net_profit_loss": round(net, 2),
        "roi_pct": roi,
        "transaction_count": len(txns),
        "investments": [t.to_dict() for t in txns],
    })


# ---------------------------------------------------------------------------
# 16.  STATEMENT ANALYSIS RESULTS  /api/agent/analytics/statements
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/analytics/statements", methods=["GET"])
@agent_auth_required
def statement_analysis():
    """
    Returns processed bank statement analyses.

    Query params:
      - month  (YYYY-MM, optional — returns specific month)
    """
    month_param = request.args.get("month")

    q = StatementAnalysis.query
    if month_param:
        q = q.filter_by(month=month_param)

    statements = q.order_by(StatementAnalysis.month.desc()).all()

    if not statements:
        return jsonify({
            "message": f"No statement analysis found{' for ' + month_param if month_param else ''}.",
            "statements": [],
        })

    return jsonify({
        "count": len(statements),
        "statements": [s.to_dict() for s in statements],
    })


# ---------------------------------------------------------------------------
# 17.  API DOCUMENTATION (Exposed API)  /api/agent/exposed-endpoints
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/exposed-endpoints", methods=["GET"])
@agent_auth_required
def exposed_apis():
    """
    Returns the documentation for the exposed APIs for the AI Agent.
    """
    endpoints = []
    
    # Iterate through all routes registered on the app
    for rule in current_app.url_map.iter_rules():
        # Only include routes from the ai_agent blueprint
        if rule.endpoint.startswith("ai_agent."):
            func = current_app.view_functions[rule.endpoint]
            docstring = func.__doc__ or "No description available."
            
            # Dynamically generate real sample data by executing the underlying function
            sample_data = None
            if str(rule) == "/api/agent/exposed-endpoints":
                sample_data = {"message": "Self-referencing docs endpoint."}
            else:
                try:
                    # Bypass auth decorators to get the raw endpoint function
                    original_func = getattr(func, '__wrapped__', func)
                    
                    # Execute it! (It will use the current request Context's args)
                    res = original_func()
                    
                    if isinstance(res, tuple):
                        res = res[0]
                        
                    if hasattr(res, 'get_json'):
                        sample_data = res.get_json()
                    elif hasattr(res, 'json'):
                        sample_data = res.json
                except Exception as e:
                    sample_data = {"error": f"Requires specific query parameters or payload. Detail: {str(e)}"}
                
            endpoints.append({
                "endpoint": str(rule),
                "methods": [m for m in rule.methods if m not in ("HEAD", "OPTIONS")],
                "description": docstring.strip(),
                "sample_response": sample_data
            })
            
    return jsonify({
        "count": len(endpoints),
        "apis": endpoints
    })


# ---------------------------------------------------------------------------
# 18.  ALL CATEGORIES LIST  /api/agent/categories
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/categories", methods=["GET"])
@agent_auth_required
def list_categories():
    """
    Returns all available categories with metadata.
    """
    cats = Category.query.order_by(Category.cat_type, Category.name).all()
    return jsonify({
        "count": len(cats),
        "categories": [c.to_dict() for c in cats],
    })


# ---------------------------------------------------------------------------
# 19.  COMPREHENSIVE DASHBOARD  /api/agent/dashboard
# ---------------------------------------------------------------------------

@ai_agent_bp.route("/dashboard", methods=["GET"])
@agent_auth_required
def comprehensive_dashboard():
    """
    Single-call comprehensive endpoint aggregating ALL critical analytics:
    overview, budget, top categories, monthly balance history, MoM deltas,
    recurring expenses, projections, and cumulative liquidity.

    Designed for maximum LLM utility in a single request.

    Query params:
      - month          (YYYY-MM, default = current)
      - history_months (int, default=3, months of balance history to include)
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    history_months = int(request.args.get("history_months", 3))
    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month."}), 400

    now = datetime.utcnow()
    cat_map = _build_category_map()

    # =====================================================================
    # 1. ACCOUNT BALANCES
    # =====================================================================
    balances = AccountBalance.query.all()
    total_balance = sum(b.current_balance for b in balances)

    # =====================================================================
    # 2. CURRENT MONTH TRANSACTIONS
    # =====================================================================
    txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
    ).all()

    total_income = sum(t.amount for t in txns if t.type == "credit")
    total_expense = sum(t.amount for t in txns if t.type == "debit")
    net = total_income - total_expense
    savings_rate = round(_safe_div(net, total_income) * 100, 1) if total_income > 0 else 0.0

    # =====================================================================
    # 3. BUDGET ADHERENCE
    # =====================================================================
    budget = Budget.query.filter_by(month=month_param).first()
    budget_remaining = (budget.total_budget - total_expense) if budget else None
    budget_usage_pct = round(_safe_div(total_expense, budget.total_budget) * 100, 1) if budget else None

    from calendar import monthrange
    if year == now.year and mon == now.month:
        _, dim = monthrange(year, mon)
        days_left = max(dim - now.day, 1)
        days_elapsed = now.day
    else:
        _, dim = monthrange(year, mon)
        days_left = 0
        days_elapsed = dim
    daily_safe = round(max(budget_remaining / days_left, 0), 2) if budget_remaining is not None and days_left > 0 else None

    budget_status = None
    if budget_usage_pct is not None:
        if budget_usage_pct > 100:
            budget_status = "over_budget"
        elif budget_usage_pct > 80:
            budget_status = "warning"
        else:
            budget_status = "on_track"

    # =====================================================================
    # 4. TOP CATEGORIES (current month)
    # =====================================================================
    cat_totals = defaultdict(float)
    cat_counts = defaultdict(int)
    for t in txns:
        if t.type == "debit":
            cname = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
            cat_totals[cname] += t.amount
            cat_counts[cname] += 1
    top_cats = sorted(cat_totals.items(), key=lambda x: -x[1])[:5]

    # =====================================================================
    # 5. RECENT LARGE TRANSACTIONS
    # =====================================================================
    recent_large = sorted(
        [t for t in txns if t.type == "debit" and t.amount >= 1000],
        key=lambda x: -x.amount
    )[:5]

    # =====================================================================
    # 6. SAVINGS GOALS
    # =====================================================================
    goals = SavingsGoal.query.all()
    goals_data = []
    for g in goals:
        pct = round(_safe_div(g.current_amount, g.target_amount) * 100, 1)
        goals_data.append({
            "name": g.name, "pct": pct,
            "saved": g.current_amount, "target": g.target_amount,
            "remaining": g.remaining,
            "emoji": g.emoji,
            "deadline": g.deadline.isoformat() if g.deadline else None,
        })

    # =====================================================================
    # 7. MONTHLY BALANCE HISTORY (multi-month trend)
    # =====================================================================
    balance_history = []
    cumulative_liquidity = []
    cumulative_net_total = 0.0

    for offset in range(history_months - 1, -1, -1):
        h_dt = date(year, mon, 1) - timedelta(days=offset * 30)
        h_y, h_m = h_dt.year, h_dt.month
        h_str = f"{h_y}-{h_m:02d}"

        mb = MonthlyBalance.query.filter_by(month=h_str).first()

        h_txns = _active_transactions().filter(
            extract("year", Transaction.date) == h_y,
            extract("month", Transaction.date) == h_m,
        ).all()
        h_inc = sum(t.amount for t in h_txns if t.type == "credit")
        h_exp = sum(t.amount for t in h_txns if t.type == "debit")
        h_net = h_inc - h_exp
        h_savings_rate = round(_safe_div(h_net, h_inc) * 100, 1) if h_inc > 0 else 0.0
        cumulative_net_total += h_net

        balance_history.append({
            "month": h_str,
            "opening_balance": mb.opening_balance if mb else None,
            "closing_balance": mb.closing_balance if mb else None,
            "balance_change": round(mb.closing_balance - mb.opening_balance, 2) if mb else None,
            "income": round(h_inc, 2),
            "expense": round(h_exp, 2),
            "net": round(h_net, 2),
            "savings_rate": h_savings_rate,
            "transaction_count": len(h_txns),
        })

        cumulative_liquidity.append({
            "month": h_str,
            "cumulative_net": round(cumulative_net_total, 2),
        })

    # MoM deltas within the history
    for i in range(1, len(balance_history)):
        prev = balance_history[i - 1]
        curr = balance_history[i]
        curr["mom_income_change"] = round(curr["income"] - prev["income"], 2)
        curr["mom_expense_change"] = round(curr["expense"] - prev["expense"], 2)
        curr["mom_net_change"] = round(curr["net"] - prev["net"], 2)
        curr["mom_income_change_pct"] = round(_safe_div(curr["mom_income_change"], prev["income"]) * 100, 1) if prev["income"] else None
        curr["mom_expense_change_pct"] = round(_safe_div(curr["mom_expense_change"], prev["expense"]) * 100, 1) if prev["expense"] else None

    # =====================================================================
    # 8. RECURRING EXPENSES (quick inline detection)
    # =====================================================================
    recurring_cutoff = now - timedelta(days=90)
    recurring_txns = (
        _active_transactions()
        .filter(Transaction.date >= recurring_cutoff, Transaction.type == "debit")
        .order_by(Transaction.date)
        .all()
    )
    purpose_groups = defaultdict(list)
    for t in recurring_txns:
        key = (t.purpose or "Unknown").strip().lower()
        purpose_groups[key].append(t)

    recurring_items = []
    for purpose, tx_list in purpose_groups.items():
        if len(tx_list) < 2:
            continue
        amounts = [t.amount for t in tx_list]
        dates_sorted = sorted([t.date for t in tx_list])
        avg_amount = sum(amounts) / len(amounts)

        if len(dates_sorted) >= 2:
            intervals = [(dates_sorted[j + 1] - dates_sorted[j]).days for j in range(len(dates_sorted) - 1)]
            avg_interval = sum(intervals) / len(intervals)
        else:
            avg_interval = 0

        max_dev = max(abs(a - avg_amount) for a in amounts)
        consistency = 1.0 - min(max_dev / avg_amount, 1.0) if avg_amount else 0
        confidence = round(consistency * 0.5 + min(len(tx_list) / 6, 1.0) * 0.5, 2)

        if confidence >= 0.5:
            recurring_items.append({
                "purpose": purpose.title(),
                "avg_amount": round(avg_amount, 2),
                "occurrences": len(tx_list),
                "avg_interval_days": round(avg_interval, 1),
                "confidence": confidence,
                "category": _category_name(tx_list[-1].category_id),
            })
    recurring_items.sort(key=lambda x: -x["confidence"])
    total_monthly_recurring = round(
        sum(r["avg_amount"] * (30 / max(r["avg_interval_days"], 1)) for r in recurring_items), 2
    )

    # =====================================================================
    # 9. PROJECTIONS (inline)
    # =====================================================================
    lookback_cutoff = now - timedelta(days=30)
    proj_txns = _active_transactions().filter(Transaction.date >= lookback_cutoff).all()
    proj_income = sum(t.amount for t in proj_txns if t.type == "credit")
    proj_expense = sum(t.amount for t in proj_txns if t.type == "debit")
    proj_days = max((now - lookback_cutoff).days, 1)
    daily_net_trend = (proj_income - proj_expense) / proj_days

    month_end_balance = round(total_balance + daily_net_trend * max(days_left, 0), 2)

    # Next month projection
    next_month = mon + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1
    _, next_dim = monthrange(next_year, next_month)
    days_to_next_month_end = days_left + next_dim
    next_month_end_balance = round(total_balance + daily_net_trend * days_to_next_month_end, 2)

    zero_balance_date = None
    if daily_net_trend < 0 and total_balance > 0:
        days_to_zero = int(total_balance / abs(daily_net_trend))
        zero_balance_date = (now + timedelta(days=days_to_zero)).strftime("%Y-%m-%d")

    projections_data = {
        "daily_net_trend": round(daily_net_trend, 2),
        "daily_avg_income": round(proj_income / proj_days, 2),
        "daily_avg_expense": round(proj_expense / proj_days, 2),
        "month_end_balance": month_end_balance,
        "next_month_end_balance": {
            "month": f"{next_year}-{next_month:02d}",
            "projected": next_month_end_balance,
        },
        "zero_balance_date": zero_balance_date,
    }

    # =====================================================================
    # 10. INSIGHTS (aggregated)
    # =====================================================================
    insights = []

    # Budget insight
    if budget and budget_usage_pct is not None:
        if budget_status == "over_budget":
            insights.append(f"⚠️ Over budget by {abs(budget_remaining):,.0f}. Spent {total_expense:,.0f} vs budget {budget.total_budget:,.0f}.")
        elif budget_status == "warning":
            insights.append(f"⚡ {budget_usage_pct}% of budget consumed with {days_left} days remaining.")
        else:
            insights.append(f"✅ Budget on track: {budget_remaining:,.0f} remaining ({budget_usage_pct}% used).")

    if daily_safe is not None:
        insights.append(f"Daily safe spending allowance: {daily_safe:,.0f}/day for {days_left} remaining days.")

    # Savings rate
    insights.append(f"Current month savings rate: {savings_rate}% (net: {net:+,.0f}).")

    # Top spending
    if top_cats:
        cats_str = ", ".join([f"{n} ({a:,.0f})" for n, a in top_cats[:3]])
        insights.append(f"Top spending categories: {cats_str}.")

    # MoM comparison
    if len(balance_history) >= 2:
        curr_h = balance_history[-1]
        prev_h = balance_history[-2]
        if prev_h["expense"] > 0:
            exp_change_pct = round(((curr_h["expense"] - prev_h["expense"]) / prev_h["expense"]) * 100, 1)
            direction = "increased" if exp_change_pct > 0 else "decreased"
            insights.append(f"Expenses {direction} by {abs(exp_change_pct)}% vs {prev_h['month']}.")
        if prev_h.get("closing_balance") and curr_h.get("closing_balance"):
            bal_change_pct = round(((curr_h["closing_balance"] - prev_h["closing_balance"]) / abs(prev_h["closing_balance"])) * 100, 1)
            direction = "increased" if bal_change_pct > 0 else "decreased"
            insights.append(f"Monthly net balance {direction} by {abs(bal_change_pct)}% vs previous month.")

    # Recurring expenses
    if recurring_items:
        insights.append(f"Detected {len(recurring_items)} recurring expenses totaling ~{total_monthly_recurring:,.0f}/month.")

    # Projections
    insights.append(f"Projected month-end balance: {month_end_balance:,.0f} (daily net: {daily_net_trend:+,.0f}).")
    if zero_balance_date:
        insights.append(f"⚠️ At current rate, balance reaches zero around {zero_balance_date}.")

    # Cumulative liquidity
    insights.append(f"Cumulative net cash flow over {len(balance_history)} months: {cumulative_net_total:+,.0f}.")

    return jsonify({
        "month": month_param,
        "generated_at": now.isoformat(),

        # Core metrics
        "total_balance": total_balance,
        "accounts": [b.to_dict() for b in balances],
        "income": round(total_income, 2),
        "expense": round(total_expense, 2),
        "net": round(net, 2),
        "savings_rate": savings_rate,
        "transaction_count": len(txns),

        # Budget
        "budget": {
            "total": budget.total_budget,
            "needs": budget.needs,
            "wants": budget.wants,
            "saving": budget.saving,
            "spent": round(total_expense, 2),
            "remaining": round(budget_remaining, 2),
            "usage_pct": budget_usage_pct,
            "daily_safe_spend": daily_safe,
            "days_remaining": days_left,
            "status": budget_status,
        } if budget else None,

        # Top categories
        "top_categories": [
            {"name": name, "amount": round(amt, 2), "count": cat_counts.get(name, 0),
             "pct_of_total": round(_safe_div(amt, total_expense) * 100, 1)}
            for name, amt in top_cats
        ],

        # Large transactions
        "recent_large_transactions": [
            {"id": t.id, "date": t.date.strftime("%Y-%m-%d"), "amount": t.amount,
             "purpose": t.purpose, "category": cat_map.get(t.category_id, {}).get("name", "Uncategorized")}
            for t in recent_large
        ],

        # Savings goals
        "savings_goals": goals_data,

        # Multi-month balance history with MoM deltas
        "balance_history": balance_history,
        "cumulative_liquidity": cumulative_liquidity,

        # Recurring expenses
        "recurring_expenses": {
            "items": recurring_items[:5],  # Top 5 for dashboard
            "total_monthly_recurring": total_monthly_recurring,
        },

        # Projections
        "projections": projections_data,

        # All insights for LLM
        "insights": insights,
    })
