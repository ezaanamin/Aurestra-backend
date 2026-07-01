# controllers/backup_controller.py

from flask import jsonify, request
import services.backup_service as svc


def create_backup(current_user):
    """POST /api/backup/create — create an encrypted backup for the authenticated user."""
    try:
        dec_key = request.headers.get("X-Decryption-Key")
        if not dec_key:
            return jsonify({"error": "X-Decryption-Key header is required."}), 400

        backup = svc.create_user_backup(current_user.id, dec_key)
        return jsonify({
            "message": "Backup created successfully.",
            "backup":  backup.to_dict(),
        }), 201

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def list_backups(current_user):
    """GET /api/backup/list — list metadata of all backups for the authenticated user."""
    try:
        backups = svc.list_user_backups(current_user.id)
        return jsonify({"backups": backups, "count": len(backups)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def latest_backup(current_user):
    """GET /api/backup/latest — return the most recent backup metadata."""
    try:
        backup = svc.get_latest_backup(current_user.id)
        if not backup:
            return jsonify({"backup": None, "message": "No backups found."}), 200
        return jsonify({"backup": backup}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def restore_backup(current_user, backup_id):
    """POST /api/backup/restore/<backup_id> — decrypt and restore a backup."""
    try:
        dec_key = request.headers.get("X-Decryption-Key")
        if not dec_key:
            return jsonify({"error": "X-Decryption-Key header is required."}), 400

        data = request.get_json(silent=True) or {}
        confirmed = data.get("confirmed", False)
        if not confirmed:
            return jsonify({
                "error": "Restore requires explicit confirmation.",
                "hint":  "Send { \"confirmed\": true } in the request body."
            }), 400

        result = svc.restore_user_backup(current_user.id, backup_id, dec_key)
        return jsonify({
            "message": "Restore completed successfully.",
            "result":  result,
        }), 200

    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        # Wrong decryption key or corrupted backup
        return jsonify({"error": str(e), "code": "DECRYPT_FAILED"}), 422
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def delete_backup(current_user, backup_id):
    """DELETE /api/backup/<backup_id> — delete a backup file and its DB record."""
    try:
        svc.delete_user_backup(current_user.id, backup_id)
        return jsonify({"message": "Backup deleted."}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
