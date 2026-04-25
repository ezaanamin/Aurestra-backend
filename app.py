from flask import jsonify, request
import hashlib
from fetchers import fetch_latest_bank_email
from database import app, db  # Import app from database.py
from model import MonthlyBalance, Transaction, Budget, AccountBalance, SavingsGoal, Category, SMSHistory, FinancialInsight
from datetime import datetime, date, timedelta
import os
import json
from dateutil.relativedelta import relativedelta
import re
from werkzeug.utils import secure_filename
from time import time
from fetchers import (
    fetch_latest_bank_email,
    calculate_combined_summary
)
from drive_utils import (
    get_drive_service,
    ensure_folder_path,
    upload_json,
    get_gmail_service,
    create_message,
    send_gmail_message,
    GOOGLE_SIGNIN_OAUTH_SCOPES,
)
from fcm_utils import send_push_to_all
from sqlalchemy import func, desc, extract, case
from sms_parser import process_bank_sms, BankAlhabibSMSParser, generate_sms_hash, generate_transaction_hash
import jwt
import base64
import requests as http_requests

import secrets
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
from functools import wraps
from model import User
from google.oauth2 import id_token
from google.auth.transport import requests
from google_auth_oauthlib.flow import Flow
from database import db, app
from sqlalchemy import text
from flask_apscheduler import APScheduler

# Centralized Auth-API base URL (update AUTH_API_URL in .env when Cloudflare Tunnel rotates)
AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://9938-119-73-101-66.ngrok-free.app').rstrip('/')
# The Google account whose refresh token is held by Auth-api
AUTH_API_OWNER_EMAIL = os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')


# Config is already loaded in database.py

# SMS Batch Route moved to correct location

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or 'default_dev_secret_change_me'

# Register AI Agent API Blueprint
from ai_agent_api import ai_agent_bp
app.register_blueprint(ai_agent_bp)

# ──────────────────────────────────────────────────────
# SCHEDULER  (Midnight jobs)
# ──────────────────────────────────────────────────────
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

@scheduler.task('cron', id='do_daily_cleanup', hour=0, minute=0)
def do_daily_cleanup_job():
    """Triggered every day at 12:00 AM — cleans temp staging files."""
    print("⏰ [Scheduler] Triggering midnight cleanup task...")
    try:
        from daily_cleanup import perform_daily_cleanup
        perform_daily_cleanup()
    except Exception as e:
        print(f"❌ [Scheduler] Cleanup job failed: {e}")

@scheduler.task('cron', id='do_midnight_backup', hour=0, minute=2)
def do_midnight_backup_job():
    """
    Triggered every day at 12:02 AM (2 min after cleanup).
    3-destination backup: Google Drive → Local disk → PostgreSQL.
    """
    print("⏰ [Scheduler] Triggering midnight backup task...")
    try:
        from backup_manager import BackupManager
        bm = BackupManager()
        bm.init_app(app)
        with app.app_context():
            results = bm.perform_backup()
            ok = sum(1 for v in results.values() if v is True)
            print(f"✅ [Scheduler] Backup complete — {ok}/3 destinations succeeded.")
    except Exception as e:
        print(f"❌ [Scheduler] Backup job failed: {e}")


# Email Config (Gmail)
 

# -------------------------
# HEALTH CHECK ROUTES
# -------------------------
@app.route('/api/health', methods=['GET'])
def health_check():
    """Simple backend liveness probe"""
    return jsonify({
        "status": "online",
        "message": "Backend is running",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/api/health/db', methods=['GET'])
def db_health_check():
    """Deep database connectivity and schema check"""
    try:
        from sqlalchemy import inspect
        
        # 1. Check Connection
        db.session.execute(func.now()).scalar()
        
        # 2. Check Tables
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        
        required_tables = ['users', 'transactions', 'categories', 'budgets']
        missing_tables = [t for t in required_tables if t not in existing_tables]
        
        status = "healthy" if not missing_tables else "degraded"
        
        return jsonify({
            "status": status,
            "database": "connected",
            "tables_found": len(existing_tables),
            "missing_required_tables": missing_tables,
            "all_tables": existing_tables
        }), 200 if status == "healthy" else 503
        
    except Exception as e:
        print(f"❌ DB Health Check Failed: {e}")
        return jsonify({
            "status": "offline",
            "error": str(e)
        }), 500

# -------------------------
# UTILS
# -------------------------
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
        
        if not token:
            print("❌ [Auth] Token missing in headers")
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            user_id = data.get('user_id')
            # print(f"🔍 [Auth] Token Decoded. User ID: {user_id}")
            
            with app.app_context():
                print(f"🔍 [Auth] Looking up User ID: {user_id}")
                current_user = User.query.get(user_id)
                
            if not current_user:
                print(f"❌ [Auth] User {user_id} NOT found in DB. Table empty or reset?")
                return jsonify({'message': 'User invalid! (DB Record Missing)'}), 401
        except jwt.ExpiredSignatureError:
            print("❌ [Auth] Token Expired")
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            print(f"❌ [Auth] Token Invalid: {e}")
            return jsonify({'message': 'Token is invalid!'}), 401
        except Exception as e:
            print(f"❌ [Auth] Unexpected Auth Error: {e}")
            return jsonify({'message': 'Token error'}), 401
            
        return f(current_user, *args, **kwargs)
    
    return decorated

def generate_otp():
    """Generates a secure 6-digit OTP."""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def _build_otp_raw_email(to_email, otp_code, expiry_minutes=5):
    """Build a base64url-encoded RFC 2822 email for the Gmail API."""
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
</head>
<body style="margin:0;padding:0;background:#0a0f1e;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0f1e;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="100%" style="max-width:540px;background:#0f172a;border-radius:16px;border:1px solid #1e293b;overflow:hidden;">

          <!-- Header -->
          <tr>
            <td style="padding:32px 36px 24px;border-bottom:1px solid #1e293b;">
              <p style="margin:0;font-size:11px;letter-spacing:3px;color:#64748b;text-transform:uppercase;">Centralized AI System</p>
              <h1 style="margin:8px 0 0;font-size:26px;font-weight:700;color:#f8fafc;letter-spacing:-0.5px;">Eleystra</h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:32px 36px;">
              <p style="margin:0 0 16px;font-size:15px;color:#94a3b8;line-height:1.6;">Hello,</p>
              <p style="margin:0 0 24px;font-size:15px;color:#cbd5e1;line-height:1.7;">
                This code was requested to verify your identity and grant access to <strong style="color:#f8fafc;">Eleystra</strong>.
              </p>
              <p style="margin:0 0 24px;font-size:14px;color:#94a3b8;line-height:1.7;">
                Eleystra is your centralized AI system — a unified intelligence layer connecting all your apps, agents, and services.
                Once verified, your access extends seamlessly across the entire Eleystra ecosystem.
              </p>

              <!-- OTP Block -->
              <p style="margin:0 0 12px;font-size:12px;letter-spacing:2px;color:#64748b;text-transform:uppercase;">Your One-Time Passcode (OTP)</p>
              <div style="background:#0d1117;border:1px solid #22d3ee;border-radius:12px;padding:24px;text-align:center;margin:0 0 24px;">
                <span style="font-size:42px;font-weight:800;letter-spacing:14px;color:#22d3ee;font-variant-numeric:tabular-nums;">{otp_code}</span>
              </div>

              <p style="margin:0 0 32px;font-size:13px;color:#64748b;text-align:center;">
                This code will expire in <strong style="color:#94a3b8;">{expiry_minutes} minutes</strong>.
              </p>

              <div style="background:#0f1f2e;border-left:3px solid #1e40af;border-radius:4px;padding:14px 18px;margin:0 0 24px;">
                <p style="margin:0;font-size:13px;color:#64748b;line-height:1.6;">
                  If you did not request this verification, you can safely ignore this email. No action is required.
                </p>
              </div>

              <p style="margin:0;font-size:13px;color:#475569;">
                For support, contact:
                <a href="mailto:support@eleystra.com" style="color:#22d3ee;text-decoration:none;">support@eleystra.com</a>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:20px 36px;border-top:1px solid #1e293b;">
              <p style="margin:0;font-size:12px;color:#334155;">— Eleystra Security</p>
              <p style="margin:8px 0 0;font-size:11px;color:#1e293b;">&copy; 2026 Eleystra. All rights reserved.</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    raw = (
        f"From: Eleystra Security <{AUTH_API_OWNER_EMAIL}>\r\n"
        f"To: {to_email}\r\n"
        f"Subject: Your Eleystra Verification Code\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"\r\n"
        f"{html_content}"
    )
    return base64.urlsafe_b64encode(raw.encode('utf-8')).decode('utf-8')


def send_otp_via_auth_api(to_email, otp_code):
    """Send OTP email using the centralized Auth-api Gmail proxy."""
    try:
        raw = _build_otp_raw_email(to_email, otp_code)
        resp = http_requests.post(
            f"{AUTH_API_URL}/google/proxy/gmail/v1/users/me/messages/send",
            params={'email': AUTH_API_OWNER_EMAIL},
            json={'raw': raw},
            timeout=15,
        )
        data = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
        if resp.status_code == 200 and data.get('id'):
            print(f"✅ OTP sent via Auth-api Gmail proxy to {to_email}")
            return True
        # Auth-api may return login_required if its token expired
        if data.get('action') == 'login_required':
            print(f"⚠️ Auth-api token expired — re-login to Auth-api required.")
        else:
            print(f"⚠️ Auth-api Gmail proxy returned {resp.status_code}: {data}")
        return False
    except Exception as e:
        print(f"⚠️ Failed to send OTP via Auth-api: {e}")
        return False


def send_otp_email(to_email, otp_code):
    # Primary: Auth-api proxy (no local Google tokens needed)
    if send_otp_via_auth_api(to_email, otp_code):
        return True

    print(f"⚠️ Auth-api proxy failed, falling back to direct Gmail API for {to_email}...")
    try:
        user = User.query.filter_by(email=to_email).first()
        if not user or not user.google_refresh_token:
            print(f"⚠️ No refresh token for {to_email}. OTP cannot be sent.")
            return False
        service = get_gmail_service(user)
        if not service:
            return False
        text_content = (
            f"Your Eleystra verification code is: {otp_code}\n\n"
            f"This code expires in 5 minutes.\n\n"
            f"If you did not request this, ignore this email.\n\n"
            f"For support: support@eleystra.com\n\n— Eleystra Security"
        )
        message = create_message("me", to_email, "Your Eleystra Verification Code", text_content, "")
        result = send_gmail_message(service, "me", message)
        if result:
            print(f"✅ OTP sent via direct Gmail API to {to_email}")
            return True
        return False
    except Exception as e:
        print(f"⚠️ Direct Gmail API also failed: {e}")
        return False

# Simple in-memory rate limiting
from collections import defaultdict
from time import time

request_counts = defaultdict(list)



def save_monthly_summary(month, total_open, total_close):
    with app.app_context():
        existing = MonthlyBalance.query.filter_by(month=month).first()
        if existing:
            print(f"✅ Summary for {month} already exists in DB.")
            return
        summary = MonthlyBalance(
            source="combined",
            month=month,
            opening_balance=total_open,
            closing_balance=total_close
        )
        db.session.add(summary)
        db.session.commit()
        print(f"✅ Saved monthly summary for {month} in DB.")

# -------------------------
# REPORTS ROUTES
# -------------------------
@app.route("/api/reports/statement", methods=["POST"])
@token_required
def get_statement_report(current_user):
    from model import StatementAnalysis # Fix NameError
    data = request.get_json() or {}
    
    # Input: month "YYYY-MM" (e.g. "2026-01")
    input_month_str = data.get("month")
    
    if not input_month_str:
        # Default to current month if not provided, or handle as "Latest"
        input_month_str = datetime.now().strftime("%Y-%m")
        
    target_month_str = input_month_str
    
    # 1. Try fetching from E-Statement Analysis Table (The new Source of Truth)
    analysis = StatementAnalysis.query.filter_by(month=target_month_str).first()
    
    if analysis:
        # print(f"✅ Found Analysis for {target_month_str} in DB")
        
        # 2. Fetch linked transactions for the table
        try:
            stmt_tx_ids = json.loads(analysis.transaction_ids) if analysis.transaction_ids else []
            from model import Transaction
            linked_txs = Transaction.query.filter(Transaction.id.in_(stmt_tx_ids)).order_by(Transaction.date.desc()).all()
        except:
            linked_txs = []
            
        tx_data = []
        for t in linked_txs:
            tx_data.append({
                "date": t.date.strftime("%d/%m/%Y"),
                "amount": t.amount,
                "description": t.notes or t.sender or "Transaction",
                "type": t.type,
                "status": "existing"
            })
            
        response_data = analysis.to_dict()
        response_data["data"] = tx_data  # Populate 'data' field for the frontend table

        # RESTORED: Backup to Drive (Encrypted)
        if current_user.google_refresh_token:
            try:
                service = get_drive_service(current_user)
                if service:
                    # Ensure "Aurestra Finance/{Month}"
                    folder_id = ensure_folder_path(service, ["Aurestra Finance", target_month_str])
                    if folder_id:
                        upload_json(service, folder_id, "statement.json", response_data)
            except Exception as e:
                print(f"⚠️ Drive Backup Failed: {e}")
        
        return jsonify(response_data)
        
    # print(f"⚠️ No Analysis found for {target_month_str} in DB.")
    
    # Check if user is asking for the *current* month
    # We can't have a final statement for the current running month.
    now_str = datetime.now().strftime("%Y-%m")
    
    if target_month_str == now_str:
        return jsonify({
            "message": f"Statement for {target_month_str} is not finalized yet. Please wait for the month to end."
        }), 404
    else:
        return jsonify({
            "message": f"Analysis for {target_month_str} isn't computed. Please go to Home and click 'Calculate'."
        }), 404
        
    # ---------------------------------------------------------
    # ---------------------------------------------------------



# -------------------------
# BASIC ROUTES
# -------------------------

@app.route("/test")
def test_route():
    return jsonify({"status": "ok", "message": "Backend is deployed and running"}), 200

@app.route("/")
def home():
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', 'Unknown')
    # Mask password
    safe_uri = re.sub(r':([^@]+)@', ':****@', db_uri)
    print(f"🌍 Home Hit. Connected to: {safe_uri}")
    return f"Backend Running ✅ (DB: {safe_uri})"

# -------------------------
# AUTH ROUTES
# -------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get('email')
    # Updated Flow: Email ONLY -> OTP
    # No password check here as per user request (Passwordless/OTP-only)
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        # Prevent auto-creation for security, or keep it? 
        # User said "user shouldn't have any password". 
        # Usually this implies they just type email and get OTP.
        # But we must ensure the user exists.
        return jsonify({'message': 'User not found'}), 404

    # Generate Real Random OTP (Secure)
    # Using secrets for cryptographic randomness
    otp = ''.join([str(secrets.randbelow(10)) for _ in range(6)])
    
    user.otp_code = otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=5)
    db.session.commit()
    
    # Attempt to send Email
    email_success = send_otp_email(user.email, otp)
    
    if email_success:
        return jsonify({'message': 'OTP sent to email', 'otp_required': True}), 200
    else:
        # Fallback: Log OTP to console and allow login flow (Soft Fail)
        print(f"⚠️ [AUTH] Email sending failed. DEBUG OTP for {email}: {otp}")
        return jsonify({'message': 'OTP sent (Check Console/Dev)', 'otp_required': True}), 200

@app.route("/api/google/login", methods=["POST"])
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
            
        # 4. Generate OTP for 2FA (Keeping existing security layer)
        otp = generate_otp()
        from datetime import timezone
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        
        user.otp_code = otp
        user.otp_expiry = expiry
        db.session.commit()
        
        # Send OTP
        try:
            from drive_utils import send_otp_email
            send_otp_email(user.email, otp)
        except Exception as e:
            print(f"⚠️ [AUTH] OTP Email failed: {e}")
        
        return jsonify({
            'message': 'Please verify OTP',
            'otp_required': True,
            'email': email,
            'centralized_auth': centralized_auth,
            'auth_service_url': auth_url if not centralized_auth else None
        }), 200

    except ValueError as e:
        return jsonify({'message': f'Invalid token: {str(e)}'}), 401
    except Exception as e:
        print(f"Google Login Error: {e}")
        return jsonify({'message': 'Internal server error'}), 500

@app.route("/api/auth/status", methods=["GET"])
@token_required
def get_auth_status(current_user):
    """Checks the status of the centralized Auth Service for the current user."""
    try:
        res = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": current_user.email}, timeout=8)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        return jsonify({"authenticated": False, "error": str(e)}), 503


@app.route("/api/auth/verify", methods=["POST"])
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

    # 3. Generate OTP for 2FA
    otp = generate_otp()
    from datetime import timezone
    user.otp_code = otp
    user.otp_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    db.session.commit()

    # 4. Send OTP via Auth-api Gmail proxy
    try:
        send_otp_email(email, otp)
    except Exception as e:
        print(f"⚠️ [AUTH VERIFY] OTP send failed: {e}")

    return jsonify({
        'message': 'OTP sent to your email',
        'otp_required': True,
        'email': email,
    }), 200

@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json()
    email = data.get('email')
    otp = data.get('otp')
    
    print(f"🔒 [OTP] Verifying for email: {email} with OTP: {otp}")
    user = User.query.filter_by(email=email).first()
    
    if not user:
        print(f"❌ [OTP] User not found for email: {email}")
        return jsonify({'message': 'User not found'}), 404
        
    if user.otp_code != otp:
        print(f"❌ [OTP] Mismatch! Expected: {user.otp_code}, Got: {otp}")
        return jsonify({'message': 'Invalid OTP'}), 400
        
    if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
        print(f"❌ [OTP] Expired! Expiry: {user.otp_expiry}, Now: {datetime.utcnow()}")
        return jsonify({'message': 'OTP expired'}), 400
        
    # Generate JWT
    token = jwt.encode({
        'user_id': user.id,
        'email': user.email,
        'exp': datetime.utcnow() + timedelta(hours=2)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    # Clear OTP
    user.otp_code = None
    db.session.commit()
    
    return jsonify({
        'token': token,
        'user': user.to_dict()
    }), 200

@app.route("/api/profile", methods=["GET", "POST"])
@token_required
def profile(current_user):
    if request.method == "GET":
        with app.app_context():
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


@app.route("/api/bank/latest")
@token_required
def bank_latest(current_user):
    data = fetch_latest_bank_email()
    return jsonify(data)

# Static Config for Uploads
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/static/uploads/<filename>')
def serve_upload(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/api/upload/avatar", methods=["POST"])
@token_required
def upload_avatar(current_user):
    if 'avatar' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['avatar']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        timestamp = int(time())
        filename = secure_filename(f"user_{current_user.id}_{timestamp}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Build URL (Relative for now)
        avatar_url = f"/static/uploads/{filename}"
        
        current_user.avatar_url = avatar_url
        db.session.commit()
        
        return jsonify({'message': 'File uploaded', 'avatar_url': avatar_url}), 200

@app.route("/api/wallet/latest")
@token_required
def wallet_latest(current_user):
    data = fetch_latest_wallet_email()
    return jsonify(data)


@app.route("/api/accounts/set_balance", methods=["POST"])
@token_required
def set_manual_balance(current_user):
    """
    Manually set the account balance.
    This sets is_manual=True, preventing older statements from overwriting it.
    """
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        source = data.get('source', 'bank')
        
        balance = AccountBalance.query.filter_by(source=source).first()
        if not balance:
            balance = AccountBalance(source=source, current_balance=amount)
            db.session.add(balance)
        
        # Update Balance and set Manual Flag
        balance.current_balance = amount
        balance.is_manual = True
        balance.last_updated = datetime.now()
        
        db.session.commit()
        
        # FIX: Fetch ALL accounts to return complete updated state
        all_accounts = AccountBalance.query.all()
        
        return jsonify({
            "message": "Balance updated manually",
            "account": balance.to_dict(),
            "accounts": [acc.to_dict() for acc in all_accounts]  # Return all
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/accounts", methods=["GET"])
@token_required
def get_accounts(current_user):
    with app.app_context():
        # 1. Start with fresh statement balance if possible
        try:
            bank_data = fetch_latest_bank_email()
            if "balances" in bank_data:
                closing_bal = bank_data["balances"].get("closing_balance", 0.0)
                email_date = bank_data.get("date") # Assuming fetch_latest_bank_email returns a datetime object
                
                bank_acc = AccountBalance.query.filter_by(source="bank").first()
                if bank_acc:
                    # CHECK MANUAL OVERRIDE & TIMESTAMP Logic
                    should_update = True
                    
                    if bank_acc.is_manual:
                        # LOCKED: Do not update if manual
                        should_update = False
                    
                    # Also check TIMESTAMP: Only update if email is NEWER than last_updated
                    # This prevents stale transaction emails from overwriting fresh statement data
                    if email_date and bank_acc.last_updated:
                        if email_date <= bank_acc.last_updated:
                            print(f"⏭️  Stale email balance ({email_date}) ignored. Current balance is newer ({bank_acc.last_updated})")
                            should_update = False
                    
                    if should_update:
                        print(f"🔄 Updating Account Balance from Email: {closing_bal}")
                        bank_acc.current_balance = closing_bal
                        bank_acc.last_updated = datetime.now()
                        bank_acc.is_manual = False  # Email update removes manual lock
                    
                    # SAVE TRANSACTIONS (Always process transactions, just don't overwrite balance if manual)
                    extracted_txs = bank_data.get("transactions", [])
                    new_tx_count = 0
                    for tx in extracted_txs:
                        try:
                            tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
                        except:
                            tx_date = datetime.now()

                        # New Deduplication Logic:
                        # 1. Search for ANY transaction with same Amount & Type within +/- 2 days.
                        candidates = Transaction.query.filter(
                            Transaction.amount == tx["amount"],
                            Transaction.type == tx["type"],
                            Transaction.date >= tx_date - timedelta(days=2),
                            Transaction.date <= tx_date + timedelta(days=2)
                        ).all()
                        
                        # 🎯 ROBUST DEDUPLICATION using Hashing
                        tx_hash = Transaction.generate_deterministic_hash({
                            "date": tx_date,
                            "amount": tx["amount"],
                            "type": tx["type"],
                            "description": tx["description"]
                        })
                        
                        # Check existance by HASH (idempotent)
                        exists = Transaction.query.filter_by(transaction_hash=tx_hash).first()
                        
                        if not exists:
                            # 🛡️ FALLBACK: Check for 'Similar' transaction (e.g. from SMS)
                            # Checking +/- 2 days to account for statement vs SMS date differences
                            exists = Transaction.query.filter(
                                Transaction.amount == tx["amount"],
                                Transaction.type == tx["type"],
                                Transaction.date >= tx_date - timedelta(days=2),
                                Transaction.date <= tx_date + timedelta(days=2)
                            ).first()
                            if exists:
                                print(f"🔗 Similar transaction found (SMS overlap?): {tx_date.date()} | {tx['amount']}")
                                # Update existing transaction with the statement hash if missing
                                if not exists.transaction_hash:
                                    exists.transaction_hash = tx_hash

                        if not exists:
                            try:
                                new_tx = Transaction(
                                    source="bank",
                                    date=tx_date,
                                    amount=tx["amount"],
                                    type=tx["type"],
                                    purpose="Uncategorized",
                                    sender="Bank Statement",
                                    receiver="Me",
                                    notes=tx["description"][:250],
                                    transaction_hash=tx_hash  # MUST set this!
                                )
                                db.session.add(new_tx)
                                db.session.flush() # Force UNIQUE check
                                new_tx_count += 1
                                print(f"✅ Added new transaction: {tx_hash[:10]}...")
                            except Exception as e:
                                db.session.rollback()
                                print(f"⏭️  Duplicate tx_hash group detected (race condition parented): {e}")
                                continue
                    
                    if new_tx_count > 0:
                        try:
                             send_push_to_all(
                                 title="New Bank Transactions",
                                 body=f"Found {new_tx_count} new transaction(s) from your bank statement."
                             )
                        except Exception as e:
                             print(f"⚠️ Push failed in get_accounts: {e}")

                    db.session.commit()
        except:
            pass

        # 2. Fetch all accounts
        accounts = AccountBalance.query.all()
        
        # 3. Calculate LIVE Balance (Statement + Recent SMS Adjustments)
        # Note: Savings are now Transaction-based, so we simply return the current_balance
        # which already has savings deducted.
        
        response_data = []
        cutoff_date = datetime.today().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        for acc in accounts:
            acc_dict = acc.to_dict()
            
            if acc.source == 'bank':
                # SIMPLIFICATON: The AccountBalance table *is* the running balance.
                # SMS transactions update it directly (via sms_parser.py).
                # Manual updates update it directly.
                # Email statements overwrite it (logic above).
                # validation: We do NOT need to calculate an adjustment here, as that causes double-counting.
                
                # Update response
                acc_dict['balance'] = acc.current_balance
                acc_dict['statement_base'] = acc.current_balance
                acc_dict['live_adjustment'] = 0.0
                acc_dict['savings_reduction'] = 0.0 
                
            response_data.append(acc_dict)

        return jsonify(response_data)

@app.route("/api/savings-goals", methods=["GET", "POST"])
@token_required
def manage_savings_goals(current_user):
    if request.method == "GET":
        goals = SavingsGoal.query.all()
        return jsonify([g.to_dict() for g in goals])
    
    if request.method == "POST":
        data = request.get_json()
        name = data.get("name")
        target = float(data.get("target_amount", 0))
        current = float(data.get("current_amount", 0))
        emoji = data.get("emoji", "💰")
        deadline_str = data.get("deadline") # YYYY-MM-DD
        
        deadline_date = None
        if deadline_str:
            try:
                deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            except:
                pass
        
        new_goal = SavingsGoal(
            name=name,
            target_amount=target,
            current_amount=current,
            emoji=emoji,
            deadline=deadline_date
        )
        db.session.add(new_goal)
        db.session.commit()
        
        return jsonify(new_goal.to_dict()), 201

@app.route("/api/savings-goals/<int:id>", methods=["PUT"])
@token_required
def update_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        data = request.get_json()
        if "name" in data:
            goal.name = data["name"]
        if "target_amount" in data:
            goal.target_amount = float(data["target_amount"])
        if "current_amount" in data:
            goal.current_amount = float(data["current_amount"])
        if "emoji" in data:
            goal.emoji = data["emoji"]
        if "deadline" in data:
            try:
                goal.deadline = datetime.strptime(data["deadline"], "%Y-%m-%d").date()
            except:
                pass
                
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/savings-goals/<int:id>", methods=["DELETE"])
@token_required
def delete_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        # REFUND LOGIC: Check if money was allocated
        if goal.current_amount > 0:
            refund_amount = goal.current_amount
            
            # Find primary bank account to refund to
            bank_acc = AccountBalance.query.filter_by(source='bank').first()
            if not bank_acc:
                bank_acc = AccountBalance.query.first()
                
            if bank_acc:
                bank_acc.current_balance += refund_amount
                bank_acc.last_updated = datetime.utcnow()
                
                # Create Refund Transaction
                refund_tx = Transaction(
                    source='bank',
                    date=datetime.utcnow(),
                    amount=refund_amount,
                    type='credit',
                    purpose='Savings Refund',
                    sender='Savings Goal',
                    receiver='Me',
                    notes=f"Refund from deleted goal: {goal.name}"
                )
                db.session.add(refund_tx)
            
        db.session.delete(goal)
        db.session.commit()
        return jsonify({"message": "Goal deleted and funds refunded"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/savings-goals/<int:id>/contribute", methods=["POST"])
@token_required
def contribute_to_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        data = request.get_json()
        amount = float(data.get("amount", 0))
        
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than zero"}), 400
            
        # TRANSACTION Logic: Deduct from Bank Account
        # TRANSACTION Logic: Deduct from Bank Account
        # Try to find a 'bank' account first, otherwise pick the first available one (e.g. Cash)
        bank_acc = AccountBalance.query.filter_by(source='bank').first()
        if not bank_acc:
             bank_acc = AccountBalance.query.first()
             
        if not bank_acc:
            return jsonify({"error": "No account found to fund savings. Please create an account first."}), 400
            
        # Optional: Check Strict Balance?
        # if bank_acc.current_balance < amount:
        #    return jsonify({"error": "Insufficient funds in bank account"}), 400
             
        # 1. Deduct from Balance
        bank_acc.current_balance -= amount
        bank_acc.last_updated = datetime.utcnow()
        
        # 2. Create Debit Transaction
        contrib_tx = Transaction(
            source='bank',
            date=datetime.utcnow(),
            amount=amount,
            type='debit',
            purpose='Savings',
            sender='Me',
            receiver='Savings Goal',
            notes=f"Contribution to: {goal.name}"
        )
        db.session.add(contrib_tx)
        
        # 3. Update Goal
        goal.current_amount += amount
        
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
# -------------------------
# EXPENSE ROUTES
# -------------------------

def calculate_month_expenses(year, month):
    """
    Running-balance expense calculation (date-ordered).

    Rules:
      - Transactions are processed in chronological order.
      - Debit  → adds to running expense total.
      - Credit → reduces running expense, but ONLY what has already
                 been accumulated. It CANNOT go below 0.

    This means:
      - A bonus/income that arrives BEFORE any spending has NO effect.
      - A refund that arrives AFTER a purchase correctly reduces it.

    Example A (your case):
      Mar 01  Credit Rs 13,000  → running=0  (nothing to reduce)
      Mar 05  Debit  Rs  5,400  → running=5,400
      Result: Rs 5,400  ✅

    Example B (refund case):
      Mar 01  Debit  Rs 5,400   → running=5,400
      Mar 05  Credit Rs   100   → running=5,300
      Result: Rs 5,300  ✅

    Example C (credit wipes all spending):
      Mar 01  Debit  Rs 5,400   → running=5,400
      Mar 05  Credit Rs 6,000   → running=0  (clamped)
      Result: Rs 0  ✅
    """
    transactions = Transaction.query.filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.is_deleted != True,
        Transaction.is_spam    != True,
    ).order_by(Transaction.date.asc()).all()   # ← chronological order is key

    running = 0.0
    for txn in transactions:
        if txn.type == 'debit':
            running += txn.amount
        elif txn.type == 'credit':
            running = max(0.0, running - txn.amount)  # only reduce existing spending

    return running




@app.route("/api/expenses/total", methods=["GET"])
@token_required
def get_total_expenses(current_user):
    try:
        dt = datetime.now()
        year, month = dt.year, dt.month
        month_str = dt.strftime("%Y-%m")

        # --- Calculate from scratch ---
        total_expenses = calculate_month_expenses(year, month)

        # Also expose raw totals for debugging / other screens
        total_debits = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year',  Transaction.date) == year,
            extract('month', Transaction.date) == month,
            Transaction.type        == 'debit',
            Transaction.is_deleted  != True,
            Transaction.is_spam     != True,
        ).scalar() or 0.0

        total_credits = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year',  Transaction.date) == year,
            extract('month', Transaction.date) == month,
            Transaction.type        == 'credit',
            Transaction.is_deleted  != True,
            Transaction.is_spam     != True,
        ).scalar() or 0.0

        # --- Persist to Budget.total_expenses ---
        budget_entry = Budget.query.filter_by(month=month_str).first()
        if budget_entry:
            budget_entry.total_expenses = total_expenses
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"⚠️  Could not persist total_expenses to Budget: {e}")

        print(f"💰 [{month_str}] debits={total_debits:.2f}  credits={total_credits:.2f}  total_expenses={total_expenses:.2f}")

        return jsonify({
            "month":          month_str,
            "total_expense":  total_expenses,   # net (debits − credits, min 0)
            "total_debits":   total_debits,
            "total_credits":  total_credits,
        }), 200

    except Exception as e:
        print(f"❌ Failed to get total expenses: {e}")
        return jsonify({"error": str(e)}), 500



# -------------------------
# SUMMARY ROUTES
# -------------------------
@app.route("/api/monthly-summary", methods=["GET"])
@token_required
def get_monthly_summary_from_db(current_user):
    current_month = datetime.now().strftime("%Y-%m")
    
    with app.app_context():
        # 1. ALWAYS Calculate Truth from Transaction Table
        dt = datetime.now()
        
        # Dynamic Expense Calculation
        dynamic_expense = db.session.query(
            func.sum(case(
                (Transaction.type == 'debit', Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0
            ))
        ).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.is_deleted != True,
            Transaction.is_spam != True,
            Transaction.categorization_status != 'pending'
        ).scalar() or 0.0

        # Dynamic Income Calculation 
        dynamic_income_tx = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit',
            Transaction.is_deleted != True,
            Transaction.is_spam != True,
            Transaction.categorization_status != 'pending'
        ).scalar() or 0.0
        
        # Check budget for income override
        budget_entry = Budget.query.filter_by(month=current_month).first()
        final_income = dynamic_income_tx
        if budget_entry and budget_entry.total_budget > 0:
            final_income = budget_entry.total_budget
            
        final_savings = final_income - dynamic_expense

        # 2. Update/Create MonthlyBalance persistence
        summary = MonthlyBalance.query.filter_by(month=current_month).first()
        
        # FIX: Closing Balance should be the TOTAL CURRENT BALANCE (Bank + Wallet + etc)
        total_current_balance = 0
        account_balances = AccountBalance.query.all()
        for acc in account_balances:
            total_current_balance += acc.current_balance

        if not summary:
            # Create new
            summary = MonthlyBalance(
                source="auto-dynamic",
                month=current_month,
                opening_balance=0,
                closing_balance=total_current_balance, # Use actual total balance
                expense=dynamic_expense,
                # income=final_income, # REMOVED
                savings=final_savings,
                fetched_at=datetime.now()
            )
            db.session.add(summary)
        else:
            # Update existing
            summary.closing_balance = total_current_balance # Update with actual balance
            summary.expense = dynamic_expense
            # summary.income = final_income # REMOVED
            summary.savings = final_savings
            summary.fetched_at = datetime.now()
            
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Failed to update summary persistence: {e}")

        # 3. Return Dynamic Data
        return jsonify({
            "month": current_month,
            "opening_balance": summary.opening_balance,
            "closing_balance": summary.closing_balance,
            "total_expense": dynamic_expense,
            "total_income": final_income,
            "total_savings": final_savings,
            "fetched_at": datetime.now().strftime("%d %b %Y %H:%M:%S")
        })

# -------------------------
# BUDGET ROUTES
# -------------------------
@app.route("/api/budget", methods=["POST"])
@token_required
def save_budget(current_user):
    data = request.get_json()
    month = datetime.now().strftime("%Y-%m")
    
    required_fields = ['income', 'needs', 'wants', 'saving']
    
    for field in required_fields:
        if field not in data:
            return jsonify({
                "message": f"Missing '{field}' in request body."
            }), 400
    
    try:
        total_budget_amount = float(data['income'])
        needs_amount = float(data['needs'])
        wants_amount = float(data['wants'])
        saving_amount = float(data['saving'])
    except ValueError:
        return jsonify({
            "message": "Invalid value provided. All amounts must be numbers."
        }), 400
    
    with app.app_context():
        try:
            existing_budget = Budget.query.filter_by(month=month).first()

            if existing_budget:
                existing_budget.total_budget = total_budget_amount
                existing_budget.needs = needs_amount
                existing_budget.wants = wants_amount
                existing_budget.saving = saving_amount
                db.session.commit()
                
                return jsonify({
                    "message": f"Budget for {month} updated successfully.",
                    "month": month,
                    "total_budget": total_budget_amount,
                    "needs": needs_amount,
                    "wants": wants_amount,
                    "saving": saving_amount
                }), 200
            else:
                new_budget = Budget(
                    month=month,
                    total_budget=total_budget_amount,
                    needs=needs_amount,
                    wants=wants_amount,
                    saving=saving_amount
                )
                db.session.add(new_budget)
                db.session.commit()
                
                return jsonify({
                    "message": f"Budget for {month} created successfully.",
                    "month": month,
                    "total_budget": total_budget_amount,
                    "needs": needs_amount,
                    "wants": wants_amount,
                    "saving": saving_amount
                }), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"message": f"Database error: {str(e)}"}), 500

@app.route("/api/budget", methods=["GET"])
@token_required
def get_budget(current_user):
    month = datetime.now().strftime("%Y-%m")
    
    with app.app_context():
        budget = Budget.query.filter_by(month=month).first()
        # ... logic ... (simplified, just call existing logic or wrap it)
        # Note: Original code didn't use user ID, but we should eventually.
        # For now, we just protecting the route.
        if not budget:
             return jsonify({"message": f"No budget found for {month}."}), 404
        
        created_at_str = budget.created_at.strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate Spending Limit (User Request: Sum Wants + Needs)
        spending_limit = (budget.needs or 0) + (budget.wants or 0)
        
        return jsonify({
            "month": budget.month,
            "total_budget": budget.total_budget, # This is Income
            "needs": budget.needs,
            "wants": budget.wants,
            "saving": budget.saving,
            "spending_limit": spending_limit, # The actual limit to show
            "created_at": created_at_str
        }), 200

@app.route("/api/budget/history", methods=["GET"])
def get_budget_history():
    months_to_fetch = 4
    months_list = []
    today = datetime.now()

    for i in range(months_to_fetch):
        target_date = today - relativedelta(months=i)
        months_list.append(target_date.strftime("%Y-%m"))

    with app.app_context():
        budget_records = Budget.query.filter(Budget.month.in_(months_list)).all()
        budget_map = {b.month: b for b in budget_records}

        balance_records = MonthlyBalance.query.filter(MonthlyBalance.month.in_(months_list)).all()
        balance_map = {m.month: m for m in balance_records}
        
        history_data = []
        for month_str in months_list:
            budget_rec = budget_map.get(month_str)
            balance_rec = balance_map.get(month_str)
            
            # Dynamic Calculation for Freshness
            dt = datetime.strptime(month_str, "%Y-%m")
            fresh_expense = db.session.query(
                func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                ))
            ).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.is_deleted != True,
                Transaction.is_spam != True,
                Transaction.categorization_status != 'pending'
            ).scalar() or 0.0
            
            fresh_income = db.session.query(func.sum(Transaction.amount)).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.type == 'credit',
                Transaction.is_deleted != True,
                Transaction.is_spam != True,
                Transaction.categorization_status != 'pending'
            ).scalar() or 0.0
            
            # Use stored balance if available, otherwise 0
            stored_expense = getattr(balance_rec, 'expense', 0.0)
            
            # Prefer fresh calculation if stored is 0 but fresh is > 0
            final_expense = fresh_expense if fresh_expense > 0 else stored_expense
            
            # Closing balance logic
            closing = getattr(balance_rec, 'closing_balance', 0.0)
            
            # Saving = Income - Expense (Simple view)
            # Or use budget.saving if strict.
            # Let's use (Income - Expense) as actual savings
            final_savings = fresh_income - final_expense

            history_data.append({
                "month": month_str,
                "budget": {
                    "total_budget": getattr(budget_rec, 'total_budget', 0.0),
                    "needs": getattr(budget_rec, 'needs', 0.0),
                    "wants": getattr(budget_rec, 'wants', 0.0),
                    "saving": getattr(budget_rec, 'saving', 0.0),
                },
                "actual": {
                    "expense": final_expense,
                    "savings": final_savings,
                    "closing_balance": closing,
                }
            })

    return jsonify(history_data), 200

# -------------------------
# DEVICE & NOTIFICATIONS
# -------------------------

@app.route("/api/register-device", methods=["POST"])
def register_device():
    data = request.get_json()
    token = data.get("token")
    auth_header = request.headers.get('Authorization')
    user_id = None

    if auth_header and auth_header.startswith("Bearer "):
        try:
            jwt_token = auth_header.split(" ")[1]
            payload = jwt.decode(jwt_token, app.config['SECRET_KEY'], algorithms=["HS256"])
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

@app.route("/api/easypaisa/latest", methods=["GET", "POST"])
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

@app.route("/api/send-test", methods=["POST"])
def send_test_push():
    send_push_to_all(
        title="Test FCM",
        body="Backend test push"
    )
    return {"status": "sent"}, 200

# -------------------------
# TRANSACTION ROUTES
# -------------------------
@app.route('/api/analytics/trend', methods=['GET'])
def get_analytics_trend():
    period = request.args.get('period', default='month')
    
    data_points = []
    
    if period == 'week':
        # Last 7 days
        for i in range(6, -1, -1):
            target_date = datetime.now().date() - timedelta(days=i)
            total = (
                db.session.query(func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                )))
                .filter(
                    func.date(Transaction.date) == target_date,
                    Transaction.is_deleted != True,
                    Transaction.is_spam != True,
                    Transaction.categorization_status != 'pending'
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": target_date.strftime("%a"), # Mon, Tue...
                "value": float(total)
            })
            
    elif period == 'month':
        # Last 6 months (default behavior for trend)
        for i in range(5, -1, -1):
            target_date = datetime.now() - relativedelta(months=i)
            month_str = target_date.strftime("%Y-%m")
            
            total = (
                db.session.query(func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                )))
                .filter(
                    extract('year', Transaction.date) == target_date.year,
                    extract('month', Transaction.date) == target_date.month,
                    Transaction.is_deleted != True,
                    Transaction.is_spam != True,
                    Transaction.categorization_status != 'pending'
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": target_date.strftime("%b"), # Jan, Feb...
                "value": float(total)
            })
            
    elif period == 'year':
        # Current year months
        for i in range(1, 13):
            year = datetime.now().year
            total = (
                db.session.query(func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                )))
                .filter(
                    extract('year', Transaction.date) == year,
                    extract('month', Transaction.date) == i,
                    Transaction.is_deleted != True,
                    Transaction.is_spam != True,
                    Transaction.categorization_status != 'pending'
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": date(year, i, 1).strftime("%b"),
                "value": float(total)
            })
            
    elif period == 'all':
        # Last 5 years
        for i in range(4, -1, -1):
            year = datetime.now().year - i
            total = (
                db.session.query(func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                )))
                .filter(
                    extract('year', Transaction.date) == year,
                    Transaction.is_deleted != True,
                    Transaction.is_spam != True,
                    Transaction.categorization_status != 'pending'
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": str(year),
                "value": float(total)
            })
            
    return jsonify(data_points), 200

@app.route('/api/latest-transactions', methods=['GET'])
def latest_transactions():
    try:
        limit = request.args.get('limit', default=4, type=int)
        transactions = (
            db.session.query(Transaction)
            .filter(
                Transaction.is_deleted != True,
                Transaction.is_spam != True
            )
            .order_by(desc(Transaction.date))
            .limit(limit)
            .all()
        )

        result = [txn.to_dict() for txn in transactions]

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/top-categories', methods=['GET'])
def top_spending_categories():
    period = request.args.get('period', default='month')

    query = db.session.query(
        Transaction.purpose.label("category"),
        func.sum(Transaction.amount).label("total_spent")
    ).filter(
        Transaction.type == 'debit',  # ONLY debits, never credits
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.is_deleted != True,
        Transaction.is_spam != True,
        Transaction.categorization_status != 'pending'
    )

    if period == 'week':
        start_date = datetime.now() - timedelta(days=7)
        query = query.filter(Transaction.date >= start_date)
    elif period == 'month':
        dt = datetime.now()
        query = query.filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month
        )
    elif period == 'year':
        query = query.filter(
            extract('year', Transaction.date) == datetime.now().year
        )

    categories = (
        query.group_by(Transaction.purpose)
        .having(func.sum(Transaction.amount) > 0)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10)
        .all()
    )

    result = [
        {"category": cat.category, "total_spent": cat.total_spent}
        for cat in categories
    ]
    return jsonify(result), 200

# -------------------------
# TRANSACTION MANAGEMENT (DELETION, SPAM, CATEGORIZATION)
# -------------------------

@app.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
@token_required
def delete_transaction(current_user, txn_id):
    """Permanently marks a transaction as deleted"""
    try:
        transaction = Transaction.query.get(txn_id)
        if not transaction:
            return jsonify({"error": "Transaction not found"}), 404
            
        transaction.is_deleted = True
        transaction.categorization_status = 'deleted' # Optional flag update
        db.session.commit()
        
        # Note: We keep the sms_hash so if the same SMS comes in, 
        # process_bank_sms will find this record and see it's already there (though deleted).
        
        return jsonify({"success": True, "message": "Transaction deleted permanently"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/<int:txn_id>/spam', methods=['POST'])
@token_required
def mark_as_spam(current_user, txn_id):
    """Marks a transaction as spam"""
    try:
        transaction = Transaction.query.get(txn_id)
        if not transaction:
            return jsonify({"error": "Transaction not found"}), 404
            
        transaction.is_spam = True
        transaction.categorization_status = 'spam' # Optional flag update
        db.session.commit()
        
        return jsonify({"success": True, "message": "Transaction marked as spam"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/uncategorized', methods=['GET'])
@token_required
def get_uncategorized_transactions(current_user):
    """Fetch transactions that are not yet categorized and not deleted/spam"""
    try:
        transactions = Transaction.query.filter(
            Transaction.categorization_status == 'pending',
            Transaction.is_deleted != True,
            Transaction.is_spam != True
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify({
            "count": len(transactions),
            "transactions": [txn.to_dict() for txn in transactions]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/spam', methods=['GET'])
@token_required
def get_spam_transactions(current_user):
    """Fetch transactions marked as spam"""
    try:
        transactions = Transaction.query.filter(
            Transaction.is_spam == True,
            Transaction.is_deleted != True
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify([txn.to_dict() for txn in transactions]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/categorized', methods=['GET'])
@token_required
def get_categorized_transactions(current_user):
    """Fetch transactions that are categorized and not deleted/spam"""
    try:
        # Categorized means categorization_status is NOT pending
        transactions = Transaction.query.filter(
            Transaction.categorization_status != 'pending',
            Transaction.is_deleted != True,
            Transaction.is_spam != True
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify([txn.to_dict() for txn in transactions]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------
# SMS ROUTES (NEW) 🎉
# -------------------------

@app.route("/api/sms/process", methods=["POST"])
def process_sms():
    """Process bank SMS and create transaction"""
    try:
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_process', 20, 60):
        #     return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({"error": "SMS message required"}), 400
        
        message = data.get('message')
        sender = data.get('sender', 'BAHL')
        
        if not message or len(message.strip()) < 10:
            return jsonify({"error": "Invalid SMS message"}), 400
        
        # Consistent hash generation if metadata is provided
        external_sms_hash = None
        device_sms_id = data.get('_id') or data.get('id')
        date_val = data.get('date')
        
        if device_sms_id and date_val:
            device_timestamp = None
            if isinstance(date_val, int):
                device_timestamp = datetime.fromtimestamp(date_val / 1000.0)
            elif isinstance(date_val, str):
                try:
                    device_timestamp = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                except:
                    device_timestamp = datetime.utcnow()
            else:
                device_timestamp = datetime.utcnow()
                
            hash_input = f"{device_sms_id}|{sender}|{message}|{device_timestamp.isoformat()}"
            external_sms_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

        # result is now (transaction, is_new)
        transaction, is_new = process_bank_sms(message, sender, external_sms_hash=external_sms_hash)
        
        if not transaction:
            from sms_parser import BankAlhabibSMSParser
            if not BankAlhabibSMSParser.is_transaction_sms(message):
                return jsonify({
                    "status": "skipped",
                    "message": "SMS is not a transaction (OTP, info, etc.)"
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "message": "Could not parse transaction from SMS"
                }), 400
        
        status_msg = "recorded" if is_new else "already exists"
        print(f"✅ SMS transaction {status_msg}: {transaction.id}")
        
        from model import AccountBalance
        accounts = [acc.to_dict() for acc in AccountBalance.query.all()]
        
        if True: # Always try to send if we have tokens (send_push_to_all handles the check)
            try:
                if transaction.type == 'debit':
                    merchant = transaction.receiver or "Merchant"
                    title = "💸 New Spending Detected"
                    body = f"You just spent PKR {transaction.amount:,.0f} at {merchant}. Tap to categorize it now!"
                    send_push_to_all(title, body)
                elif transaction.type == 'credit':
                    sender = transaction.sender or "Source"
                    title = "💰 Money Received!"
                    body = f"PKR {transaction.amount:,.0f} has been credited to your account from {sender}. Tap to see details."
                    send_push_to_all(title, body)
            except Exception as e:
                print(f"⚠️ Push notification failed: {e}")

        
        return jsonify({
            "status": "success",
            "message": "Transaction created from SMS",
            "transaction": transaction.to_dict(),
            "accounts": accounts
        }), 201
        
    except Exception as e:
        print(f"❌ Error processing SMS: {e}")
        return jsonify({"error": "Failed to process SMS"}), 500

@app.route("/api/sms/test", methods=["POST"])
def test_sms_parsing():
    """Test SMS parsing without saving"""
    try:
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_test', 10, 60):
        #     return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({"error": "SMS message required"}), 400
        
        message = data.get('message')
        sender = data.get('sender', 'BAHL')
        
        transaction_data = BankAlhabibSMSParser.parse_sms(message, sender)
        
        if not transaction_data:
            if not BankAlhabibSMSParser.is_transaction_sms(message):
                return jsonify({
                    "status": "skipped",
                    "message": "Not a transaction SMS"
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "message": "Could not parse SMS"
                }), 400
        
        response = {
            "status": "success",
            "message": "SMS parsed successfully",
            "parsed_data": {
                "type": transaction_data['type'],
                "amount": transaction_data['amount'],
                "purpose": transaction_data['purpose'],
                "date": transaction_data['date'].isoformat(),
                "notes": transaction_data['notes'],
            }
        }
        
        if transaction_data['type'] == 'credit':
            response['parsed_data']['sender'] = transaction_data['sender']
        else:
            response['parsed_data']['receiver'] = transaction_data['receiver']
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/sms/batch", methods=["POST"])
@token_required
def process_batch_sms(current_user):
    """
    Process multiple SMS messages with Backend-Authoritative Deduplication.
    1. Insert raw messages into 'sms_messages' (deduplicated by unique sms_hash).
    2. Process only 'pending' messages to create transactions.
    """
    from model import SMSHistory, Transaction
    from sms_parser import generate_sms_hash, process_bank_sms
    
    try:
        # Rate limiting
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_batch', 10, 60):
        #     return jsonify({"error": "Rate limit exceeded."}), 429
        
        data = request.get_json()
        messages = data.get('messages', [])
        
        if not messages:
            return jsonify({"error": "Messages array required"}), 400
        
        if len(messages) > 1000: # Increased limit for raw sync
             return jsonify({"error": "Maximum 1000 messages per batch"}), 400
             
        stats = {
            'received': len(messages),
            'inserted': 0,
            'processed': 0,
            'transactions_created': 0,
            'duplicates_ignored': 0,
            'errors': 0
        }
        
        # 1. INSERT RAW MESSAGES (Database-Level Deduplication)
        new_sms_ids = []
        
        if messages:
            print(f"📥 [Batch] Received {len(messages)} messages")
            print(f"📥 [Sample] First Item: {messages[0]}")
        
        for msg_data in messages:
            try:
                # Extract fields
                body = msg_data.get('body') or msg_data.get('message')
                sender = msg_data.get('address') or msg_data.get('sender')
                device_sms_id = str(msg_data.get('_id', '')) or str(msg_data.get('id', ''))  # Android SMS ID
                
                # Handle timestamp: Frontend sends milliseconds
                date_val = msg_data.get('date')
                device_timestamp = None
                
                if isinstance(date_val, int):
                    device_timestamp = datetime.fromtimestamp(date_val / 1000.0)
                elif isinstance(date_val, str):
                    try:
                        device_timestamp = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                    except:
                        device_timestamp = datetime.utcnow()
                else:
                    device_timestamp = datetime.utcnow()
                    
                if not body or not sender:
                    print(f"⚠️ Skipping SMS: Missing body or sender")
                    continue

                # 🎯 DETERMINISTIC HASH GENERATION
                # hash_input = device_sms_id + sender + body + device_timestamp
                hash_input = f"{device_sms_id}|{sender}|{body}|{device_timestamp.isoformat()}"
                sms_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
                
                print(f"🔐 Hash: {sms_hash[:16]}... (ID: {device_sms_id}, Sender: {sender})")
                
                # 🛡️ DATABASE-LEVEL DEDUPLICATION
                # Try to insert - UNIQUE constraint on sms_hash will prevent duplicates
                new_msg = SMSHistory(
                    device_sms_id=device_sms_id,
                    sender=sender,
                    body=body,
                    device_timestamp=device_timestamp,
                    sms_hash=sms_hash,
                    status='pending'
                )
                
                db.session.add(new_msg)
                
                try:
                    db.session.flush()  # Force constraint check NOW
                    new_sms_ids.append(new_msg.id)
                    stats['inserted'] += 1
                    print(f"✅ Inserted SMS ID: {new_msg.id}")
                    
                except Exception as flush_error:
                    db.session.rollback()  # Rollback this specific insert
                    
                    # Check if it's a duplicate (UNIQUE constraint violation)
                    if 'Duplicate entry' in str(flush_error) or 'UNIQUE constraint' in str(flush_error):
                        stats['duplicates_ignored'] += 1
                        print(f"⏭️  Duplicate ignored: {sms_hash[:16]}...")
                    else:
                        stats['errors'] += 1
                        print(f"❌ Insert error: {flush_error}")
                
            except Exception as e:
                print(f"❌ Error processing SMS: {e}")
                stats['errors'] += 1
        
        db.session.commit()  # Commit all successful inserts
        
        # 2. PROCESS PENDING MESSAGES
        pending_messages = SMSHistory.query.filter(SMSHistory.id.in_(new_sms_ids)).all()
        
        affected_months = set()
        created_transactions = [] # List to hold details for UI
        
        for sms in pending_messages:
            try:
                # Use parser
                transaction, is_new = process_bank_sms(sms.body, sms.sender, external_sms_hash=sms.sms_hash)
                
                if transaction:
                    if is_new:
                        stats['transactions_created'] += 1
                        month_key = transaction.date.strftime('%Y-%m')
                        affected_months.add(month_key)
                        
                        # Add to list for UI
                        created_transactions.append({
                            'id': transaction.id,
                            'type': transaction.type,
                            'amount': transaction.amount,
                            'date': transaction.date.isoformat(),
                            'purpose': transaction.purpose,
                            'is_new': True
                        })
                    
                    sms.status = 'processed'
                else:
                    sms.status = 'ignored'
                    
                stats['processed'] += 1
                
            except Exception as e:
                print(f"❌ Error processing SMS {sms.id}: {e}")
                sms.status = 'error'
                stats['errors'] += 1
        
        db.session.commit()
        
        # 3. Update Summaries
        for month_key in affected_months:
             # (Simplified summary update trigger)
            pass

        stats['transactions'] = created_transactions # Add to response
        
        print(f"✅ Batch Complete: {stats}")
        return jsonify(stats), 200

    except Exception as e:
        db.session.rollback()
        print(f"🔥 Batch Fatal Error: {e}")
        return jsonify({"error": str(e)}), 500


# -------------------------
# CATEGORY ROUTES
# -------------------------
@app.route('/api/categories', methods=['GET'])
def get_categories():
    categories = Category.query.all()
    return jsonify([c.to_dict() for c in categories]), 200

@app.route('/api/categories', methods=['POST'])
def add_category():
    data = request.json
    name = data.get('name')
    if not name:
        return jsonify({"error": "Name is required"}), 400
    
    # Check if exists
    if Category.query.filter_by(name=name).first():
        return jsonify({"error": "Category already exists"}), 400
    
    category = Category(
        name=name,
        icon=data.get('icon', 'cash'),
        color=data.get('color', '#64748B'),
        cat_type=data.get('cat_type', 'spending'),
        is_default=False
    )
    db.session.add(category)
    db.session.commit()
    return jsonify(category.to_dict()), 201

@app.route('/api/categories/<int:id>', methods=['DELETE'])
def delete_category(id):
    category = Category.query.get(id)
    if not category:
        return jsonify({"error": "Category not found"}), 404
    
    if category.is_default:
        return jsonify({"error": "Cannot delete default categories"}), 400
        
    db.session.delete(category)
    db.session.commit()
    return jsonify({"message": "Category deleted"}), 200

@app.route('/api/categories/<int:id>', methods=['PUT'])
def update_category(id):
    try:
        data = request.json
        category = Category.query.get(id)
        if not category:
            return jsonify({"error": "Category not found"}), 404
            
        if "name" in data:
            category.name = data["name"]
        if "icon" in data:
            category.icon = data["icon"]
        if "color" in data:
            category.color = data["color"]
        if "cat_type" in data:
            category.cat_type = data["cat_type"]
            
        db.session.commit()
        return jsonify(category.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions/<int:id>', methods=['PUT'])
def update_transaction(id):
    try:
        data = request.json
        txn = Transaction.query.get(id)
        if not txn:
            return jsonify({"error": "Transaction not found"}), 404
        
        # Update category
        if "category_id" in data:
            txn.category_id = data["category_id"]
            category = Category.query.get(data["category_id"])
            if category:
                txn.purpose = category.name  # Backward compat
            txn.categorization_status = 'manual'
        
        if "purpose" in data:
            txn.purpose = data["purpose"]
            # Try to find matching category by name for backward compat
            category = Category.query.filter_by(name=data["purpose"]).first()
            if category:
                txn.category_id = category.id
            txn.categorization_status = 'manual'
            
        if "notes" in data:
            txn.notes = data["notes"]
            
        db.session.commit()
        return jsonify({
            "message": "Transaction updated", 
            "transaction": txn.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# =======================================================
# TRANSACTION CATEGORIZATION ENDPOINTS (NEW)
# =======================================================



@app.route("/api/transactions/bulk-categorize", methods=["POST"])
@token_required
def bulk_categorize_transactions(current_user):
    """Bulk update categories for multiple transactions"""
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        category_id = data.get('category_id')
        
        if not transaction_ids or not category_id:
            return jsonify({"error": "Missing required fields"}), 400
        
        category = Category.query.get(category_id)
        if not category:
            return jsonify({"error": "Category not found"}), 404
        
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.category_id = category_id
                tx.purpose = category.name
                tx.categorization_status = 'manual'
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Updated {updated_count} transactions"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/transactions/bulk-delete", methods=["POST"])
@token_required
def bulk_delete_transactions(current_user):
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        if not transaction_ids:
            return jsonify({"error": "No transactions selected"}), 400
        
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.is_deleted = True
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Deleted {updated_count} transactions"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/transactions/bulk-spam", methods=["POST"])
@token_required
def bulk_mark_spam_transactions(current_user):
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        if not transaction_ids:
            return jsonify({"error": "No transactions selected"}), 400
        
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.is_spam = True
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Marked {updated_count} transactions as spam"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/categories/suggest", methods=["GET"])
@token_required
def suggest_category(current_user):
    """Get suggested category for a merchant"""
    try:
        from model import CategorizationRule
        
        merchant = request.args.get('merchant', '').lower().strip()
        
        if not merchant:
            return jsonify({"suggestion": None}), 200
        
        # Check user rules first
        rule = CategorizationRule.query.filter(
            CategorizationRule.user_id == current_user.id,
            CategorizationRule.merchant_pattern.ilike(f"%{merchant}%")
        ).first()
        
        if rule:
            return jsonify({
                "suggestion": rule.category.to_dict(),
                "source": "user_rule",
                "confidence": "high"
            }), 200
        
        # Check common patterns
        common_patterns = {
            "mcdonald": "Food & Snacks", "burger": "Food & Snacks", "kfc": "Food & Snacks",
            "uber": "Ride / Transport", "careem": "Ride / Transport",
            "netflix": "Entertainment", "spotify": "Entertainment",
            "amazon": "Shopping", "daraz": "Shopping",
            "gym": "Gym & Fitness", "fitness": "Gym & Fitness",
            "hospital": "Healthcare", "pharmacy": "Healthcare",
            "electricity": "Bills & Utilities", "gas": "Bills & Utilities",
        }
        
        for pattern, cat_name in common_patterns.items():
            if pattern in merchant:
                category = Category.query.filter_by(name=cat_name).first()
                if category:
                    return jsonify({
                        "suggestion": category.to_dict(),
                        "source": "common_pattern",
                        "confidence": "medium"
                    }), 200
        
        return jsonify({"suggestion": None}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/categories/monthly", methods=["GET"])
@token_required
def get_monthly_category_totals(current_user):
    """
    Get aggregated category totals for selected month sorted highest to lowest.
    Query Parameter: month=YYYY-MM
    """
    try:
        month_str = request.args.get('month')
        if not month_str:
            return jsonify({"message": "Month is required (YYYY-MM)"}), 400
            
        start_date = datetime.strptime(f"{month_str}-01", "%Y-%m-%d")
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1)
            
        totals = db.session.query(
            Transaction.purpose.label('category'),
            func.sum(case(
                (Transaction.type == 'debit', Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0
            )).label('total')
        ).filter(
            Transaction.date >= start_date,
            Transaction.date < end_date,
            Transaction.is_deleted == False,
            Transaction.purpose.isnot(None),
            Transaction.purpose.ilike('Uncategorized') == False
        ).group_by(Transaction.purpose).all()
        
        sorted_totals = sorted(
            [{"category": t.category, "total": float(t.total or 0)} for t in totals],
            key=lambda x: x["total"],
            reverse=True
        )
        
        return jsonify(sorted_totals), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/categorization-rules", methods=["GET", "POST"])
@token_required
def manage_categorization_rules(current_user):
    """Get or create categorization rules"""
    from model import CategorizationRule
    
    if request.method == "GET":
        try:
            rules = CategorizationRule.query.filter_by(
                user_id=current_user.id
            ).order_by(CategorizationRule.created_at.desc()).all()
            
            return jsonify({
                "count": len(rules),
                "rules": [r.to_dict() for r in rules]
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    else:  # POST
        try:
            data = request.get_json()
            merchant_pattern = data.get('merchant_pattern', '').strip()
            category_id = data.get('category_id')
            
            if not merchant_pattern or not category_id:
                return jsonify({"error": "Missing required fields"}), 400
            
            category = Category.query.get(category_id)
            if not category:
                return jsonify({"error": "Category not found"}), 404
            
            # Check if rule already exists
            existing_rule = CategorizationRule.query.filter_by(
                user_id=current_user.id,
                merchant_pattern=merchant_pattern
            ).first()
            
            if existing_rule:
                existing_rule.category_id = category_id
                db.session.commit()
                return jsonify({
                    "message": "Rule updated",
                    "rule": existing_rule.to_dict()
                }), 200
            
            rule = CategorizationRule(
                user_id=current_user.id,
                merchant_pattern=merchant_pattern,
                category_id=category_id
            )
            db.session.add(rule)
            db.session.commit()
            
            return jsonify({
                "message": "Rule created",
                "rule": rule.to_dict()
            }), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500


@app.route("/api/categorization-rules/<int:id>", methods=["DELETE"])
@token_required
def delete_categorization_rule(current_user, id):
    """Delete a categorization rule"""
    try:
        from model import CategorizationRule
        
        rule = CategorizationRule.query.filter_by(
            id=id,
            user_id=current_user.id
        ).first()
        
        if not rule:
            return jsonify({"error": "Rule not found"}), 404
        
        db.session.delete(rule)
        db.session.commit()
        
        return jsonify({"message": "Rule deleted"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/set_salary', methods=['POST'])
@token_required
def set_salary(current_user):
    try:
        data = request.json
        amount = float(data.get("amount", 0))
        # Default to current month if not provided
        month_str = data.get("month", datetime.now().strftime("%Y-%m"))
        
        budget = Budget.query.filter_by(month=month_str).first()
        if not budget:
             budget = Budget(month=month_str, total_budget=amount)
             db.session.add(budget)
        else:
             budget.total_budget = amount

        # Auto-Calculate 50/30/20 Rule
        needs = amount * 0.50
        wants = amount * 0.30
        savings = amount * 0.20
        
        budget.needs = needs
        budget.wants = wants
        budget.saving = savings
        
        # User Feedback: Budget should be Spending Limit (Needs + Wants)
        # Not the total salary.
        budget.total_budget = needs + wants
            
        db.session.commit()
        return jsonify({
            "message": "Salary updated successfully. Budget set to Spending Limit (Needs + Wants).", 
            "salary": amount, 
            "month": month_str,
            "breakdown": {
                "needs": budget.needs,
                "wants": budget.wants,
                "saving": budget.saving,
                "spending_limit": budget.total_budget
            }
        })

    except Exception as e:
        print(f"❌ Error in set_salary: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/insights', methods=['GET'])
@token_required
def get_insights(current_user):
    """
    Returns the documentation for the exposed APIs for the AI Agent.
    """
    try:
        endpoints = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint.startswith("ai_agent."):
                func = app.view_functions[rule.endpoint]
                docstring = func.__doc__ or "No description available."
                
                # Dynamically generate real sample data by executing the underlying function
                sample_data = None
                if str(rule) == "/api/agent/exposed-endpoints":
                    sample_data = {"message": "Self-referencing docs endpoint."}
                else:
                    try:
                        # Bypass auth decorators to get the raw endpoint function
                        original_func = getattr(func, '__wrapped__', func)
                        
                        # Execute it! (It will use the current request Context's args)
                        res = original_func()
                        
                        if isinstance(res, tuple):
                            res = res[0]
                            
                        if hasattr(res, 'get_json'):
                            sample_data = res.get_json()
                        elif hasattr(res, 'json'):
                            sample_data = res.json
                    except Exception as e:
                        sample_data = {"error": f"Requires specific query parameters or payload. Detail: {str(e)}"}
                
                endpoints.append({
                    "id": str(rule),  # used as key
                    "endpoint": str(rule),
                    "methods": [m for m in rule.methods if m not in ("HEAD", "OPTIONS")],
                    "description": docstring.strip(),
                    "sample_response": sample_data
                })
                
        return jsonify(endpoints), 200

    except Exception as e:
        print(f"❌ Error fetching insights/apis: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/statement/calculate", methods=["POST"])
@token_required
def calculate_statement(current_user):
    from fetchers import fetch_previous_month_statement
    from model import User, Transaction, MonthlyBalance, Budget, Category, DeviceToken, FinancialInsight, StatementAnalysis
    from financial_agent import FinancialAgent
    import json
    data = request.get_json() or {}
    month_param = data.get("month")  # Expected format YYYY-MM
    print("🚀 Triggering Statement Calculation with month:", month_param)
    try:
        # If month provided, adjust date logic
        reference_date = None
        
        if month_param:
            try:
                target_dt = datetime.strptime(month_param, "%Y-%m")
                target_year = target_dt.year
                target_month = target_dt.month
                month_str = month_param
                
                # To fetch statement for target_month (Nov), we need to pretend we are in target_month + 1 (Dec)
                # fetch_previous_month_statement looks for PREVIOUS month relative to input.
                # So we pass a date in the *next* month.
                next_month = target_dt + relativedelta(months=1)
                reference_date = next_month.replace(day=15) # Pick mid-month to be safe
                
            except Exception as e:
                return jsonify({"error": f"Invalid month format: {e}"}), 400
        else:
            # Default to previous month
            today = datetime.now()
            first_of_this = today.replace(day=1)
            prev_month_dt = first_of_this - timedelta(days=1)
            target_year = prev_month_dt.year
            target_month = prev_month_dt.month
            month_str = prev_month_dt.strftime("%Y-%m")
            reference_date = today # Use today so it fetches previous month

        # ---------------------------------------------------------
        # OPTIMIZED FLOW: Check if READ in Database
        # ---------------------------------------------------------
        force_refresh = request.args.get('force', 'false').lower() == 'true'
        existing_analysis = StatementAnalysis.query.filter_by(month=month_str).first()
        is_read = existing_analysis and existing_analysis.reviewed_at
        
        if is_read and not force_refresh:
            print(f"⚡ Loading READ Statement for {month_str} from Database (Cached)...")
            
            # Fetch EXACT transactions linked to this statement
            try:
                stmt_tx_ids = json.loads(existing_analysis.transaction_ids) if existing_analysis.transaction_ids else []
                cached_txs = Transaction.query.filter(Transaction.id.in_(stmt_tx_ids)).order_by(Transaction.date.desc()).all()
            except:
                cached_txs = []
            
            tx_list_response = []
            for t in cached_txs:
                tx_list_response.append({
                    "date": t.date.strftime("%d/%m/%Y"),
                    "amount": t.amount,
                    "description": t.notes or t.sender or "Transaction",
                    "type": t.type,
                    "status": "existing"
                })
                
            return jsonify({
                "message": "Statement loaded from cache (Read).",
                "cached": True,
                "month": month_str,
                "balances": {
                    "opening": existing_analysis.opening_balance,
                    "closing": existing_analysis.closing_balance
                },
                "data": tx_list_response,
                "stats": {
                    "added": 0,
                    "skipped": len(cached_txs),
                    "total": len(cached_txs)
                },
                "processing_status": existing_analysis.processing_status,
                "read_status": "read",
                "balance_matches": True
            }), 200

        # ELSE: UNREAD or NOT EXISTS - Proceed to Email Fetching
        print(f"📧 Fetching UNREAD or NEW statement for {month_str} from Gmail API...")

        # Get manual selections if any
        user_selected_balance = data.get("user_selected_balance")
        is_confirmed = data.get("confirmed", False)

        # Fetch statement PDF/Data (Fresh with GMAIL API)
        result = fetch_previous_month_statement(current_user, reference_date=reference_date)
        if "error" in result:
             print(f"⚠️ Statement Fetch Error: {result.get('error')}")
             return jsonify({
                 "error": result.get("error"),
                 "message": "Could not find bank statement in Gmail via API.",
                 "month": month_str
             }), 400
            
        # 🛡️ MANUAL SELECTION CHECK
        # If we have a balance table and user hasn't confirmed yet, return it for selection
        if not is_confirmed and ("all_email_data" in result):
            print(f"🕵️  Manual Selection Required for {month_str}")
            return jsonify({
                "message": "Multiple balances found or manual verification required.",
                "requires_selection": True,
                "month": month_str,
                "all_email_data": result.get("all_email_data", []),
                "suggested_balances": result.get("balances", {})
            }), 200

        extracted_txs = result.get("transactions", [])
        balances = result.get("balances", {})
        
        # Override with user selection if provided
        if is_confirmed and user_selected_balance is not None:
            print(f"🎯 Using USER SELECTED Closing Balance: {user_selected_balance}")
            balances["closing_balance"] = float(user_selected_balance)
            
        added_count = 0
        skipped_count = 0
        added_tx_data = []
        stmt_transaction_ids = []
        for tx in extracted_txs:
            try:
                tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
            except:
                continue
            
            clean_desc = re.sub(r'^\d{2}\/\d{2}\/\d{4}\s*', '', tx["description"]).strip()
            
            # 🎯 DETEERMINISTIC HASHING for deduplication
            tx_hash = Transaction.generate_deterministic_hash({
                "date": tx_date,
                "amount": tx["amount"],
                "type": tx["type"],
                "description": tx["description"]
            })
            
            exists = Transaction.query.filter_by(transaction_hash=tx_hash).first()
            
            if not exists:
                # 🛡️ FALLBACK: Check for 'Similar' transaction (e.g. from SMS)
                # Checking +/- 2 days to account for statement vs SMS date differences
                exists = Transaction.query.filter(
                    Transaction.amount == tx["amount"],
                    Transaction.type == tx["type"],
                    Transaction.date >= tx_date - timedelta(days=2),
                    Transaction.date <= tx_date + timedelta(days=2)
                ).first()
                if exists:
                    print(f"🔗 Similar transaction found (SMS overlap?): {tx_date.date()} | {tx['amount']}")
                    # Update the existing transaction with the statement's better description/hash if missing
                    if not exists.transaction_hash:
                        exists.transaction_hash = tx_hash
                    # If it was from SMS, it might have source='bank_sms'.
                    # We keep that but link it to the statement by hash.
            
            if exists:
                print(f"⚠️ Duplicate Found: {tx_hash[:10]}...")
                status = "skipped"
                skipped_count += 1
                stmt_transaction_ids.append(exists.id)
            else:
                try:
                    new_tx = Transaction(
                        source="bank_statement",
                        date=tx_date,
                        amount=tx["amount"],
                        type=tx["type"],
                        purpose="Uncategorized",
                        sender="Bank Statement",
                        receiver="Me",
                        notes=clean_desc[:250],
                        transaction_hash=tx_hash
                    )
                    db.session.add(new_tx)
                    db.session.flush() # Get ID
                    status = "added"
                    added_count += 1
                    stmt_transaction_ids.append(new_tx.id)
                except Exception as e:
                    db.session.rollback()
                    print(f"⏭️  Parallel duplicate prevented: {tx_hash[:10]}...")
                    # Find the one that was just inserted by the other thread
                    existing_race = Transaction.query.filter_by(transaction_hash=tx_hash).first()
                    if existing_race:
                        stmt_transaction_ids.append(existing_race.id)
                    status = "skipped"
                    skipped_count += 1
            
            added_tx_data.append({
                "date": tx["date"],
                "amount": tx["amount"],
                "description": clean_desc,
                "status": status
            })
        # Update monthly balance
        open_bal = balances.get("opening_balance", 0)
        close_bal = balances.get("closing_balance", 0)
        mb = MonthlyBalance.query.filter_by(month=month_str).first()
        if not mb:
            mb = MonthlyBalance(month=month_str, opening_balance=open_bal, closing_balance=close_bal, source="bank_statement")
            db.session.add(mb)
        else:
            mb.opening_balance = open_bal
            mb.closing_balance = close_bal
        db.session.commit()

        # ---------------------------------------------------------
        # 1. Populate/Update StatementAnalysis Table
        # ---------------------------------------------------------
        # FIX: Calculate totals based on the ACTUAL transactions in the statement PDF
        # instead of a strict calendar month query. This handles billing cycles that span months.
        
        calc_income = 0.0
        calc_expense = 0.0
        income_bd = {}
        expense_bd = {}
        
        # extracted_txs contains all transactions found in the PDF
        print(f"📊 Starting Analysis Calculation with {len(extracted_txs)} transactions...")
        
        for tx in extracted_txs:
            try:
                # 1. Parse Amount
                raw_amt = str(tx.get("amount", "0"))
                # Remove currency symbols and commas
                clean_amt_str = raw_amt.replace("Rs", "").replace(",", "").strip()
                amt = float(clean_amt_str)
                
                # 2. Parse Type
                t_type = str(tx.get("type", "")).lower().strip()
                desc = tx.get("description", "Uncategorized")
                
                # Debug log for each tx
                print(f"   >> Processing: {desc[:20]}... | Type: {t_type} | Amt: {amt}")

                # 3. Categorize & Sum
                # Handle cases: 'debit', 'dr', 'credit', 'cr'
                # Also check if amount itself is negative (some parsers return -39000 for debit)
                
                is_credit = t_type in ['credit', 'cr', 'deposit']
                is_debit = t_type in ['debit', 'dr', 'withdrawal']
                
                # Fallback: Inference from amount sign if type is ambiguous
                if not is_credit and not is_debit:
                    if amt < 0:
                        is_debit = True
                        amt = abs(amt) # Treat expense as positive magnitude for summation
                    else:
                        # Default assumption? Standard statements usually separate columns.
                        # If truly unknown, maybe skip or assume debit?
                        pass 
                
                # Adjust amount sign for calculation
                # We want total_income (positive) and total_expense (positive magnitude)
                
                cat = "Uncategorized"  # Placeholder
                
                if is_credit:
                    calc_income += abs(amt)
                    income_bd[cat] = income_bd.get(cat, 0) + abs(amt)
                elif is_debit:
                    # Ensure we add positive magnitude to expenses
                    calc_expense += abs(amt) 
                    expense_bd[cat] = expense_bd.get(cat, 0) + abs(amt)
                else:
                    print(f"   ⚠️ Skipping ambiguous transaction: {t_type} | {amt}")
                    
            except Exception as e:
                print(f"⚠️ Error summing transaction for analysis: {e}")
                continue
                
        print(f"📊 Final Calc -> Income: {calc_income}, Expense: {calc_expense}")
                
        calc_surplus = calc_income - calc_expense
        calc_status = "Surplus" if calc_surplus >= 0 else "Deficit"
        
        breakdown_obj = {"income": income_bd, "expenses": expense_bd}
        
        # Update StatementAnalysis
        stmt_analysis = StatementAnalysis.query.filter_by(month=month_str).first()
        if not stmt_analysis:
            stmt_analysis = StatementAnalysis(month=month_str)
            db.session.add(stmt_analysis)
            
        stmt_analysis.opening_balance = open_bal
        stmt_analysis.closing_balance = close_bal
        stmt_analysis.total_income = calc_income
        stmt_analysis.total_expense = calc_expense
        stmt_analysis.net_result = calc_surplus
        stmt_analysis.status = calc_status
        stmt_analysis.breakdown_json = json.dumps(breakdown_obj)
        stmt_analysis.analysis_date = datetime.utcnow()
        stmt_analysis.transaction_ids = json.dumps(stmt_transaction_ids)
        
        if not stmt_analysis.statement_id:
            stmt_analysis.statement_id = month_str
        
        # ----------------------------------------------------------------------
        # OPTIMIZED BALANCE APPLICATION (ON CALCULATION IF UNREAD)
        # Only apply closing balance to AccountBalance if UNREAD
        # ----------------------------------------------------------------------
        balance_update_message = ""
        
        if not stmt_analysis.reviewed_at:
            from model import AccountBalance
            account_balance = AccountBalance.query.filter_by(source='bank').first()
            
            if account_balance:
                old_balance = account_balance.current_balance
                
                # FIX: SET to closing balance (don't add)
                # This replaces the balance with the statement's closing value
                account_balance.current_balance = close_bal
                account_balance.last_updated = datetime.now()
                # FIX: Remove manual lock as Statement is the new Truth
                account_balance.is_manual = False
                
                balance_update_message = f"Balance SET from {old_balance:,.2f} to {close_bal:,.2f}"
                print(f"✅ {balance_update_message}")
            else:
                account_balance = AccountBalance(
                    source='bank',
                    current_balance=close_bal,
                    last_updated=datetime.utcnow(),
                    is_manual=False
                )
                db.session.add(account_balance)
                balance_update_message = f"Balance set to {close_bal:.2f} (Marked Read)"
            
            stmt_analysis.balance_applied = True
            stmt_analysis.reviewed_at = datetime.utcnow()  # MARK AS READ IMMEDIATELY
            print(f"✅ UNREAD Statement {month_str} processed & balance added.")
        else:
            balance_update_message = "Statement already read - no balance update."
        
        db.session.commit()
        print(f"✅ Updated StatementAnalysis for {month_str}")

        # --- TRIGGER AI ANALYSIS ---
        response = {
            "message": "Statement processed",
            "month": month_str,
            "added_transactions": added_count,
            "skipped_transactions": skipped_count,
            "data": added_tx_data,
            "balances": {"opening": open_bal, "closing": close_bal},
            "balance_update": balance_update_message,
            "processing_status": stmt_analysis.processing_status,
            "read_status": "read", # It was just marked read above
            "balance_matches": True
        }
        # ---------------------------------------------------------
        # DRIVE BACKUP TRIGGER
        # ---------------------------------------------------------
        if current_user.google_refresh_token:
             from drive_utils import get_drive_service, ensure_folder_path, upload_json
             try:
                 print(f"☁️ Backing up {month_str} to Drive...")
                 service = get_drive_service(current_user)
                 if service:
                     backup_payload = {
                         "month": month_str,
                         "transactions": extracted_txs,
                         "balances": balances,
                         "summary": {
                            "opening": open_bal,
                            "closing": close_bal,
                            "expense": mb.expense if mb.expense else 0.0,
                            "savings": mb.savings if mb.savings else 0.0
                         }
                     }
                     folder_id = ensure_folder_path(service, ["Aurestra Finance", month_str])
                     if folder_id:
                         upload_json(service, folder_id, "statement.json", backup_payload)
             except Exception as drive_err:
                 print(f"⚠️ Drive Backup Failed: {drive_err}")

        return jsonify(response)
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/sms/last-sync", methods=["GET"])
@token_required
def get_last_sms_sync(current_user):
    try:
        # Find the VERY LATEST transaction created via SMS
        latest_tx = Transaction.query.filter(
            Transaction.source.in_(['bank_sms', 'sms'])
        ).order_by(Transaction.created_at.desc()).first()
        
        if latest_tx:
            return jsonify({
                "last_sync_time": latest_tx.created_at.isoformat(),
                "source": "database"
            })
        else:
            # DB is empty. Return null so frontend can decide the default (e.g. Jan 31st)
            return jsonify({
                "last_sync_time": None,
                "source": "empty_db"
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sms/process", methods=["POST"])
@token_required
def api_process_sms(current_user):
    from sms_parser import process_bank_sms
    
    try:
        data = request.get_json() or {}
        message = data.get("message")
        sender = data.get("sender", "BAHL")
        
        if not message:
            return jsonify({"error": "Message content is required"}), 400
            
        print(f"📩 Processing SMS from {sender}: {message[:50]}...")
        
        # Process the SMS
        # result is (transaction, is_new)
        tx, is_new = process_bank_sms(message, sender)
        
        if not tx:
             # Check if it was ignored or failed
             from sms_parser import BankAlhabibSMSParser
             if not BankAlhabibSMSParser.is_transaction_sms(message):
                 return jsonify({
                     "status": "ignored", 
                     "message": "SMS is not a transaction"
                 }), 200
             else:
                 return jsonify({
                     "status": "failed",
                     "message": "Could not parse SMS"
                 }), 400
             
        # Verification: Check if it actually saved
        tx_data = {
            "id": tx.id,
            "amount": tx.amount,
            "type": tx.type,
            "description": tx.notes,
            "date": tx.date.strftime("%Y-%m-%d %H:%M:%S"),
            "is_new": is_new
        }
        return jsonify({
            "status": "success",
            "message": "Transaction recorded" if is_new else "Transaction already exists",
            "transaction": tx_data
        }), 201
            
        return jsonify(result), 200

    except Exception as e:
        print(f"❌ SMS API Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/calculate-summary", methods=["POST"])
def calculate_summary_endpoint():
    try:
        data = request.get_json() or {}
        month_str = data.get("month", datetime.now().strftime("%Y-%m"))
        
        dt = datetime.strptime(month_str, "%Y-%m")
        
        # Live Calculation
        total_income = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit'
        ).scalar() or 0.0
        
        total_expense = db.session.query(
            func.sum(case(
                (Transaction.type == 'debit', Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0
            ))
        ).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month
        ).scalar() or 0.0
        
        total_savings = total_income - total_expense
        
        # Update MonthlyBalance in DB for persistence
        summary = MonthlyBalance.query.filter_by(month=month_str).first()
        if not summary:
            summary = MonthlyBalance(
                month=month_str, 
                opening_balance=0, 
                closing_balance=0,
                source="combined"  # Explicitly set source
            )
            db.session.add(summary)
        
        # Update fields
        summary.expense = total_expense
        summary.savings = total_savings
        summary.closing_balance = summary.opening_balance + total_income - total_expense
        # Ensure source is set if updating existing
        if not summary.source:
             summary.source = "combined"
             
        db.session.commit()

        # --- TRIGGER AI ANALYSIS ---
        try:
            # Need finding target_year/month if not defined?
            # Assuming dt.year/dt.month work
            print(f"🤖 Triggering Financial Agent for {dt.year}-{dt.month:02d}...")
            agent = FinancialAgent()
            agent.analyze_month(dt.year, dt.month)
        except Exception as e:
            print(f"❌ Financial Agent Error: {e}")
            # Don't fail the whole request if analysis fails, just log it.

        # Backup to Google Drive (if enabled)
        # Note: 'extracted_txs', 'balances', etc are not available here easily unless recalculated
        # But user wants summary updated.
        
        response_payload = {
            "message": f"Statement processed for {month_str}",
            "data": {
                "month": month_str,
                "summary": {
                    "expense": summary.expense,
                    "savings": summary.savings
                }
            }
        }
        return jsonify(response_payload), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



# -------------------------
# STARTUP
# -------------------------
def seed_categories():
    """Seed the database with default categories."""
    defaults = [
        {"name": "Food & Snacks", "icon": "food", "color": "#FF6B6B", "type": "spending"},
        {"name": "Movies", "icon": "movie", "color": "#EC4899", "type": "spending"},
        {"name": "Tea", "icon": "coffee", "color": "#F59E0B", "type": "spending"},
        {"name": "Therapy", "icon": "brain", "color": "#8B5CF6", "type": "spending"},
        {"name": "Uber", "icon": "car", "color": "#4ECDC4", "type": "spending"},
        {"name": "Audible Subscription", "icon": "headphones", "color": "#A78BFA", "type": "spending"},
        {"name": "Google One Subscription", "icon": "google", "color": "#3B82F6", "type": "spending"},
        {"name": "Ride / Transport", "icon": "car", "color": "#4ECDC4", "type": "spending"},
        {"name": "Bills & Utilities", "icon": "receipt", "color": "#3B82F6", "type": "spending"},
        {"name": "Shopping", "icon": "shopping", "color": "#A78BFA", "type": "spending"},
        {"name": "Healthcare", "icon": "hospital", "color": "#10B981", "type": "spending"},
        {"name": "Education", "icon": "school", "color": "#F59E0B", "type": "spending"},
        {"name": "Groceries", "icon": "cart", "color": "#10B981", "type": "spending"},
        {"name": "Personal Care", "icon": "sparkles", "color": "#8B5CF6", "type": "spending"},
        {"name": "Online Services", "icon": "web", "color": "#3B82F6", "type": "spending"},
        {"name": "Gym & Fitness", "icon": "dumbbell", "color": "#FF6B6B", "type": "spending"},
        {"name": "Income", "icon": "cash", "color": "#10B981", "type": "income"},
        {"name": "Bonus", "icon": "gift", "color": "#F59E0B", "type": "income"},
        {"name": "Investment", "icon": "trending-up", "color": "#3B82F6", "type": "income"},
        {"name": "Uncategorized", "icon": "help-circle", "color": "#64748B", "type": "both"},
    ]
    
    with app.app_context():
        for cat_data in defaults:
            cat = Category.query.filter_by(name=cat_data["name"]).first()
            if not cat:
                cat = Category(
                    name=cat_data["name"],
                    icon=cat_data["icon"],
                    color=cat_data["color"],
                    cat_type=cat_data["type"],
                    is_default=True
                )
                db.session.add(cat)
            else:
                # Update type for existing default categories if needed
                cat.cat_type = cat_data["type"]
        db.session.commit()
        print("✅ Categories seeded")

# Consolidate: /api/insights moved to line 2019

@app.route("/api/insights/generate", methods=["POST"])
@token_required
def generate_insights(current_user):
    try:
        from datetime import datetime, date, timedelta
        from model import FinancialInsight
        from financial_agent import FinancialAgent
        from fetchers import fetch_latest_bank_email
        
        data = request.get_json() or {}
        month_str = data.get("month") # YYYY-MM
        
        # 1. Sync Data first (User requirement: "open bank statement...")
        print("🔄 Manual Trigger: Syncing Bank Data...")
        try:
            fetch_latest_bank_email()
            # We can also fetch wallet/easypaisa if needed
        except Exception as e:
            print(f"⚠️ Sync failed: {e}")
            # Continue anyway, maybe transactions are already there
            
        # 2. Determine target month
        target_year = None
        target_month = None
        
        if month_str:
            dt = datetime.strptime(month_str, "%Y-%m")
            target_year = dt.year
            target_month = dt.month
        else:
            # Default to PREVIOUS month as per user description ("january looks at december")
            # But allowing "Current Month" might be useful?
            # User said: "calculate the monthly summary for THAT month."
            # If I am in Jan, and I click button, do I want Jan summary (partial) or Dec (complete)?
            # User said: "For example, if it's January, it should look at all of December..."
            # So default = Previous Month.
            today = date.today()
            first = today.replace(day=1)
            prev = first - timedelta(days=1)
            target_year = prev.year
            target_month = prev.month
            
        # 3. Use Financial Agent
        agent = FinancialAgent()
        agent.analyze_month(target_year, target_month)
        
        return jsonify({
            "message": f"Insights generated for {target_year}-{target_month:02d}",
            "month": f"{target_year}-{target_month:02d}"
        }), 200

    except Exception as e:
        print(f"❌ Generation failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/reports/statement/mark-read", methods=["POST"])
@token_required
def mark_statement_as_read(current_user):
    from model import StatementAnalysis, AccountBalance
    data = request.get_json() or {}
    month_str = data.get("month")
    
    if not month_str:
        return jsonify({"error": "Month is required"}), 400
        
    stmt = StatementAnalysis.query.filter_by(month=month_str).first()
    if not stmt:
        return jsonify({"error": "Statement not found"}), 404
    
    # ADDITIVE BALANCE LOGIC (MOVED HERE)
    # Only if NOT already applied
    if not stmt.balance_applied:
        close_bal = stmt.closing_balance
        account_balance = AccountBalance.query.filter_by(source='bank').first()
        
        if account_balance:
            old_balance = account_balance.current_balance
            
            # FIX: SET Balance (Overwrite)
            account_balance.current_balance = close_bal
            account_balance.last_updated = datetime.utcnow()
            account_balance.is_manual = False # FIX: Unlock manual
            
            print(f"💰 [mark-read] SET Balance: {old_balance} -> {close_bal}")
        else:
            account_balance = AccountBalance(
                source='bank',
                current_balance=close_bal,
                last_updated=datetime.utcnow(),
                is_manual=False
            )
            db.session.add(account_balance)
            print(f"💰 [mark-read] SET Balance: {close_bal}")
            
        stmt.balance_applied = True

    if not stmt.reviewed_at:
        stmt.reviewed_at = datetime.utcnow()
        print(f"✅ Marked statement {month_str} as READ")
    
    db.session.commit()
    return jsonify({
        "message": "Statement marked as read and balance updated", 
        "balance_applied": stmt.balance_applied,
        "reviewed_at": stmt.reviewed_at.isoformat()
    })


@app.route("/api/transactions", methods=["POST"])
@token_required
def create_transaction(current_user):
    try:
        data = request.get_json()
        
        amount = float(data.get('amount', 0))
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400
            
        t_type = data.get('type', 'debit') # 'debit' or 'credit'
        purpose = data.get('category', 'Uncategorized')
        notes = data.get('notes', '')
        date_str = data.get('date')
        
        tx_date = datetime.utcnow()
        if date_str:
            try:
                tx_date = datetime.strptime(date_str, "%Y-%m-%d")
            except:
                pass

        new_tx = Transaction(
            source='manual',
            date=tx_date,
            amount=amount,
            type=t_type,
            purpose=purpose,
            sender='Manual Entry',
            receiver='Me' if t_type == 'credit' else 'Merchant',
            notes=notes,
            categorization_status='confirmed'
        )
        
        db.session.add(new_tx)
        db.session.flush()  # Get transaction ID
        
        # FIX: UPDATE ACCOUNT BALANCE ALWAYS
        balance = AccountBalance.query.filter_by(source='bank').first()
        if not balance:
            balance = AccountBalance.query.first()
            
        if balance:
            if t_type == 'credit':
                balance.current_balance += amount
                print(f"💰 Balance increased by {amount}: {balance.current_balance}")
            elif t_type == 'debit':
                balance.current_balance -= amount
                print(f"💸 Balance decreased by {amount}: {balance.current_balance}")
            
            balance.last_updated = datetime.now()
        
        db.session.commit()
        
        return jsonify({
            "message": "Transaction added successfully",
            "transaction": {
                "id": new_tx.id,
                "amount": new_tx.amount,
                "purpose": new_tx.purpose,
                "date": new_tx.date.strftime("%Y-%m-%d"),
                "type": new_tx.type
            },
            "new_balance": balance.current_balance if balance else None
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Transaction creation error: {e}")
        return jsonify({"error": str(e)}), 500

# --- Scheduler & Backup ---
from flask_apscheduler import APScheduler
from backup_manager import BackupManager

scheduler = APScheduler()
backup_manager = BackupManager()

def scheduled_backup_job():
    with app.app_context():
        # NOTE: Scheduler runs in its own context, so we re-push app context
        backup_manager.perform_backup()

# --- Gunicorn/Production Entry Point ---
# This block runs when Gunicorn imports 'app'
with app.app_context():
    db.create_all()
    # verify_columns(app) # Optional: if you have the helper
    # seed_categories() # Good to have seeded
    # print("✅ [Prod/Dev] Database tables ensured.")
    
    # Init Backup
    backup_manager.init_app(app)
    
    # Init Scheduler (only if not already running to avoid double-init on reloads)
    if not scheduler.running:
        # Schedule Daily Backup at 12:00 AM (Midnight)
        scheduler.add_job(id='daily_backup', func=scheduled_backup_job, trigger='cron', hour=0, minute=0)
        scheduler.init_app(app)
        scheduler.start()
        print("⏰ [System] Backup Scheduler Started (Daily @ 00:00)")

@app.route("/api/backup/trigger", methods=["POST"])
@token_required
def trigger_manual_backup(current_user):
    """Manually trigger the backup process"""
    try:
        # Run asynchronously in a real app, but for now synchronous is okay or use thread
        # Using a thread to avoid blocking response
        import threading
        
        def run_backup():
            with app.app_context():
                backup_manager.perform_backup()
                
        thread = threading.Thread(target=run_backup)
        thread.start()
        
        return jsonify({"message": "Backup started in background"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_categories()
    # Use 0.0.0.0 to allow access from other devices/emulator
    app.run(host='0.0.0.0', port=5000, debug=True)
