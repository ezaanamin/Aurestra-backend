# services/analytics_service.py — Dynamic, real backend financial analytics service

from datetime import datetime, date, timedelta, timezone
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, extract, desc, case
from database import db
from model import Transaction, AccountBalance, NetWorthHistory, Category, CategoryBucketMapping, CategoryPrivacySetting
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row

CATEGORY_COLOR_PALETTE = {
    "shopping": "#A78BFA",            # Lavender
    "food & snacks": "#FF6B6B",        # Coral
    "food": "#FF6B6B",
    "transport": "#22D3EE",            # Cyan
    "uber": "#22D3EE",
    "ride / transport": "#22D3EE",
    "education": "#3B82F6",            # Blue
    "entertainment": "#EC4899",        # Pink
    "game": "#EC4899",
    "games": "#EC4899",
    "bills & utilities": "#F59E0B",    # Amber
    "bills": "#F59E0B",
    "utilities": "#F59E0B",
    "therapy": "#8B5CF6",              # Violet
    "healthcare": "#8B5CF6",
    "groceries": "#10B981",            # Emerald
    "personal care": "#FB7185",        # Rose
    "investment": "#6366F1",           # Indigo
    "subscriptions": "#38BDF8",        # Sky
    "subscription": "#38BDF8",
    "audible subscription": "#38BDF8",
    "google one subscription": "#38BDF8",
    "comic book": "#EC4899",
    "tea": "#F59E0B",
    "movies": "#EC4899",
    "gym & fitness": "#FF6B6B",
    "bank reduction": "#F59E0B",
    "retracted": "#64748B",            # Muted Slate
}

DISTINCT_PALETTE = [
    "#A78BFA", "#FF6B6B", "#22D3EE", "#3B82F6", "#EC4899",
    "#F59E0B", "#8B5CF6", "#10B981", "#FB7185", "#6366F1",
    "#38BDF8", "#34D399", "#F472B6", "#E879F9", "#FBBF24"
]

def get_canonical_category_color(name: str, cat_id: int = None) -> str:
    if not name:
        return "#64748B"
    clean_name = str(name).strip().lower()
    if clean_name in CATEGORY_COLOR_PALETTE:
        return CATEGORY_COLOR_PALETTE[clean_name]
    for k, v in CATEGORY_COLOR_PALETTE.items():
        if k in clean_name or clean_name in k:
            return v
    # Deterministic hash
    idx = (abs(hash(clean_name)) + (cat_id or 0)) % len(DISTINCT_PALETTE)
    return DISTINCT_PALETTE[idx]



def get_current_total_balance(user_id: int) -> float:
    """Calculates total balance across user's active accounts/wallets."""
    accounts = AccountBalance.query.filter_by(user_id=user_id).all()
    if not accounts:
        return 0.0
    return sum(float(a.current_balance or 0.0) for a in accounts)


def record_daily_snapshot_if_missing(user_id: int, target_date: date = None) -> NetWorthHistory:
    """
    Idempotent snapshot generation for a user on a given date (default today).
    Uses actual sum of AccountBalance rows.
    """
    if target_date is None:
        tz = timezone(timedelta(hours=5))
        target_date = datetime.now(tz).date()

    existing = NetWorthHistory.query.filter_by(user_id=user_id, date=target_date).first()
    curr_balance = get_current_total_balance(user_id)

    if existing:
        # Update snapshot to match latest live state for today
        existing.net_worth_value = curr_balance
        db.session.commit()
        return existing

    snapshot = NetWorthHistory(user_id=user_id, date=target_date, net_worth_value=curr_balance)
    db.session.add(snapshot)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        snapshot = NetWorthHistory.query.filter_by(user_id=user_id, date=target_date).first()
    return snapshot


def backfill_snapshots_if_needed(user_id: int, days_back: int = 30):
    """
    If a user has missing historical daily snapshots, backfill using current total balance
    and rolling daily net cash flow (credits - debits from transactions).
    Does NOT fabricate numbers — reconstructs only from real past transactions.
    """
    tz = timezone(timedelta(hours=5))
    today = datetime.now(tz).date()
    
    # Get existing snapshot dates
    existing_records = (
        NetWorthHistory.query
        .filter_by(user_id=user_id)
        .order_by(NetWorthHistory.date.desc())
        .all()
    )
    existing_map = {r.date: r for r in existing_records}

    # Ensure today's snapshot exists
    record_daily_snapshot_if_missing(user_id, today)

    # Reconstruct backwards from today's balance
    curr_balance = get_current_total_balance(user_id)

    # Fetch daily net transaction amounts for user
    # net_flow = credit_amt - debit_amt
    daily_txns = (
        db.session.query(
            func.date(Transaction.date).label("tx_date"),
            func.sum(
                case(
                    (Transaction.type == 'credit', Transaction.amount),
                    (Transaction.type == 'debit', -Transaction.amount),
                    else_=0
                )
            ).label("net_flow")
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            exclude_own_account_transfer_sql(),
        )
        .group_by(func.date(Transaction.date))
        .all()
    )

    # Convert to dictionary {date_obj: net_flow_float}
    flow_map = {}
    for r in daily_txns:
        if r.tx_date:
            try:
                if isinstance(r.tx_date, str):
                    d_obj = datetime.strptime(r.tx_date, "%Y-%m-%d").date()
                elif isinstance(r.tx_date, date):
                    d_obj = r.tx_date
                else:
                    d_obj = r.tx_date
                flow_map[d_obj] = float(r.net_flow or 0.0)
            except Exception:
                pass

    # Walk backward from today
    running_balance = curr_balance
    new_snapshots = []

    for i in range(1, days_back + 1):
        prev_date = today - timedelta(days=i)
        # Yesterday's closing balance = today's closing balance - today's net flow
        today_date = today - timedelta(days=i - 1)
        today_flow = flow_map.get(today_date, 0.0)
        running_balance = max(0.0, running_balance - today_flow)

        if prev_date not in existing_map:
            new_snapshots.append(NetWorthHistory(
                user_id=user_id,
                date=prev_date,
                net_worth_value=round(running_balance, 2)
            ))

    if new_snapshots:
        try:
            db.session.bulk_save_objects(new_snapshots)
            db.session.commit()
        except Exception:
            db.session.rollback()


def get_analytics_dashboard_data(user_id: int, period: str = 'month') -> dict:
    """
    Returns complete real analytics dashboard data for a user & period ('week', 'month', 'year', 'all').
    """
    tz = timezone(timedelta(hours=5))
    now = datetime.now(tz)
    today = now.date()

    # Self-heal today's snapshot & backfill history if missing
    record_daily_snapshot_if_missing(user_id, today)
    backfill_snapshots_if_needed(user_id, days_back=60 if period == 'month' else (14 if period == 'week' else 365))

    # 1. Determine Date Boundaries for Current & Previous Periods
    if period == 'week':
        # Selected week: last 7 days including today
        curr_start = today - timedelta(days=6)
        curr_end = today
        prev_start = curr_start - timedelta(days=7)
        prev_end = curr_start - timedelta(days=1)
        period_label = "vs last week"
    elif period == 'year':
        # Selected year: Jan 1 of current year to today
        curr_start = date(today.year, 1, 1)
        curr_end = today
        prev_start = date(today.year - 1, 1, 1)
        prev_end = date(today.year - 1, 12, 31)
        period_label = "vs last year"
    elif period == 'all':
        curr_start = None
        curr_end = None
        prev_start = None
        prev_end = None
        period_label = ""
    else:  # default 'month'
        curr_start = date(today.year, today.month, 1)
        curr_end = today
        # Last month full range
        prev_month_dt = (now - relativedelta(months=1))
        prev_start = date(prev_month_dt.year, prev_month_dt.month, 1)
        # Last day of previous month
        prev_end = curr_start - timedelta(days=1)
        period_label = "vs last month"

    # 2. Query Real Total Spent (debits, excluding transfers)
    def query_spent(start_dt, end_dt):
        q = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.type == 'debit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            exclude_own_account_transfer_sql(),
        )
        if start_dt:
            q = q.filter(func.date(Transaction.date) >= start_dt)
        if end_dt:
            q = q.filter(func.date(Transaction.date) <= end_dt)
        return float(q.scalar() or 0.0)

    total_spent = query_spent(curr_start, curr_end)
    prev_spent = query_spent(prev_start, prev_end) if prev_start else 0.0

    # Calculate real percentage change
    if prev_start and prev_spent > 0:
        spending_change_pct = round(((total_spent - prev_spent) / prev_spent) * 100, 1)
    else:
        spending_change_pct = None  # No valid previous comparison

    # 3. Real Income for current period
    def query_income(start_dt, end_dt):
        q = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.type == 'credit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            exclude_own_account_transfer_sql(),
        )
        if start_dt:
            q = q.filter(func.date(Transaction.date) >= start_dt)
        if end_dt:
            q = q.filter(func.date(Transaction.date) <= end_dt)
        return float(q.scalar() or 0.0)

    total_income = query_income(curr_start, curr_end)

    # 4. Total Current Balance
    total_balance = get_current_total_balance(user_id)

    # 5. Real Historical Balance & Flow Snapshots Graph Series
    graph_series = []

    if period == 'week':
        # 7 daily points (6 days ago up to today)
        daily_txs = (
            db.session.query(
                func.date(Transaction.date).label("tx_date"),
                func.sum(case((Transaction.type == 'credit', Transaction.amount), else_=0)).label("day_income"),
                func.sum(case((Transaction.type == 'debit', Transaction.amount), else_=0)).label("day_expense"),
            )
            .filter(
                Transaction.user_id == user_id,
                func.date(Transaction.date) >= curr_start,
                func.date(Transaction.date) <= curr_end,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(func.date(Transaction.date))
            .all()
        )

        inc_map = {}
        exp_map = {}
        flow_map = {}
        for r in daily_txs:
            if r.tx_date:
                key = r.tx_date if isinstance(r.tx_date, str) else r.tx_date.strftime("%Y-%m-%d")
                day_inc = float(r.day_income or 0.0)
                day_exp = float(r.day_expense or 0.0)
                inc_map[key] = day_inc
                exp_map[key] = day_exp
                flow_map[key] = day_inc - day_exp

        total_flow_in_period = sum(flow_map.values())
        start_balance = max(0.0, total_balance - total_flow_in_period)

        running = start_balance
        for i in range(7):
            d = curr_start + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            day_inc = round(inc_map.get(d_str, 0.0), 2)
            day_exp = round(exp_map.get(d_str, 0.0), 2)
            day_flow = round(day_inc - day_exp, 2)
            running = round(max(0.0, running + day_flow), 2)
            if i == 6:
                running = total_balance

            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%a"),
                "balance": float(running),
                "income": float(day_inc),
                "expenses": float(day_exp),
                "net_cash_flow": float(day_flow),
                "value": float(running)
            })

    elif period == 'month':
        daily_txs = (
            db.session.query(
                func.date(Transaction.date).label("tx_date"),
                func.sum(case((Transaction.type == 'credit', Transaction.amount), else_=0)).label("day_income"),
                func.sum(case((Transaction.type == 'debit', Transaction.amount), else_=0)).label("day_expense"),
            )
            .filter(
                Transaction.user_id == user_id,
                func.date(Transaction.date) >= curr_start,
                func.date(Transaction.date) <= curr_end,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(func.date(Transaction.date))
            .all()
        )

        inc_map = {}
        exp_map = {}
        flow_map = {}
        for r in daily_txs:
            if r.tx_date:
                key = r.tx_date if isinstance(r.tx_date, str) else r.tx_date.strftime("%Y-%m-%d")
                day_inc = float(r.day_income or 0.0)
                day_exp = float(r.day_expense or 0.0)
                inc_map[key] = day_inc
                exp_map[key] = day_exp
                flow_map[key] = day_inc - day_exp

        total_flow_in_period = sum(flow_map.values())
        start_balance = max(0.0, total_balance - total_flow_in_period)

        num_days = (curr_end - curr_start).days + 1
        all_daily = []
        running = start_balance
        for i in range(num_days):
            d = curr_start + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            day_inc = round(inc_map.get(d_str, 0.0), 2)
            day_exp = round(exp_map.get(d_str, 0.0), 2)
            day_flow = round(day_inc - day_exp, 2)
            running = round(max(0.0, running + day_flow), 2)
            if i == num_days - 1:
                running = total_balance
            all_daily.append((d, running, day_inc, day_exp, day_flow))

        step = max(1, (num_days - 1) // 6)
        sampled_indices = list(range(0, num_days, step))
        if (num_days - 1) not in sampled_indices:
            sampled_indices.append(num_days - 1)

        for idx in sampled_indices:
            d, val, day_inc, day_exp, day_flow = all_daily[idx]
            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%b %d"),
                "balance": float(val),
                "income": float(day_inc),
                "expenses": float(day_exp),
                "net_cash_flow": float(day_flow),
                "value": float(val)
            })

    elif period == 'year':
        monthly_txs = (
            db.session.query(
                extract('month', Transaction.date).label("m"),
                func.sum(case((Transaction.type == 'credit', Transaction.amount), else_=0)).label("m_income"),
                func.sum(case((Transaction.type == 'debit', Transaction.amount), else_=0)).label("m_expense"),
            )
            .filter(
                Transaction.user_id == user_id,
                extract('year', Transaction.date) == today.year,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(extract('month', Transaction.date))
            .all()
        )

        month_inc_map = {int(r.m): float(r.m_income or 0.0) for r in monthly_txs if r.m}
        month_exp_map = {int(r.m): float(r.m_expense or 0.0) for r in monthly_txs if r.m}
        month_flow_map = {m: month_inc_map.get(m, 0.0) - month_exp_map.get(m, 0.0) for m in range(1, today.month + 1)}

        active_months = list(range(1, today.month + 1))
        total_flow_yr = sum(month_flow_map.get(m, 0.0) for m in active_months)
        start_balance = max(0.0, total_balance - total_flow_yr)

        running = start_balance
        for m in active_months:
            m_date = date(today.year, m, 1)
            m_inc = round(month_inc_map.get(m, 0.0), 2)
            m_exp = round(month_exp_map.get(m, 0.0), 2)
            m_flow = round(m_inc - m_exp, 2)
            running = round(max(0.0, running + m_flow), 2)
            if m == today.month:
                running = total_balance

            graph_series.append({
                "date": m_date.isoformat(),
                "label": m_date.strftime("%b"),
                "balance": float(running),
                "income": float(m_inc),
                "expenses": float(m_exp),
                "net_cash_flow": float(m_flow),
                "value": float(running)
            })

    else: # 'all'
        earliest_tx = db.session.query(func.min(Transaction.date)).filter(
            Transaction.user_id == user_id,
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
        ).scalar()

        if earliest_tx:
            start_d = earliest_tx.date() if isinstance(earliest_tx, datetime) else earliest_tx
        else:
            start_d = today - timedelta(days=30)

        monthly_txs = (
            db.session.query(
                extract('year', Transaction.date).label("yr"),
                extract('month', Transaction.date).label("m"),
                func.sum(case((Transaction.type == 'credit', Transaction.amount), else_=0)).label("m_income"),
                func.sum(case((Transaction.type == 'debit', Transaction.amount), else_=0)).label("m_expense"),
            )
            .filter(
                Transaction.user_id == user_id,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(extract('year', Transaction.date), extract('month', Transaction.date))
            .all()
        )

        ym_inc_map = {}
        ym_exp_map = {}
        ym_flow_map = {}
        for r in monthly_txs:
            if r.yr and r.m:
                key = (int(r.yr), int(r.m))
                inc_val = float(r.m_income or 0.0)
                exp_val = float(r.m_expense or 0.0)
                ym_inc_map[key] = inc_val
                ym_exp_map[key] = exp_val
                ym_flow_map[key] = inc_val - exp_val

        ym_list = []
        curr_yr, curr_m = start_d.year, start_d.month
        while (curr_yr < today.year) or (curr_yr == today.year and curr_m <= today.month):
            ym_list.append((curr_yr, curr_m))
            curr_m += 1
            if curr_m > 12:
                curr_m = 1
                curr_yr += 1

        total_flow_all = sum(ym_flow_map.get(ym, 0.0) for ym in ym_list)
        start_balance = max(0.0, total_balance - total_flow_all)

        all_monthly_pts = []
        running = start_balance
        for idx, (yr, m) in enumerate(ym_list):
            m_date = date(yr, m, 1)
            inc_val = round(ym_inc_map.get((yr, m), 0.0), 2)
            exp_val = round(ym_exp_map.get((yr, m), 0.0), 2)
            flow_val = round(inc_val - exp_val, 2)
            running = round(max(0.0, running + flow_val), 2)
            if idx == len(ym_list) - 1:
                running = total_balance
            all_monthly_pts.append((m_date, running, inc_val, exp_val, flow_val))

        step = max(1, (len(all_monthly_pts) - 1) // 5) if len(all_monthly_pts) > 1 else 1
        sampled_indices = list(range(0, len(all_monthly_pts), step))
        if (len(all_monthly_pts) - 1) not in sampled_indices:
            sampled_indices.append(len(all_monthly_pts) - 1)

        for idx in sampled_indices:
            d, val, inc_val, exp_val, flow_val = all_monthly_pts[idx]
            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%b '%y"),
                "balance": float(val),
                "income": float(inc_val),
                "expenses": float(exp_val),
                "net_cash_flow": float(flow_val),
                "value": float(val)
            })

    if len(graph_series) == 1:
        prev_d = today - timedelta(days=1)
        graph_series.insert(0, {
            "date": prev_d.isoformat(),
            "label": prev_d.strftime("%a"),
            "balance": graph_series[0]["balance"],
            "income": 0.0,
            "expenses": 0.0,
            "net_cash_flow": 0.0,
            "value": graph_series[0]["value"]
        })

    # 6. Canonical Aggregation for Spending Breakdown with Privacy Protection
    _SELF_TRANSFER_NAMES = {'self transfer', 'self-transfer'}
    base_tx_query = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.type == 'debit',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    )
    if curr_start:
        base_tx_query = base_tx_query.filter(func.date(Transaction.date) >= curr_start)
    if curr_end:
        base_tx_query = base_tx_query.filter(func.date(Transaction.date) <= curr_end)

    txns = base_tx_query.all()

    # Load categories map & user privacy settings
    all_categories = {c.id: c for c in Category.query.all()}
    name_to_cat = {c.name.strip().lower(): c for c in all_categories.values()}

    user_privacy_rows = CategoryPrivacySetting.query.filter_by(user_id=user_id, is_retracted=True).all()
    retracted_category_ids = {r.category_id for r in user_privacy_rows}
    for cat in all_categories.values():
        if getattr(cat, 'is_retracted', False) and (cat.user_id == user_id or cat.user_id is None):
            retracted_category_ids.add(cat.id)

    aggregated = {}

    for t in txns:
        p_str = (t.purpose or "").strip()
        if not p_str or p_str.lower() in _SELF_TRANSFER_NAMES or p_str.lower() == 'uncategorized':
            continue

        cat = None
        if t.category_id and t.category_id in all_categories:
            cat = all_categories[t.category_id]
        elif p_str.lower() in name_to_cat:
            cat = name_to_cat[p_str.lower()]

        cat_id = cat.id if cat else None
        cat_name = cat.name if cat else p_str
        cat_icon = cat.icon if cat else "cash"

        is_retracted = (cat_id in retracted_category_ids) or (cat and getattr(cat, "is_retracted", False))

        if is_retracted:
            group_key = "cat_retracted"
            display_name = "Retracted"
            display_icon = "shield-lock"
            display_color = "#64748B"
        else:
            group_key = f"cat_{cat_id}" if cat_id else f"name_{cat_name.lower()}"
            display_name = cat_name
            display_icon = cat_icon
            display_color = get_canonical_category_color(cat_name, cat_id)

        if group_key not in aggregated:
            aggregated[group_key] = {
                "category_id": None if is_retracted else cat_id,
                "name": display_name,
                "icon": display_icon,
                "color": display_color,
                "amount": 0.0,
                "is_retracted": is_retracted,
                "reasons": {}
            }

        aggregated[group_key]["amount"] += float(t.amount or 0.0)

        # Do NOT expose subcategory details for retracted categories to prevent privacy leaks
        if not is_retracted:
            reason_val = t.bank_reduction_reason or getattr(t, 'shopping_details', None) or getattr(t, 'subscription_details', None)
            if reason_val and str(reason_val).strip():
                r_name = str(reason_val).strip()
                aggregated[group_key]["reasons"][r_name] = aggregated[group_key]["reasons"].get(r_name, 0.0) + float(t.amount or 0.0)

    # Sort aggregated items by total amount descending
    sorted_items = sorted(aggregated.values(), key=lambda x: x["amount"], reverse=True)
    denom = total_spent if total_spent > 0 else (sum(x["amount"] for x in sorted_items) or 1.0)

    breakdown = []
    for item in sorted_items:
        amt = round(item["amount"], 2)
        pct = round((amt / denom) * 100, 1)

        reasons_list = []
        if item["reasons"] and not item.get("is_retracted"):
            total_reason_amt = sum(item["reasons"].values()) or 1.0
            for r_name, r_amt in sorted(item["reasons"].items(), key=lambda x: x[1], reverse=True):
                reasons_list.append({
                    "reason": r_name,
                    "amount": round(r_amt, 2),
                    "percentage": round((r_amt / total_reason_amt) * 100, 1)
                })

        breakdown.append({
            "category_id": item["category_id"],
            "name": item["name"],
            "icon": item["icon"],
            "amount": amt,
            "percentage": pct,
            "color": item["color"],
            "is_retracted": bool(item.get("is_retracted")),
            "reasons": reasons_list
        })

    return {
        "period": period,
        "period_label": period_label,
        "total_spent": total_spent,
        "prev_spent": prev_spent,
        "spending_change_pct": spending_change_pct,
        "total_income": total_income,
        "total_balance": total_balance,
        "graph_metric": "Total Balance",
        "series": graph_series,
        "breakdown": breakdown
    }

