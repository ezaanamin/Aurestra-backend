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
        from backup_manager import BackupManager

        with app.app_context():
            # 1. Run system-wide backup (using primary user key as password)
            bm = BackupManager()
            bm.init_app(app)
            results = bm.perform_backup()

            success_count = sum(
                1 for result in results.values() if result is True
            )
            total = len(results)

            print(
                f"✅ System backup complete: "
                f"{success_count}/{total} destinations succeeded."
            )

            # 2. Run per-user backups
            from model import User
            from services.backup_service import create_user_backup
            users = User.query.filter(User.decryption_key.isnot(None)).all()
            print(f"⏰ Generating per-user encrypted backups for {len(users)} users...")
            for u in users:
                try:
                    create_user_backup(u.id, u.decryption_key)
                    print(f"✅ Auto per-user backup created for {u.email}")
                except Exception as ex:
                    print(f"❌ Failed auto per-user backup for {u.email}: {ex}")

            return success_count == total

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
    hour=9,
    minute=40,
    misfire_grace_time=300,
    max_instances=1,
    timezone="Asia/Karachi",
)
def do_8am_retry_backup_job():
    print("⏰ [8AM] Running scheduled backup...")
    _run_backup("8AM")


@scheduler.task(
    "cron",
    id="generate_monthly_summary",
    day="last",
    hour=23,
    minute=50,
    misfire_grace_time=3600,
    timezone="Asia/Karachi",
)
def scheduled_monthly_summary():
    print("⏰ [CRON] Running end-of-month RAG summary for all users...")
    from services.rag_service import generate_monthly_rag_summary
    from model import User
    with app.app_context():
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
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

        seed_categories()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )