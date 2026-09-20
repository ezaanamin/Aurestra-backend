import re
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from flask import request, jsonify
from sqlalchemy import func

from model import Transaction, Budget, AccountBalance
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from decorator.helpers import sum_month_expenses

_EXCLUDED_PURPOSES = {'Self transfer', 'self transfer', 'Self Transfer', 'self-transfer', 'own account transfer'}

CASH_KINDS = {"cash", "bank", "mobile_wallet", "digital_bank"}
INVESTMENT_KINDS = {"investment", "investments", "stock", "stocks", "crypto", "mutual_fund"}
LIABILITY_KINDS = {"credit", "credit_card", "line_of_credit", "loan", "liability"}

def get_finance_snapshot(current_user):
    """
    GET /api/assistant/finance-snapshot
    Returns high-level finance snapshot card data for Elyestra Desktop.
    """
    now = datetime.now()
    curr_period = now.strftime("%Y-%m")
    prev_dt = now - relativedelta(months=1)
    prev_period = prev_dt.strftime("%Y-%m")

    # 1. Wallets (strictly user-scoped read-only query; guarantees zero writes)
    accounts = (
        AccountBalance.query
        .filter_by(user_id=current_user.id)
        .filter(AccountBalance.is_deleted.isnot(True))
        .order_by(AccountBalance.sort_order, AccountBalance.id)
        .all()
    )
    wallets = []
    for acc in accounts:
        w_bal = round(float(acc.current_balance or 0.0), 2)
        wallets.append({
            "name": acc.display_name or acc.source or "Wallet",
            "slug": acc.source or "",
            "type": acc.account_kind or "bank",
            "balance": w_bal
        })

    cash_total = round(sum(w["balance"] for w in wallets if (w["type"] or "").lower() in CASH_KINDS), 2)
    investments_total = round(sum(w["balance"] for w in wallets if (w["type"] or "").lower() in INVESTMENT_KINDS), 2)
    liabilities_total = round(sum(w["balance"] for w in wallets if (w["type"] or "").lower() in LIABILITY_KINDS), 2)
    net_worth_total = round(sum(w["balance"] for w in wallets if (w["type"] or "").lower() not in LIABILITY_KINDS) - liabilities_total, 2)

    # 2. Net change since 1st of month from transactions
    start_of_month = datetime(now.year, now.month, 1)
    month_txs = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= start_of_month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    ).all()

    credits_month = sum(float(t.amount or 0.0) for t in month_txs if t.type == 'credit')
    debits_month = sum(float(t.amount or 0.0) for t in month_txs if t.type == 'debit')
    net_flow_month = credits_month - debits_month

    cash_change_amount = round(net_flow_month, 2)
    start_net_worth = net_worth_total - net_flow_month
    net_worth_change_pct = round(((net_worth_total - start_net_worth) / start_net_worth) * 100, 1) if start_net_worth > 0 else None

    # 3. Spending (reuse sum_month_expenses from decorator/helpers)
    curr_spent = sum_month_expenses(current_user.id, now.year, now.month)
    last_spent = sum_month_expenses(current_user.id, prev_dt.year, prev_dt.month)
    spend_change_pct = round(((curr_spent - last_spent) / last_spent) * 100, 1) if last_spent > 0 else None

    curr_debits = [t for t in month_txs if t.type == 'debit']
    cat_totals = {}
    for t in curr_debits:
        if is_own_account_transfer_row(t) or t.purpose in _EXCLUDED_PURPOSES:
            continue
        c_name = t.purpose or "Uncategorized"
        cat_totals[c_name] = cat_totals.get(c_name, 0.0) + float(t.amount or 0.0)

    prev_start = datetime(prev_dt.year, prev_dt.month, 1)
    prev_txs = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= prev_start,
        Transaction.date < start_of_month,
        Transaction.type == 'debit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    ).all()
    prev_cat_totals = {}
    for t in prev_txs:
        if is_own_account_transfer_row(t) or t.purpose in _EXCLUDED_PURPOSES:
            continue
        c_name = t.purpose or "Uncategorized"
        prev_cat_totals[c_name] = prev_cat_totals.get(c_name, 0.0) + float(t.amount or 0.0)

    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5]
    top_categories = []
    for c_name, c_amt in sorted_cats:
        c_amt_round = round(c_amt, 2)
        share_pct = round((c_amt_round / curr_spent) * 100, 1) if curr_spent > 0 else 0.0
        p_amt = prev_cat_totals.get(c_name, 0.0)
        c_change_pct = round(((c_amt_round - p_amt) / p_amt) * 100, 1) if p_amt > 0 else None
        top_categories.append({
            "category": c_name,
            "amount": c_amt_round,
            "share_pct": share_pct,
            "change_pct_vs_last_month": c_change_pct,
            "budget": None,
            "over_budget_by": None
        })

    # 4. Alerts (derived purely from real data)
    alerts = []
    # Negative balance
    for w in wallets:
        if w["balance"] < 0:
            alerts.append({
                "type": "negative_balance",
                "category": "",
                "message": f"Wallet '{w['name']}' has a negative balance of PKR {w['balance']:,.2f}.",
                "severity": "critical"
            })
    # Spending spikes (>20% vs last month)
    for c_name, c_amt in cat_totals.items():
        p_amt = prev_cat_totals.get(c_name, 0.0)
        if p_amt > 0 and ((c_amt - p_amt) / p_amt) > 0.20:
            spike_pct = round(((c_amt - p_amt) / p_amt) * 100, 1)
            alerts.append({
                "type": "spending_spike",
                "category": c_name,
                "message": f"Spending on '{c_name}' is up {spike_pct}% vs last month (PKR {c_amt:,.2f} vs PKR {p_amt:,.2f}).",
                "severity": "warning"
            })
    # Over total budget
    budget_obj = Budget.query.filter_by(user_id=current_user.id, month=curr_period).first()
    if budget_obj and budget_obj.total_budget > 0 and curr_spent > budget_obj.total_budget:
        over_by = round(curr_spent - budget_obj.total_budget, 2)
        alerts.append({
            "type": "over_budget",
            "category": "Total",
            "message": f"Monthly spending (PKR {curr_spent:,.2f}) exceeded total budget (PKR {budget_obj.total_budget:,.2f}) by PKR {over_by:,.2f}.",
            "severity": "critical"
        })

    return jsonify({
        "currency": "PKR",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "net_worth": { "amount": net_worth_total, "change_pct": net_worth_change_pct },
        "cash": { "amount": cash_total, "change_amount": cash_change_amount },
        "investments": { "amount": investments_total, "change_amount": None },
        "wallets": wallets,
        "spending": {
            "period": curr_period,
            "total": round(curr_spent, 2),
            "last_month_total": round(last_spent, 2),
            "change_pct_vs_last_month": spend_change_pct,
            "top_categories": top_categories
        },
        "alerts": alerts
    }), 200


def get_spending(current_user):
    """
    GET /api/assistant/spending?period=YYYY-MM&category=<optional>
    Chat helper route for spending queries.
    """
    period_param = request.args.get("period", "").strip()
    category_param = request.args.get("category", "").strip()

    if not period_param:
        period_param = datetime.now().strftime("%Y-%m")

    # Strict YYYY-MM validation
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period_param):
        return jsonify({"error": "Invalid period format. Must be YYYY-MM."}), 400

    y, m = map(int, period_param.split("-"))
    start_date = datetime(y, m, 1)
    end_date = start_date + relativedelta(months=1)
    prev_start = start_date - relativedelta(months=1)

    # Parameterized query on transactions
    base_query = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= start_date,
        Transaction.date < end_date,
        Transaction.type == 'debit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    )

    if category_param:
        base_query = base_query.filter(func.lower(func.trim(Transaction.purpose)) == category_param.lower())

    txns = base_query.order_by(Transaction.date.desc()).all()
    filtered_txns = [t for t in txns if not is_own_account_transfer_row(t) and t.purpose not in _EXCLUDED_PURPOSES]

    # Previous month query for comparison
    prev_query = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= prev_start,
        Transaction.date < start_date,
        Transaction.type == 'debit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    )
    if category_param:
        prev_query = prev_query.filter(func.lower(func.trim(Transaction.purpose)) == category_param.lower())
    prev_txns = [t for t in prev_query.all() if not is_own_account_transfer_row(t) and t.purpose not in _EXCLUDED_PURPOSES]
    prev_total_by_cat = {}
    for t in prev_txns:
        c = t.purpose or "Uncategorized"
        prev_total_by_cat[c] = prev_total_by_cat.get(c, 0.0) + float(t.amount or 0.0)

    # Aggregate categories
    cat_groups = {}
    merchant_groups = {}
    total_amount = 0.0

    for t in filtered_txns:
        amt = float(t.amount or 0.0)
        total_amount += amt
        c = t.purpose or "Uncategorized"
        cat_groups[c] = cat_groups.get(c, 0.0) + amt

        # Merchant extraction (receiver or notes)
        m_name = (t.receiver if (t.receiver and t.receiver != "Merchant") else (t.notes or t.receiver or "Unknown")).strip()
        if m_name not in merchant_groups:
            merchant_groups[m_name] = {"category": c, "amount": 0.0, "count": 0}
        merchant_groups[m_name]["amount"] += amt
        merchant_groups[m_name]["count"] += 1

    categories_res = []
    for c_name, c_amt in sorted(cat_groups.items(), key=lambda x: x[1], reverse=True):
        p_amt = prev_total_by_cat.get(c_name, 0.0)
        change_pct = round(((c_amt - p_amt) / p_amt) * 100, 1) if p_amt > 0 else None
        categories_res.append({
            "category": c_name,
            "amount": round(c_amt, 2),
            "change_pct_vs_last_month": change_pct,
            "budget": None,
            "over_budget_by": None
        })

    sorted_merchants = sorted(merchant_groups.items(), key=lambda x: x[1]["amount"], reverse=True)[:10]
    top_merchants = [
        {"name": m_name, "category": m_info["category"], "amount": round(m_info["amount"], 2), "count": m_info["count"]}
        for m_name, m_info in sorted_merchants
    ]

    # Wallet display name lookup
    wallet_map = {a.source: a.display_name for a in AccountBalance.query.filter_by(user_id=current_user.id).all()}

    # Transactions list: up to 20 latest if category is given, empty if no category
    transactions_res = []
    if category_param:
        for t in filtered_txns[:20]:
            m_name = (t.receiver if (t.receiver and t.receiver != "Merchant") else (t.notes or t.receiver or "Unknown")).strip()
            transactions_res.append({
                "date": t.date.strftime("%Y-%m-%d") if t.date else "",
                "amount": round(float(t.amount or 0.0), 2),
                "category": t.purpose or "Uncategorized",
                "merchant": m_name,
                "wallet": wallet_map.get(t.account_balance_source, t.account_balance_source or "Unknown")
            })

    return jsonify({
        "currency": "PKR",
        "period": period_param,
        "total": round(total_amount, 2),
        "categories": categories_res,
        "top_merchants": top_merchants,
        "transactions": transactions_res
    }), 200
