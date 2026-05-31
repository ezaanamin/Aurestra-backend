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
from ai_agent_api import ai_agent_bp
from financial_api import financial_api_bp

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
            bm = BackupManager()
            bm.init_app(app)

            results = bm.perform_backup()

            success_count = sum(
                1 for result in results.values() if result is True
            )
            total = len(results)

            print(
                f"✅ Backup complete: "
                f"{success_count}/{total} destinations succeeded."
            )
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
)
def do_8am_retry_backup_job():
    global _midnight_backup_succeeded
    if _midnight_backup_succeeded:
        print("⏭️  [8AM Retry] Midnight backup was successful — skipping retry.")
        return
    print("⚠️  [8AM Retry] Midnight backup failed or didn't run — retrying now...")
    _midnight_backup_succeeded = _run_backup("8AM retry")


scheduler.init_app(app)

# Prevent duplicate scheduler in Flask debug mode
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
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
        debug=True,
    )