"""Auto-split from legacy app.py — devices controller."""
from flask import Blueprint, jsonify, request, current_app
from functools import wraps
import os
import json
import re
import hashlib
import secrets
import random
import base64
import threading
from collections import defaultdict
from datetime import datetime, date, timedelta
from time import time

import jwt
import requests as http_requests
from dateutil.relativedelta import relativedelta
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc, extract, case, text
from sqlalchemy.exc import IntegrityError
from google.oauth2 import id_token
from google.auth.transport import requests
from google_auth_oauthlib.flow import Flow
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.extensions import db
from app.middleware.auth import token_required
from app.models import (
    MonthlyBalance, Transaction, Budget, AccountBalance, SavingsGoal,
    Category, SMSHistory, FinancialInsight, User, DeviceToken,
    StatementAnalysis, CategorizationRule,
)
from fetchers import fetch_latest_bank_email, calculate_combined_summary
from drive_utils import (
    get_drive_service, ensure_folder_path, upload_json,
    get_gmail_service, create_message, send_gmail_message, GOOGLE_SIGNIN_OAUTH_SCOPES,
)
from fcm_utils import send_push_to_all, get_push_service_diagnostics
from sms_parser import process_bank_sms, BankAlhabibSMSParser, generate_sms_hash, generate_transaction_hash
from account_matching import match_account_for_notification
from notification_parser import ingest_notification_for_user, list_notifications_for_user
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from ledger_sync import apply_pending_transaction_ledger, ensure_account_balance_row, log_wallet_attribution

AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://auth.elyestra.org').rstrip('/')
AUTH_API_OWNER_EMAIL = os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')

devices_bp = Blueprint("devices", __name__)

# -------------------------
# DEVICE & NOTIFICATIONS
# -------------------------

@devices_bp.route("/api/register-device", methods=["POST"])
def register_device():
    data = request.get_json()
    token = data.get("token")
    auth_header = request.headers.get('Authorization')
    user_id = None

    if auth_header and auth_header.startswith("Bearer "):
        try:
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(jwt_token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
            user_id = payload.get('user_id')
        except:
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
            new_token = DeviceToken(token=token, user_id=user_id)
            db.session.add(new_token)
            db.session.commit()
            print("📥 Registered persistent device token:", token)
            return {"status": "success", "message": "Token registered"}, 201

    return {"error": "Token missing"}, 400

@devices_bp.route("/api/easypaisa/latest", methods=["GET", "POST"])
def easypaisa_latest():
    result = fetch_and_save_easypaisa_emails()
    
    if result.get("count", 0) > 0:
        title = "New Easypaisa Transactions"
        body = f"You have {result['count']} new transaction(s) saved."
        try:
            send_push_to_all(title, body)
            print("✅ Push notification sent.")
        except Exception as e:
            print(f"❌ Failed to send push: {e}")
    
    return jsonify(result)

@devices_bp.route("/api/debug/push-status", methods=["GET"])
@token_required
def debug_push_status(current_user):
    """Diagnose FCM on the server (credentials, project id, token count). Auth required."""
    return jsonify(get_push_service_diagnostics()), 200


@devices_bp.route("/api/send-test", methods=["POST"])
def send_test_push():
    send_push_to_all(
        title="Test FCM",
        body="Backend test push"
    )
    return {"status": "sent"}, 200

