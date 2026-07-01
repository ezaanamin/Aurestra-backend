# controllers/system_controller.py

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


def push_diagnostics():
    try:
        from fcm_utils import get_push_service_diagnostics
        return jsonify({"success": True, "diagnostics": get_push_service_diagnostics()}), 200
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


def debug_push_status(current_user):
    from fcm_utils import get_push_service_diagnostics
    return jsonify(get_push_service_diagnostics()), 200


def manual_backup(current_user):
    """POST /api/system/backup — trigger an immediate system backup."""
    if not current_user.email == os.getenv("BANK_EMAIL_ACCOUNT"):
        return jsonify({"message": "Forbidden. Admin access required."}), 403

    try:
        from services.backup.backup_manager import BackupOrchestrator
        from flask import current_app

        orchestrator = BackupOrchestrator(current_app)
        # We can run it asynchronously or synchronously; here we run it synchronously.
        results = orchestrator.perform_full_backup()

        return jsonify({
            "message": "Backup executed successfully.",
            "results": results
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def list_api_insights(current_user):
    """Returns API endpoint documentation for the LIVE_STATE service."""
    try:
        endpoints = []
        for rule in current_app.url_map.iter_rules():
            if rule.endpoint.startswith("live_state."):
                func = current_app.view_functions[rule.endpoint]
                endpoints.append({
                    "id":          str(rule),
                    "endpoint":    str(rule),
                    "methods":     [m for m in rule.methods if m not in ("HEAD", "OPTIONS")],
                    "description": (func.__doc__ or "No description available.").strip(),
                })
        return jsonify(endpoints), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def test_route():
    return jsonify({"status": "ok", "message": "Backend is deployed and running"}), 200


def home():
    db_uri  = current_app.config.get('SQLALCHEMY_DATABASE_URI', 'Unknown')
    safe    = re.sub(r':([^@]+)@', ':****@', db_uri)
    return f"Backend Running ✅ (DB: {safe})"
