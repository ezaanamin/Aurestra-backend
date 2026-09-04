# controllers/notification_controller.py

from datetime import datetime
import jwt as pyjwt
from flask import request, jsonify, current_app
from database import db
from notification_parser import ingest_notification_for_user, list_notifications_for_user
from fcm_utils import send_push_to_all


def ingest(current_user):
    try:
        data   = request.get_json(force=True, silent=True) or {}
        result = ingest_notification_for_user(current_user.id, data)
        code   = 201 if result.get("created") else 200
        return jsonify(result), code
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500


def list_notifications(current_user):
    try:
        limit = request.args.get("limit", 100, type=int)
        items = list_notifications_for_user(current_user.id, limit=limit)
        return jsonify({"notifications": items}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_device(current_user):
    data  = request.get_json() or {}
    token = data.get("token")
    user_id = current_user.id

    if token:
        from model import DeviceToken
        existing = DeviceToken.query.filter_by(token=token).first()
        if existing:
            existing.last_seen = datetime.utcnow()
            existing.user_id = user_id
            db.session.commit()
            return jsonify({"status": "success", "message": "Token updated"}), 200
        else:
            db.session.add(DeviceToken(token=token, user_id=user_id))
            db.session.commit()
            return jsonify({"status": "success", "message": "Token registered"}), 201

    return jsonify({"error": "Token missing"}), 400


def send_test(current_user):
    from model import DeviceToken
    user_tokens = [t.token for t in DeviceToken.query.filter_by(user_id=current_user.id).all()]
    if not user_tokens:
        return jsonify({"status": "no_devices", "message": "No devices registered for this user"}), 200
    send_push_to_all(title="Test FCM", body="Backend test push", tokens=user_tokens)
    return jsonify({"status": "sent"}), 200
