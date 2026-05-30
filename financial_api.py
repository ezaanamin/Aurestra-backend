import os
import jwt
from datetime import datetime, timedelta, date
from functools import wraps
from collections import defaultdict
from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import extract, func

from database import db
from transfer_matching import exclude_own_account_transfer_sql
from model import (
    Transaction, MonthlyBalance, Budget, AccountBalance,
    SavingsGoal, Category, StatementAnalysis, User
)

from ai_agent_api import (
    _active_transactions, _month_str, _safe_div, _build_category_map,
    _parse_date, _category_name
)

from ai_agent_api import (
    anomaly_detection as ai_anomaly_detection,
    recurring_expenses as ai_recurring_expenses,
    savings_analytics as ai_savings_analytics,
    projected_balances as ai_projected_balances,
    cashflow_trends as ai_cashflow_trends,
    month_over_month as ai_month_over_month,
    transaction_search as ai_transaction_search,
    category_breakdown as ai_category_breakdown,
    monthly_balance_trends as ai_monthly_balance_trends
)

financial_api_bp = Blueprint("financial_api", __name__, url_prefix="/api")

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = os.environ.get("SECRET_KEY", "0aefb44af279f5bb0ad9ecce393be138")

        # Read from Authorization header — strips "Bearer " prefix if present
        auth = request.headers.get('Authorization', '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else auth

        if not token or token != expected:
            return jsonify({'message': 'Invalid or missing API key!'}), 401

        current_user = User.query.order_by(User.id).first()
        if not current_user:
            return jsonify({'message': 'No user found in database!'}), 401

        return f(current_user, *args, **kwargs)
    return decorated


# =============================================================================
# API Group 1: Executive Insights
# =============================================================================

@financial_api_bp.route("/executive/snapshot", methods=["GET"])
@token_required
def executive_snapshot(current_user):
    """
    Concise dashboard card: current balance, this month's performance,
    budget health, and actionable insights.
    """
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    try:
        year, mon = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month format. Use YYYY-MM."}), 400

    now = datetime.utcnow()
    
    # 1. Balances
    balances = AccountBalance.query.all()
    total_balance = sum(b.current_balance for b in balances)
    account_list = [{"source": b.source, "display_name": b.display_name, "balance": b.current_balance} for b in balances]

    # 2. This Month Performance
    txns = _active_transactions().filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == mon,
    ).all()

    income = sum(t.amount for t in txns if t.type == "credit")
    expense = sum(t.amount for t in txns if t.type == "debit")
    net = income - expense
    savings_rate = round(_safe_div(net, income) * 100, 1) if income > 0 else 0.0

    from calendar import monthrange
    if year == now.year and mon == now.month:
        _, dim = monthrange(year, mon)
        days_remaining = max(dim - now.day, 1)
        days_elapsed = now.day
    else:
        _, dim = monthrange(year, mon)
        days_remaining = 0
        days_elapsed = dim

    # 3. Budget
    budget = Budget.query.filter_by(month=month_param).first()
    budget_data = None
    budget_status = "unknown"
    if budget:
        budget_spent = expense
        budget_remaining = budget.total_budget - budget_spent
        usage_pct = round(_safe_div(budget_spent, budget.total_budget) * 100, 1)
        daily_safe = round(max(budget_remaining / days_remaining, 0), 2) if days_remaining > 0 else 0
        
        if usage_pct > 100:
            budget_status = "over_budget"
        elif usage_pct > 80:
            budget_status = "warning"
        else:
            budget_status = "on_track"
            
        budget_data = {
            "total": budget.total_budget,
            "spent": round(budget_spent, 2),
            "remaining": round(budget_remaining, 2),
            "usage_pct": usage_pct,
            "daily_safe_spend": daily_safe,
            "status": budget_status
        }

    # 4. Savings Goals
    goals = SavingsGoal.query.all()
    total_target = sum(g.target_amount for g in goals)
    total_saved = sum(g.current_amount for g in goals)
    overall_pct = round(_safe_div(total_saved, total_target) * 100, 1) if total_target > 0 else 0.0
    
    # 5. Top Categories
    cat_map = _build_category_map()
    cat_totals = defaultdict(float)
    for t in txns:
        if t.type == "debit":
            cname = cat_map.get(t.category_id, {}).get("name", "Uncategorized")
            cat_totals[cname] += t.amount
    
    top_cats = sorted(cat_totals.items(), key=lambda x: -x[1])[:3]
    top_categories_list = [
        {"name": name, "amount": round(amt, 2), "pct_of_expense": round(_safe_div(amt, expense) * 100, 1)}
        for name, amt in top_cats
    ]

    # 6. Comparison (MoM)
    prev_dt = date(year, mon, 1) - timedelta(days=1)
    prev_month_str = _month_str(prev_dt)
    prev_txns = _active_transactions().filter(
        extract("year", Transaction.date) == prev_dt.year,
        extract("month", Transaction.date) == prev_dt.month,
    ).all()
    
    prev_income = sum(t.amount for t in prev_txns if t.type == "credit")
    prev_expense = sum(t.amount for t in prev_txns if t.type == "debit")
    prev_net = prev_income - prev_expense
    
    comp_data = {
        "prev_month": prev_month_str,
        "income_change_pct": round(_safe_div(income - prev_income, prev_income) * 100, 1) if prev_income else None,
        "expense_change_pct": round(_safe_div(expense - prev_expense, prev_expense) * 100, 1) if prev_expense else None,
        "net_change": round(net - prev_net, 2)
    }

    return jsonify({
        "month": month_param,
        "generated_at": now.isoformat(),
        "total_balance": round(total_balance, 2),
        "accounts": account_list,
        "this_month": {
            "income": round(income, 2),
            "expense": round(expense, 2),
            "net": round(net, 2),
            "savings_rate": savings_rate,
            "transaction_count": len(txns),
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining
        },
        "budget": budget_data,
        "savings_goals": {
            "active_count": len(goals),
            "total_target": total_target,
            "total_saved": total_saved,
            "overall_pct": overall_pct
        },
        "top_categories": top_categories_list,
        "comparison": comp_data
    }), 200

# =============================================================================
# API Group 2: Financial Intelligence
# =============================================================================

@financial_api_bp.route("/financial-intelligence/months", methods=["GET"])
@token_required
def get_months(current_user):
    limit = request.args.get("limit", 24, type=int)

    records = (
        MonthlyBalance.query
        .order_by(MonthlyBalance.month.desc())
        .limit(limit)
        .all()
    )

    return jsonify({
        "months": [
            {
                "month": r.month,
                "opening_balance": max(r.opening_balance or 0, 0),
                "closing_balance": max(r.closing_balance or 0, 0),
                "expense": max(r.expense or 0, 0),
                "savings": max(r.savings or 0, 0),
            }
            for r in records
        ]
    })

@financial_api_bp.route("/financial-intelligence/months/<month_str>", methods=["GET"])
@token_required
def get_month_deep_dive(current_user, month_str):
    """Full deep-dive analysis for a specific month."""
    # We can effectively reuse the comprehensive dashboard logic from ai_agent_api
    # by simulating a request to it or rewriting it.
    # To keep it DRY, we call the same logic but without the AI agent auth.
    try:
        from ai_agent_api import comprehensive_dashboard as ai_dash
        # Temporarily override request args
        original_args = request.args
        request.args = {"month": month_str}
        
        # Bypass the agent auth decorator
        func_to_call = getattr(ai_dash, '__wrapped__', ai_dash)
        response = func_to_call()
        
        request.args = original_args
        return response
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/transactions", methods=["GET"])
@token_required
def get_transactions(current_user):
    """Return all transactions with minimal fields."""

    try:
        transactions = (
            Transaction.query
            .filter(Transaction.is_deleted == False)
            .order_by(Transaction.date.desc())
            .all()
        )

        return jsonify({
            "count": len(transactions),
            "results": [
                {
                    "amount": t.amount,
                    "sender": t.sender,
                    "date": t.date.isoformat() if t.date else None,
                    "type": t.type
                }
                for t in transactions
            ]
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/categories", methods=["GET"])
@token_required
def get_categories(current_user):
    """All spending/income categories with their current-month totals."""
    try:
        func_to_call = getattr(ai_category_breakdown, '__wrapped__', ai_category_breakdown)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/trends", methods=["GET"])
@token_required
def get_trends(current_user):
    """Spending/income trends across periods (month, year)."""
    try:
        func_to_call = getattr(ai_monthly_balance_trends, '__wrapped__', ai_monthly_balance_trends)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/comparisons", methods=["GET"])
@token_required
def get_comparisons(current_user):
    """Side-by-side month-over-month comparison."""
    try:
        func_to_call = getattr(ai_month_over_month, '__wrapped__', ai_month_over_month)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/cashflow", methods=["GET"])
@token_required
def get_cashflow(current_user):
    """Daily cash flow with rolling averages."""
    try:
        func_to_call = getattr(ai_cashflow_trends, '__wrapped__', ai_cashflow_trends)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/merchants", methods=["GET"])
@token_required
def get_merchants(current_user):
    """Merchant/sender/receiver analytics."""
    month_param = request.args.get("month", _month_str(datetime.utcnow()))
    tx_type = request.args.get("type", "debit")
    top_n = request.args.get("top_n", 20, type=int)
    
    try:
        y, m = int(month_param[:4]), int(month_param[5:7])
    except (ValueError, IndexError):
        return jsonify({"error": "Invalid month."}), 400

    txns = _active_transactions().filter(
        extract("year", Transaction.date) == y,
        extract("month", Transaction.date) == m,
        Transaction.type == tx_type
    ).all()
    
    merchants = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for t in txns:
        name = t.receiver if tx_type == "debit" else t.sender
        name = (name or "Unknown").strip().title()
        if not name:
            name = "Unknown"
        merchants[name]["amount"] += t.amount
        merchants[name]["count"] += 1
        
    sorted_merchants = sorted(merchants.items(), key=lambda x: -x[1]["amount"])[:top_n]
    total = sum(m["amount"] for m in merchants.values())
    
    return jsonify({
        "month": month_param,
        "type": tx_type,
        "total_amount": round(total, 2),
        "merchants": [
            {
                "name": name,
                "amount": round(data["amount"], 2),
                "count": data["count"],
                "pct_of_total": round(_safe_div(data["amount"], total) * 100, 1)
            } for name, data in sorted_merchants
        ]
    })

@financial_api_bp.route("/financial-intelligence/anomalies", methods=["GET"])
@token_required
def get_anomalies(current_user):
    """Category-level deviations from historical averages."""
    try:
        func_to_call = getattr(ai_anomaly_detection, '__wrapped__', ai_anomaly_detection)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/recurring", methods=["GET"])
@token_required
def get_recurring(current_user):
    """Subscription / recurring expense detection."""
    try:
        func_to_call = getattr(ai_recurring_expenses, '__wrapped__', ai_recurring_expenses)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/savings", methods=["GET"])
@token_required
def get_savings(current_user):
    """Savings goal progress and projections."""
    try:
        func_to_call = getattr(ai_savings_analytics, '__wrapped__', ai_savings_analytics)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financial_api_bp.route("/financial-intelligence/accounts", methods=["GET"])
@token_required
def get_accounts_analytics(current_user):
    """Account-level analytics - per-wallet income/expense breakdown."""
    months = request.args.get("months", 3, type=int)
    cutoff = datetime.utcnow() - timedelta(days=30 * months)
    
    balances = AccountBalance.query.all()
    txns = _active_transactions().filter(Transaction.date >= cutoff).all()
    
    acc_data = {}
    for b in balances:
        acc_data[b.source] = {
            "display_name": b.display_name,
            "current_balance": b.current_balance,
            "income": 0.0,
            "expense": 0.0,
            "net": 0.0,
            "transaction_count": 0
        }
        
    for t in txns:
        if t.source not in acc_data:
            continue
        if t.type == "credit":
            acc_data[t.source]["income"] += t.amount
            acc_data[t.source]["net"] += t.amount
        elif t.type == "debit":
            acc_data[t.source]["expense"] += t.amount
            acc_data[t.source]["net"] -= t.amount
        acc_data[t.source]["transaction_count"] += 1
        
    return jsonify({
        "months_analyzed": months,
        "accounts": [
            {
                "source": k,
                **v
            } for k, v in acc_data.items()
        ]
    })

@financial_api_bp.route("/financial-intelligence/reports", methods=["GET"])
@token_required
def get_reports(current_user):
    """Available bank statement analyses."""
    try:
        from ai_agent_api import statement_analysis as ai_statements
        func_to_call = getattr(ai_statements, '__wrapped__', ai_statements)
        return func_to_call()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

