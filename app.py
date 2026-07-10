# app.py
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
from routes.sms_routes import sms_bp
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

blueprints = [
    auth_bp,
    account_bp,
    transaction_bp,
    budget_bp,
    savings_bp,
    sms_bp,
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
    day="*",
    hour=23,
    minute=50,
    misfire_grace_time=3600,
    timezone="Asia/Karachi",
)
def scheduled_monthly_summary():
    import datetime
    from calendar import monthrange
    
    # Check if today is the last day of the month in Asia/Karachi (UTC+5)
    tz = datetime.timezone(datetime.timedelta(hours=5))
    now = datetime.datetime.now(tz)
    _, last_day = monthrange(now.year, now.month)
    
    if now.day != last_day:
        return

    print("⏰ [CRON] Running end-of-month RAG summary for all users...")
    from services.rag_service import generate_monthly_rag_summary
    from model import User
    with app.app_context():
        month_str = now.strftime('%Y-%m')
        users = User.query.all()
        for u in users:
            try:
                generate_monthly_rag_summary(u.id, month_str)
                print(f"✅ Generated summary for user {u.email}")
            except Exception as e:
                print(f"❌ Failed to generate summary for user {u.email}: {e}")


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
# Database Initialization
# ─────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()

# ─────────────────────────────────────────────────────────────
# Run Application
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    with app.app_context():
        from decorator.helpers import seed_categories
        from services.subscription_service import seed_plans

        seed_categories()
        seed_plans()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )