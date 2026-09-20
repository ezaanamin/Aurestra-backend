import os
import hmac
from functools import wraps
from flask import request, jsonify
from model import User

def assistant_token_required(f):
    """
    Static Bearer token authentication specifically for Elyestra Desktop Assistant.
    Used ONLY on /api/assistant routes. Fails closed (401) if env vars are unset.
    Zero DB queries on missing or invalid tokens.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        expected_token = os.getenv("ASSISTANT_API_TOKEN", "").strip()
        user_email = os.getenv("ASSISTANT_USER_EMAIL", "").strip()

        # Fails closed if env vars are not configured
        if not expected_token or not user_email:
            return jsonify({"error": "Unauthorized"}), 401

        auth_header = request.headers.get("Authorization", "")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        token = auth_header[7:].strip()
        if not token:
            return jsonify({"error": "Unauthorized"}), 401

        # Constant-time comparison using bytes to prevent timing attacks and handle odd characters
        try:
            is_valid = hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))
        except Exception:
            return jsonify({"error": "Unauthorized"}), 401

        if not is_valid:
            return jsonify({"error": "Unauthorized"}), 401

        # Scope exclusively to the user matching ASSISTANT_USER_EMAIL
        current_user = User.query.filter_by(email=user_email).first()
        if not current_user:
            return jsonify({"error": "Unauthorized"}), 401

        return f(current_user, *args, **kwargs)
    return decorated

