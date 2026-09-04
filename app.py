# app.py - refreshed modules
import os
from flask_apscheduler import APScheduler

from database import app, db

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "default_dev_secret_change_me"
)

# SECURITY FIX (MED-5/6): Crash fast if running with insecure default secrets.
# This prevents accidentally deploying with weak/public keys that allow JWT forgery.
_secret_key = os.getenv("SECRET_KEY", "")
_agent_jwt_secret = os.getenv("AGENT_JWT_SECRET", "")
_KNOWN_WEAK_KEYS = {
    "", "default_dev_secret_change_me", "0aefb44af279f5bb0ad9ecce393be138"
}
_is_production = os.getenv("FLASK_ENV", "production") not in ("development", "testing")

if _is_production:
    if _secret_key in _KNOWN_WEAK_KEYS:
        raise RuntimeError(
            "SECURITY ERROR: SECRET_KEY is missing or set to a known-weak default. "
            "Set a strong SECRET_KEY in your .env file before running in production."
        )
    if _agent_jwt_secret in _KNOWN_WEAK_KEYS:
        raise RuntimeError(
            "SECURITY ERROR: AGENT_JWT_SECRET is missing or set to a known-weak default. "
            "Set a strong AGENT_JWT_SECRET in your .env file before running in production."
        )

# ─────────────────────────────────────────────────────────────
# Register Blueprints
# ─────────────────────────────────────────────────────────────
from routes.auth_routes import auth_bp
from routes.account_routes import account_bp
from routes.transaction_routes import transaction_bp
from routes.budget_routes import budget_bp
from routes.savings_routes import savings_bp
from routes.category_routes import category_bp
from routes.report_routes import report_bp
from routes.notification_routes import notification_bp
from routes.system_routes import system_bp
from routes.live_state_routes import live_state_bp
from routes.financial_insight_routes import financial_insight_bp
from ai_agent_api import ai_agent_bp
from financial_api import financial_api_bp
from routes.backup_routes import backup_bp
from routes.chat_routes import chat_bp
from routes.subscription_routes import subscription_bp
from routes.export_routes import export_bp

blueprints = [
    auth_bp,
    account_bp,
    transaction_bp,
    budget_bp,
    savings_bp,
    category_bp,
    report_bp,
    notification_bp,
    system_bp,
    ai_agent_bp,
    financial_api_bp,
    live_state_bp,
    financial_insight_bp,
    backup_bp,
    chat_bp,
    subscription_bp,
    export_bp,
]

for bp in blueprints:
    app.register_blueprint(bp)

# ─────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────
scheduler = APScheduler()

# Tracks whether the midnight backup fully succeeded
_midnight_backup_succeeded = False


def _run_backup(label: str) -> bool:
    """Shared backup runner. Returns True if all destinations succeeded."""
    print(f"⏰ Running {label} backup...")
    try:
        from services.backup.backup_manager import BackupOrchestrator

        with app.app_context():
            orchestrator = BackupOrchestrator(app)
            summary = orchestrator.perform_full_backup()

            sys_ok = all(summary["system"].values())
            users_ok = summary["users"]["failed"] == 0
            
            return sys_ok and users_ok

    except Exception as e:
        print(f"❌ Backup failed: {str(e)}")
        return False



@scheduler.task(
    "cron",
    id="do_midnight_backup",
    hour=0,
    minute=2,
    misfire_grace_time=300,
    max_instances=1,
    timezone="Asia/Karachi",
)
def do_midnight_backup_job():
    global _midnight_backup_succeeded
    _midnight_backup_succeeded = _run_backup("midnight")


@scheduler.task(
    "cron",
    id="do_8am_retry_backup",
    hour=8,
    minute=0,
    misfire_grace_time=300,
    max_instances=1,
    timezone="Asia/Karachi",
)
def do_8am_retry_backup_job():
    global _midnight_backup_succeeded
    if _midnight_backup_succeeded:
        print("⏰ [8AM] Midnight backup succeeded, skipping retry.")
        return
    print("⏰ [8AM] Running scheduled retry backup...")
    _run_backup("8AM")


@scheduler.task(
    "cron",
    id="generate_monthly_summary",
    day="1",
    hour=0,
    minute=5,
    misfire_grace_time=3600,
    timezone="Asia/Karachi",
)
def scheduled_monthly_summary():
    import datetime
    from dateutil.relativedelta import relativedelta

    tz = datetime.timezone(datetime.timedelta(hours=5))
    now = datetime.datetime.now(tz)
    
    # Calculate completed month (e.g. on Oct 1st -> process September)
    first_of_this_month = datetime.datetime(now.year, now.month, 1, tzinfo=tz)
    completed_month_dt = first_of_this_month - relativedelta(months=1)
    target_month_str = completed_month_dt.strftime('%Y-%m')

    print(f"⏰ [AI_INSIGHT] Scheduled monthly insight job started at {now.isoformat()} for target completed month: {target_month_str}")

    from services.rag_service import generate_monthly_rag_summary
    from model import User

    with app.app_context():
        users = User.query.all()
        processed = 0
        failed = 0

        for u in users:
            try:
                print(f"⏰ [AI_INSIGHT] Processing user: {u.id} for month: {target_month_str}")
                res = generate_monthly_rag_summary(u.id, target_month_str)
                if res is not None:
                    print(f"✅ [AI_INSIGHT] Insight generated & saved for user: {u.id}")
                else:
                    print(f"ℹ️ [AI_INSIGHT] Insight skipped/handled for user: {u.id}")
                processed += 1
            except Exception as e:
                import traceback
                failed += 1
                print(f"❌ [AI_INSIGHT] Failed processing user: {u.id} ({e})\n{traceback.format_exc()}")

        print(f"⏰ [AI_INSIGHT] Monthly job completed: {processed} succeeded/handled, {failed} failed out of {len(users)} users.")


# @scheduler.task(
#     "interval",
#     id="generate_monthly_summary_test",
#     minutes=2,  # DEV ONLY — do not enable in production
#     misfire_grace_time=300,
# )
# def test_monthly_summary():
#     print("⏰ [TEST INTERVAL] Running TEST RAG summary...")
#     from services.rag_service import generate_monthly_rag_summary
#     with app.app_context():
#         import datetime
#         now = datetime.datetime.now(datetime.timezone.utc)
#         month_str = now.strftime('%Y-%m')
#         generate_monthly_rag_summary(month_str)


@scheduler.task(
    "cron",
    id="capture_daily_net_worth",
    hour=23,
    minute=55,
    misfire_grace_time=3600,
    timezone="Asia/Karachi",
)
def scheduled_daily_net_worth():
    import datetime
    import logging
    
    print(f"⏰ [CRON] net_worth_snapshot job started at {datetime.datetime.now().isoformat()}")
    with app.app_context():
        from model import User, AccountBalance, NetWorthHistory
        from database import db
        
        # Use server local date based on timezone (Asia/Karachi)
        tz = datetime.timezone(datetime.timedelta(hours=5))
        today = datetime.datetime.now(tz).date()
        
        users = User.query.all()
        processed = 0
        for u in users:
            try:
                balances = AccountBalance.query.filter_by(user_id=u.id).all()
                total_nw = sum(b.current_balance for b in balances)
                
                record = NetWorthHistory.query.filter_by(user_id=u.id, date=today).first()
                if record:
                    record.net_worth_value = total_nw
                else:
                    record = NetWorthHistory(user_id=u.id, date=today, net_worth_value=total_nw)
                    db.session.add(record)
                db.session.commit()
                processed += 1
            except Exception as e:
                import traceback
                db.session.rollback()
                print(f"❌ net_worth_snapshot failed for user {u.id}: {repr(e)}\n{traceback.format_exc()}")
        
        print(f"⏰ [CRON] net_worth_snapshot complete: {processed}/{len(users)} users processed successfully.")

scheduler.init_app(app)

# Print backup password on startup for decryption reference
import os as _os
_backup_pw = _os.getenv("BACKUP_PASSWORD", "default_secure_password")
print(f"🔑 [Backup] Password: {_backup_pw}")

# Prevent duplicate scheduler in Flask debug mode.
# In debug mode Flask spawns a reloader parent + a worker child process.
# We only want the scheduler running in the worker (WERKZEUG_RUN_MAIN=true).
# When running normally (production/gunicorn) app.debug is False so it always starts.
_is_reloader_parent = app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
if not _is_reloader_parent:
    scheduler.start()

# ─────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    # Ensure newly added columns exist in existing SQLite database tables
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(categories);")).fetchall()]
            if 'icon_type' not in cols:
                conn.execute(text("ALTER TABLE categories ADD COLUMN icon_type VARCHAR(20) DEFAULT 'library' NOT NULL;"))
            if 'custom_icon_url' not in cols:
                conn.execute(text("ALTER TABLE categories ADD COLUMN custom_icon_url VARCHAR(255);"))
            tx_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(transactions);")).fetchall()]
            if 'bank_reduction_reason' not in tx_cols:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN bank_reduction_reason VARCHAR(255);"))
            acc_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(account_balances);")).fetchall()]
            if 'is_deleted' not in acc_cols:
                conn.execute(text("ALTER TABLE account_balances ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))
            user_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(users);")).fetchall()]
            if 'ai_feed' not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN ai_feed BOOLEAN DEFAULT 1;"))
            conn.commit()
    except Exception as _e:
        print(f"⚠️ Table migration check: {_e}")

# ─────────────────────────────────────────────────────────────
# Run Application
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    with app.app_context():
        from decorator.helpers import seed_categories
        from services.subscription_service import seed_plans
        from migrate_bank_reduction import migrate_bank_reduction_data

        seed_categories()
        seed_plans()
        migrate_bank_reduction_data()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )