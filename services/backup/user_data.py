import datetime
from database import db
from model import (
    Transaction, Category, AccountBalance,
    Budget, MonthlyBalance, FinancialInsight, StatementAnalysis,
    UploadedReceipt, DeviceNotification, SavingsGoal,
    CategorizationRule, User,
)

# ── Data export ──────────────────────────────────────────────────────────────
def _serialize_row(obj) -> dict:
    """Safe serializer for any SQLAlchemy model row."""
    out = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name, None)
        if hasattr(val, 'isoformat'):
            val = val.isoformat()
        out[col.name] = val
    return out

def export_user_data(user_id: int, app_version: str, db_version: int) -> dict:
    """Export all user-owned rows as serializable dicts."""
    def _rows_raw(query):
        return [_serialize_row(r) for r in query]

    def _safe(name, fn):
        try:
            return fn()
        except Exception as e:
            print(f"[Backup Export] Warning: skipping table '{name}' export: {e}")
            return []

    u = User.query.get(user_id)
    prefs = {
        "notifications_enabled": u.notifications_enabled if u else True
    }

    return {
        "version":     app_version,
        "user_id":     user_id,
        "exported_at": datetime.datetime.utcnow().isoformat(),
        "app_version": app_version,
        "db_version":  db_version,
        "preferences": prefs,
        "tables": {
            "transactions":         _safe("transactions",         lambda: _rows_raw(Transaction.query.filter_by(user_id=user_id).all())),
            "categories":           _safe("categories",           lambda: _rows_raw(Category.query.filter_by(user_id=user_id).all())),
            "account_balances":     _safe("account_balances",     lambda: _rows_raw(AccountBalance.query.filter_by(user_id=user_id).all())),
            "budgets":              _safe("budgets",              lambda: _rows_raw(Budget.query.filter_by(user_id=user_id).all())),
            "monthly_balances":     _safe("monthly_balances",     lambda: _rows_raw(MonthlyBalance.query.filter_by(user_id=user_id).all())),
            "financial_insights":   _safe("financial_insights",   lambda: _rows_raw(FinancialInsight.query.filter_by(user_id=user_id).all())),
            "statement_analysis":   _safe("statement_analysis",   lambda: _rows_raw(StatementAnalysis.query.filter_by(user_id=user_id).all())),
            "uploaded_receipts":    _safe("uploaded_receipts",    lambda: _rows_raw(UploadedReceipt.query.filter_by(user_id=user_id).all())),
            "device_notifications": _safe("device_notifications", lambda: _rows_raw(DeviceNotification.query.filter_by(user_id=user_id).all())),
            "savings_goals":        _safe("savings_goals",        lambda: _rows_raw(SavingsGoal.query.filter_by(user_id=user_id).all())),
            "categorization_rules": _safe("categorization_rules", lambda: _rows_raw(CategorizationRule.query.filter_by(user_id=user_id).all())),
        }
    }

def count_tables(data: dict) -> dict:
    return {k: len(v) for k, v in data.get("tables", {}).items()}


def get_latest_dates(data: dict) -> dict:
    """Computes the latest (max) date for each table in the exported data."""
    latest_dates = {}
    tables = data.get("tables", {})
    date_fields = {
        "transactions": "date",
        "account_balances": "last_updated",
        "budgets": "created_at",
        "monthly_balances": "fetched_at",
        "financial_insights": "created_at",
        "statement_analysis": "analysis_date",
        "uploaded_receipts": "created_at",
        "device_notifications": "created_at",
        "savings_goals": "created_at",
        "categorization_rules": "created_at",
    }
    for table_name, rows in tables.items():
        if not rows:
            continue
        field = date_fields.get(table_name)
        if not field:
            continue
        vals = []
        for r in rows:
            val = r.get(field)
            if val:
                vals.append(val)
        if vals:
            latest_dates[table_name] = max(vals)
    return latest_dates



# ── Data import (restore) ────────────────────────────────────────────────────
def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.datetime.fromisoformat(str(val))
    except Exception:
        return None

def _restore_table_generic(model_class, user_id: int, rows: list) -> int:
    from sqlalchemy.types import DateTime, Date
    count = 0
    for r in rows:
        if r.get("user_id") != user_id:
            continue
        obj_data = {}
        for col in model_class.__table__.columns:
            val = r.get(col.name)
            if val is not None and isinstance(col.type, (DateTime, Date)):
                parsed = _parse_dt(val)
                if parsed and isinstance(col.type, Date):
                    val = parsed.date()
                else:
                    val = parsed
            obj_data[col.name] = val
        obj = model_class(**obj_data)
        db.session.merge(obj)
        count += 1
    return count

def restore_user_data(user_id: int, data: dict) -> dict:
    """Restores data dictionary back into the SQLite Database."""
    restored = {}

    # Restore preferences
    prefs = data.get("preferences", {})
    u = User.query.get(user_id)
    if u and "notifications_enabled" in prefs:
        u.notifications_enabled = bool(prefs["notifications_enabled"])

    # Specialized logic for Transactions
    count_txn = 0
    for r in data["tables"].get("transactions", []):
        if r.get("is_deleted"):
            continue
        existing = Transaction.query.filter_by(id=r["id"], user_id=user_id).first()
        if existing:
            existing.purpose   = r.get("purpose")
            existing.sender    = r.get("sender")
            existing.receiver  = r.get("receiver")
            existing.notes     = r.get("notes")
            existing.amount    = r.get("amount")
            existing.type      = r.get("type")
            existing.category_id = r.get("category_id")
            existing.categorization_status = r.get("categorization_status", "pending")
        else:
            txn = Transaction(
                id=r["id"], user_id=user_id,
                source=r.get("source", "backup"),
                date=_parse_dt(r.get("date")),
                amount=r.get("amount", 0),
                type=r.get("type", "debit"),
                purpose=r.get("purpose"),
                sender=r.get("sender"),
                receiver=r.get("receiver"),
                transaction_id=r.get("transaction_id"),
                transaction_hash=r.get("transaction_hash"),
                sms_hash=r.get("sms_hash"),
                notes=r.get("notes"),
                categorization_status=r.get("categorization_status", "pending"),
                category_id=r.get("category_id"),
                account_balance_source=r.get("account_balance_source"),
                balance_applied=r.get("balance_applied", True),
                is_deleted=False,
                is_spam=r.get("is_spam", False),
            )
            db.session.merge(txn)
        count_txn += 1
    restored["transactions"] = count_txn

    # Specialized logic for Categories
    count_cat = 0
    for r in data["tables"].get("categories", []):
        existing = Category.query.filter_by(id=r["id"], user_id=user_id).first()
        if not existing:
            cat = Category(
                id=r["id"], user_id=user_id,
                name=r.get("name", "Unknown"),
                icon=r.get("icon"),
                color=r.get("color"),
                is_default=r.get("is_default", False),
            )
            db.session.merge(cat)
        count_cat += 1
    restored["categories"] = count_cat

    # Specialized logic for Account Balances
    count_ab = 0
    for r in data["tables"].get("account_balances", []):
        existing = AccountBalance.query.filter_by(source=r["source"], user_id=user_id).first()
        if existing:
            existing.current_balance = r.get("current_balance", existing.current_balance)
        else:
            ab = AccountBalance(
                id=r["id"], user_id=user_id,
                source=r["source"],
                display_name=r.get("display_name"),
                current_balance=r.get("current_balance", 0.0),
            )
            db.session.merge(ab)
        count_ab += 1
    restored["account_balances"] = count_ab

    # Specialized logic for Budgets
    count_b = 0
    for r in data["tables"].get("budgets", []):
        existing = Budget.query.filter_by(user_id=user_id).first()
        if not existing:
            b = Budget(
                id=r.get("id"), user_id=user_id,
                income=r.get("income", 0.0),
                savings_goal=r.get("savings_goal", 0.0),
                total_budget=r.get("total_budget", 0.0),
            )
            db.session.merge(b)
        count_b += 1
    restored["budgets"] = count_b

    # Specialized logic for Insights
    count_ins = 0
    import json
    for r in data["tables"].get("financial_insights", []):
        existing = FinancialInsight.query.filter_by(user_id=user_id, month=r["month"]).first()
        if not existing:
            fi = FinancialInsight(
                id=r.get("id"), user_id=user_id,
                month=r["month"],
                content=r.get("content", ""),
                metrics_json=json.dumps(r.get("metrics", {})),
                tags=r.get("tags"),
            )
            db.session.merge(fi)
        count_ins += 1
    restored["financial_insights"] = count_ins

    restored["monthly_balances"] = _restore_table_generic(MonthlyBalance, user_id, data["tables"].get("monthly_balances", []))
    restored["statement_analysis"] = _restore_table_generic(StatementAnalysis, user_id, data["tables"].get("statement_analysis", []))
    restored["uploaded_receipts"] = _restore_table_generic(UploadedReceipt, user_id, data["tables"].get("uploaded_receipts", []))
    restored["device_notifications"] = _restore_table_generic(DeviceNotification, user_id, data["tables"].get("device_notifications", []))
    restored["savings_goals"] = _restore_table_generic(SavingsGoal, user_id, data["tables"].get("savings_goals", []))
    restored["categorization_rules"] = _restore_table_generic(CategorizationRule, user_id, data["tables"].get("categorization_rules", []))

    db.session.commit()
    return restored
