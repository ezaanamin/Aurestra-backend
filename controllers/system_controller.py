# controllers/system_controller.py

import os
import re
from datetime import datetime
from flask import jsonify, current_app
from database import db
from sqlalchemy import func, inspect


def health_check():
    return jsonify({
        "status":    "online",
        "message":   "Backend is running",
        "timestamp": datetime.now().isoformat(),
    }), 200


def db_health_check():
    try:
        db.session.execute(func.now()).scalar()
        inspector      = inspect(db.engine)
        existing       = inspector.get_table_names()
        required       = ['users', 'transactions', 'categories', 'budgets']
        missing        = [t for t in required if t not in existing]
        status         = "healthy" if not missing else "degraded"
        return jsonify({
            "status":                   status,
            "database":                 "connected",
            "tables_found":             len(existing),
            "missing_required_tables":  missing,
            "all_tables":               existing,
        }), 200 if status == "healthy" else 503
    except Exception as e:
        return jsonify({"status": "offline", "error": str(e)}), 500


def debug_push_status(current_user):
    from fcm_utils import get_push_service_diagnostics
    return jsonify(get_push_service_diagnostics()), 200


def manual_backup(current_user):
    """POST /api/system/backup — trigger an immediate system backup."""
    if not current_user.email == os.getenv("BANK_EMAIL_ACCOUNT"):
        return jsonify({"message": "Forbidden. Admin access required."}), 403

    try:
        from services.backup.backup_manager import BackupOrchestrator

        orchestrator = BackupOrchestrator(current_app)
        # We can run it asynchronously or synchronously; here we run it synchronously.
        results = orchestrator.perform_full_backup()

        return jsonify({
            "message": "Backup executed successfully.",
            "results": results
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def trigger_backup(current_user):
    """POST /api/backup/trigger — compatibility entrypoint for backup triggering."""
    return manual_backup(current_user)


def test_route():
    return jsonify({"status": "ok", "message": "Backend is deployed and running"}), 200


def home():
    db_uri  = current_app.config.get('SQLALCHEMY_DATABASE_URI', 'Unknown')
    safe    = re.sub(r':([^@]+)@', ':****@', db_uri)
    return f"Backend Running ✅ (DB: {safe})"
