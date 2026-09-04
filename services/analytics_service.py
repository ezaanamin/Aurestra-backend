# services/analytics_service.py — Dynamic, real backend financial analytics service

from datetime import datetime, date, timedelta, timezone
from dateutil.relativedelta import relativedelta
from sqlalchemy import func, extract, desc, case
from database import db
from model import Transaction, AccountBalance, NetWorthHistory, Category, CategoryBucketMapping
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row


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

    # 5. Real Historical Balance Snapshots Graph Series
    # We build the balance progression backwards from today's canonical total balance:
    # balance(t - 1) = balance(t) - net_flow(t)
    graph_series = []

    if period == 'week':
        # 7 daily points (e.g. 6 days ago up to today)
        # Fetch daily net flows for last 7 days
        daily_flows = (
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
                func.date(Transaction.date) >= curr_start,
                func.date(Transaction.date) <= curr_end,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(func.date(Transaction.date))
            .all()
        )

        flow_map = {}
        for r in daily_flows:
            if r.tx_date:
                key = r.tx_date if isinstance(r.tx_date, str) else r.tx_date.strftime("%Y-%m-%d")
                flow_map[key] = float(r.net_flow or 0.0)

        # Build daily points forward from curr_start
        # First find balance at curr_start
        # Sum all net flows from curr_start to today
        total_flow_in_period = sum(flow_map.values())
        start_balance = max(0.0, total_balance - total_flow_in_period)

        running = start_balance
        for i in range(7):
            d = curr_start + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            running = round(max(0.0, running + flow_map.get(d_str, 0.0)), 2)
            # Make sure latest point matches canonical total_balance exactly
            if i == 6:
                running = total_balance

            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%a"), # Mon, Tue, Wed...
                "value": float(running)
            })

    elif period == 'month':
        # Daily points for the month, but with max 7-8 clean date labels to prevent overlap
        daily_flows = (
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
                func.date(Transaction.date) >= curr_start,
                func.date(Transaction.date) <= curr_end,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(func.date(Transaction.date))
            .all()
        )

        flow_map = {}
        for r in daily_flows:
            if r.tx_date:
                key = r.tx_date if isinstance(r.tx_date, str) else r.tx_date.strftime("%Y-%m-%d")
                flow_map[key] = float(r.net_flow or 0.0)

        total_flow_in_period = sum(flow_map.values())
        start_balance = max(0.0, total_balance - total_flow_in_period)

        num_days = (curr_end - curr_start).days + 1
        # Calculate daily balances for every day
        all_daily = []
        running = start_balance
        for i in range(num_days):
            d = curr_start + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            running = round(max(0.0, running + flow_map.get(d_str, 0.0)), 2)
            if i == num_days - 1:
                running = total_balance
            all_daily.append((d, running))

        # Sample 6-8 evenly spaced points so x-axis is clean
        step = max(1, (num_days - 1) // 6)
        sampled_indices = list(range(0, num_days, step))
        if (num_days - 1) not in sampled_indices:
            sampled_indices.append(num_days - 1)

        for idx in sampled_indices:
            d, val = all_daily[idx]
            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%b %d"), # e.g. Sep 1, Sep 5...
                "value": float(val)
            })

    elif period == 'year':
        # 12 monthly data points for current year
        monthly_flows = (
            db.session.query(
                extract('month', Transaction.date).label("m"),
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
                extract('year', Transaction.date) == today.year,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                exclude_own_account_transfer_sql(),
            )
            .group_by(extract('month', Transaction.date))
            .all()
        )

        month_flow_map = {int(r.m): float(r.net_flow or 0.0) for r in monthly_flows if r.m}

        # Calculate sum from Jan to current month
        active_months = list(range(1, today.month + 1))
        total_flow_yr = sum(month_flow_map.get(m, 0.0) for m in active_months)
        start_balance = max(0.0, total_balance - total_flow_yr)

        running = start_balance
        for m in active_months:
            m_date = date(today.year, m, 1)
            running = round(max(0.0, running + month_flow_map.get(m, 0.0)), 2)
            if m == today.month:
                running = total_balance

            graph_series.append({
                "date": m_date.isoformat(),
                "label": m_date.strftime("%b"), # Jan, Feb, Mar...
                "value": float(running)
            })

    else: # 'all'
        # Dynamically aggregate based on date range of transactions & snapshots
        earliest_tx = db.session.query(func.min(Transaction.date)).filter(
            Transaction.user_id == user_id,
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
        ).scalar()

        if earliest_tx:
            start_d = earliest_tx.date() if isinstance(earliest_tx, datetime) else earliest_tx
        else:
            start_d = today - timedelta(days=30)

        # Monthly aggregation across months
        num_months = (today.year - start_d.year) * 12 + (today.month - start_d.month) + 1
        num_months = max(1, num_months)

        # Query net flow per year-month
        monthly_flows = (
            db.session.query(
                extract('year', Transaction.date).label("yr"),
                extract('month', Transaction.date).label("m"),
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
            .group_by(extract('year', Transaction.date), extract('month', Transaction.date))
            .all()
        )

        ym_flow_map = {}
        for r in monthly_flows:
            if r.yr and r.m:
                ym_flow_map[(int(r.yr), int(r.m))] = float(r.net_flow or 0.0)

        # Build list of (yr, m) tuples
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
            running = round(max(0.0, running + ym_flow_map.get((yr, m), 0.0)), 2)
            if idx == len(ym_list) - 1:
                running = total_balance
            all_monthly_pts.append((m_date, running))

        # Sample cleanly if more than 6-7 months to prevent label clutter
        step = max(1, (len(all_monthly_pts) - 1) // 5) if len(all_monthly_pts) > 1 else 1
        sampled_indices = list(range(0, len(all_monthly_pts), step))
        if (len(all_monthly_pts) - 1) not in sampled_indices:
            sampled_indices.append(len(all_monthly_pts) - 1)

        for idx in sampled_indices:
            d, val = all_monthly_pts[idx]
            # Format: Sep '25 | Nov '25...
            graph_series.append({
                "date": d.isoformat(),
                "label": d.strftime("%b '%y"),
                "value": float(val)
            })

    # Ensure graph has at least 2 points for SVG rendering
    if len(graph_series) == 1:
        prev_d = today - timedelta(days=1)
        graph_series.insert(0, {
            "date": prev_d.isoformat(),
            "label": prev_d.strftime("%a"),
            "value": graph_series[0]["value"]
        })

    # 6. Real Spending Breakdown by Category for current period
    cat_query = db.session.query(
        Transaction.purpose.label("category"),
        func.sum(Transaction.amount).label("total")
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'debit',
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        exclude_own_account_transfer_sql(),
    )
    if curr_start:
        cat_query = cat_query.filter(func.date(Transaction.date) >= curr_start)
    if curr_end:
        cat_query = cat_query.filter(func.date(Transaction.date) <= curr_end)

    cat_rows = (
        cat_query
        .group_by(Transaction.purpose)
        .having(func.sum(Transaction.amount) > 0)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(6)
        .all()
    )

    palette = ['#7B5CF5', '#00C9A7', '#F5A623', '#FF6B8A', '#60A5FA', '#4DE8C2']
    breakdown = []
    period_spent_sum = sum(float(r.total or 0.0) for r in cat_rows) or 1.0

    for idx, r in enumerate(cat_rows):
        amt = float(r.total or 0.0)
        pct = round((amt / period_spent_sum) * 100, 1)
        breakdown.append({
            "name": r.category,
            "amount": amt,
            "percentage": pct,
            "color": palette[idx % len(palette)]
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
