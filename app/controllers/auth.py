"""Auto-split from legacy app.py — auth controller."""
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

AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://9938-119-73-101-66.ngrok-free.app').rstrip('/')
AUTH_API_OWNER_EMAIL = os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')

auth_bp = Blueprint("auth", __name__)

# -------------------------
# AUTH ROUTES
# -------------------------

@auth_bp.route("/api/google/login", methods=["POST"])
def google_login():
    data = request.get_json()
    id_token_str = data.get('idToken')
    
    if not id_token_str:
        return jsonify({'message': 'Missing ID token'}), 400

    try:
        # 1. Verify ID Token locally to get identity
        id_info = id_token.verify_oauth2_token(
            id_token_str, 
            requests.Request(), 
            os.getenv('GOOGLE_WEB_CLIENT_ID')
        )

        email = id_info.get('email')
        google_id = id_info.get('sub')
        name = id_info.get('name')
        picture = id_info.get('picture')
        
        # 2. Sync with Centralized Auth Service
        auth_url = os.getenv("AUTH_SERVICE_URL")
        centralized_auth = False
        try:
            # Check if this user is already authenticated in the centralized system
            import requests as py_requests
            auth_status_res = py_requests.get(f"{auth_url}/auth/check", params={"email": email}, timeout=5)
            if auth_status_res.status_code == 200:
                centralized_auth = auth_status_res.json().get("authenticated", False)
        except Exception as e:
            print(f"⚠️ [AUTH] Could not reach Centralized Auth Service at {auth_url}: {e}")

        # 3. Create or Update Local User
        user = User.query.filter_by(email=email).first()
        
        if not user:
            user = User(
                email=email,
                full_name=name,
                google_id=google_id,
                google_email=email,
                avatar_url=picture
            )
            db.session.add(user)
            db.session.commit()
        else:
            if not user.google_id:
                user.google_id = google_id
                user.google_email = email
            if picture:
                user.avatar_url = picture
            if name and not user.full_name:
                user.full_name = name
            db.session.commit()
            
        exp_ts = id_info.get('exp')
        exp_date = datetime.utcfromtimestamp(exp_ts) if exp_ts else datetime.utcnow() + timedelta(hours=2)
            
        token = jwt.encode({
            'user_id': user.id,
            'email': user.email,
            'exp': exp_date
        }, current_app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': user.to_dict(),
            'email': email,
            'centralized_auth': centralized_auth,
            'auth_service_url': auth_url if not centralized_auth else None
        }), 200

    except ValueError as e:
        return jsonify({'message': f'Invalid token: {str(e)}'}), 401
    except Exception as e:
        print(f"Google Login Error: {e}")
        return jsonify({'message': 'Internal server error'}), 500

@auth_bp.route("/api/auth/status", methods=["GET"])
@token_required
def get_auth_status(current_user):
    """Checks the status of the centralized Auth Service for the current user."""
    try:
        res = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": current_user.email}, timeout=8)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"authenticated": False, "error": str(e)}), 503


@auth_bp.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    """
    Primary login endpoint for WebView-based centralized auth flow.
    The mobile app sends the email captured from Auth-api WebView login.
    We verify it against Auth-api, then issue an OTP for 2FA.
    """
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'message': 'Email is required'}), 400

    # 1. Verify user is authenticated in the centralized Auth-api
    try:
        check_res = http_requests.get(
            f"{AUTH_API_URL}/auth/check",
            params={"email": email},
            timeout=10,
        )
        check_data = check_res.json()
    except Exception as e:
        print(f"⚠️ [AUTH VERIFY] Cannot reach Auth-api: {e}")
        return jsonify({'message': 'Auth service unreachable. Please try again.'}), 503

    if not check_data.get("authenticated"):
        reason = check_data.get("reason", "unknown")
        print(f"⚠️ [AUTH VERIFY] Auth-api says not authenticated for {email}: {reason}")
        return jsonify({
            'message': 'Not authenticated with Google. Please sign in first.',
            'reason': reason,
            'action': 'login_required',
            'auth_url': f"{AUTH_API_URL}/auth/google/login",
        }), 401

    # 2. Find or create local Aurestra user
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, google_email=email)
        db.session.add(user)
        db.session.commit()
        print(f"✅ [AUTH VERIFY] Created new Aurestra user: {email}")
    else:
        if not user.google_email:
            user.google_email = email
            db.session.commit()

    id_token_str = data.get('idToken')
    
    exp_date = datetime.utcnow() + timedelta(hours=2)
    if id_token_str:
        try:
            decoded = jwt.decode(id_token_str, options={"verify_signature": False})
            exp_ts = decoded.get('exp')
            if exp_ts:
                exp_date = datetime.utcfromtimestamp(exp_ts)
        except Exception as e:
            print(f"⚠️ Could not parse idToken for exp: {e}")

    # 3. Generate JWT
    token = jwt.encode({
        'user_id': user.id,
        'email': user.email,
        'exp': exp_date
    }, current_app.config['SECRET_KEY'], algorithm="HS256")

    return jsonify({
        'message': 'Login successful',
        'token': token,
        'user': user.to_dict(),
        'email': email,
    }), 200


@auth_bp.route("/api/profile", methods=["GET", "POST"])
@token_required
def profile(current_user):
    if request.method == "GET":
        with current_app.app_context():
            # Calculate stats
            tx_count = Transaction.query.filter_by(is_deleted=False).count()
            goals_count = SavingsGoal.query.count()
            # Count actual defined categories, not just those used in transactions
            categories_count = Category.query.count()
            
            user_data = current_user.to_dict()
            user_data['stats'] = {
                'transactions': tx_count,
                'goals': goals_count,
                'categories': categories_count
            }
            return jsonify(user_data)
    
    if request.method == "POST":
        data = request.get_json()
        if 'full_name' in data:
            current_user.full_name = data['full_name']
        if 'email' in data:
            current_user.email = data['email']
        if 'avatar_url' in data:
            current_user.avatar_url = data['avatar_url']
        if 'notifications_enabled' in data:
            current_user.notifications_enabled = bool(data['notifications_enabled'])
        
        db.session.commit()
        return jsonify(current_user.to_dict())


