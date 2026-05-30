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


def register_device():
    data  = request.get_json() or {}
    token = data.get("token")

    user_id = None
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith("Bearer "):
        try:
            payload = pyjwt.decode(
                auth_header.split(" ")[1],
                current_app.config['SECRET_KEY'],
                algorithms=["HS256"],
            )
            user_id = payload.get('user_id')
        except Exception:
            pass

    if token:
        from model import DeviceToken
        existing = DeviceToken.query.filter_by(token=token).first()
        if existing:
            existing.last_seen = datetime.utcnow()
            if user_id:
                existing.user_id = user_id
            db.session.commit()
            return {"status": "success", "message": "Token updated"}, 200
        else:
            db.session.add(DeviceToken(token=token, user_id=user_id))
            db.session.commit()
            return {"status": "success", "message": "Token registered"}, 201

    return {"error": "Token missing"}, 400


def send_test():
    send_push_to_all(title="Test FCM", body="Backend test push")
    return {"status": "sent"}, 200
