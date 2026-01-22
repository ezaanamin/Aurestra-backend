from flask import jsonify, request
from fetchers import fetch_latest_bank_email, fetch_latest_wallet_email
from database import app, db  # Import app from database.py
from model import MonthlyBalance, Transaction, Budget, AccountBalance, SavingsGoal, Category
from datetime import datetime, date, timedelta
import os
import json
from dateutil.relativedelta import relativedelta
import re
from werkzeug.utils import secure_filename
from time import time
from fetchers import (
    fetch_latest_bank_email,
    fetch_latest_wallet_email,
    fetch_and_save_easypaisa_emails,
    calculate_combined_summary
)
from drive_utils import get_drive_service, ensure_folder_path, upload_json
from fcm_utils import send_push_to_all
from sqlalchemy import func
from sqlalchemy import desc
from sqlalchemy import extract
from sms_parser import process_bank_sms, BankAlhabibSMSParser
import jwt
import smtplib
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

# Config is already loaded in database.py



# JWT Secret
# JWT Secret
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or 'default_dev_secret_change_me'

# Email Config (Gmail)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
# Ideally these should be env vars
SMTP_EMAIL = os.getenv('SMTP_EMAIL') 
# NOTE: User needs to provide App Password in .env or we assume it is the Wallet email
SMTP_PASSWORD = os.getenv("WALLET_APP_PASSWORD") or os.getenv("wallet_APP_PASSWORD") or os.getenv("APP_PASSWORD") or os.getenv("OPTP_APP_PASSWORD") 

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
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = User.query.get(data['user_id'])
            if not current_user:
                return jsonify({'message': 'User invalid!'}), 401
        except:
            return jsonify({'message': 'Token is invalid!'}), 401
            
        return f(current_user, *args, **kwargs)
    
    return decorated

def generate_otp():
    """Generates a secure 6-digit OTP."""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def send_otp_email(to_email, otp_code):
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "Your Aurestra Verification Code"
        
        # Plain text fallback
        text = f"Your Aurestra verification code is: {otp_code}\n\nValid for 5 minutes."
        
        # HTML Version
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
              <h2 style="color: #3B82F6;">Aurestra</h2>
              <p>Hello,</p>
              <p>Your verification code for secure login is:</p>
              <div style="background-color: #f8fafc; padding: 15px; text-align: center; border-radius: 5px; font-size: 24px; font-weight: bold; letter-spacing: 5px; color: #1e293b;">
                {otp_code}
              </div>
              <p style="color: #666; font-size: 14px; margin-top: 20px;">This code is valid for 5 minutes. Do not share this code with anyone.</p>
              <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
              <p style="color: #999; font-size: 12px; text-align: center;">&copy; 2026 Aurestra Finance. All rights reserved.</p>
            </div>
          </body>
        </html>
        """
        
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_EMAIL, to_email, text)
        server.quit()
        return True
    except Exception as e:
        print(f"⚠️ Failed to send email: {e}")
        return False

# Simple in-memory rate limiting
from collections import defaultdict
from time import time

request_counts = defaultdict(list)

def rate_limit_check(key, limit, window):
    """Simple rate limiting: limit requests per window (seconds)"""
    now = time()
    request_counts[key] = [req_time for req_time in request_counts[key] if now - req_time < window]
    
    if len(request_counts[key]) >= limit:
        return False
    
    request_counts[key].append(now)
    return True

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
    return "Backend Running ✅"

# -------------------------
# AUTH ROUTES
# -------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password') 
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        user = User(email=email, password_hash=password, full_name="Ezaan Amin")
        db.session.add(user)
        db.session.commit()
    else:
        # DEBUG LOGS (Removed for deployment)
        # print(f"DEBUG LOGIN: Request for {email}")
        # print(f"DEBUG LOGIN: Input Pass: '{password}'")
        # print(f"DEBUG LOGIN: Stored Pass: '{user.password_hash}'")

        if user.password_hash != password:
             return jsonify({'message': 'Invalid credentials'}), 401

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
    server_auth_code = data.get('serverAuthCode')
    
    if not id_token_str:
        return jsonify({'message': 'Missing ID token'}), 400

    try:
        # Verify ID Token
        id_info = id_token.verify_oauth2_token(
            id_token_str, 
            requests.Request(), 
            os.getenv('GOOGLE_WEB_CLIENT_ID')
        )

        email = id_info.get('email')
        google_id = id_info.get('sub')
        name = id_info.get('name')
        picture = id_info.get('picture') # Get Google Avatar
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if not user:
            # Create new user
            user = User(
                email=email,
                full_name=name,
                password_hash="GOOGLE_AUTH", # Placeholder
                google_id=google_id,
                google_email=email,
                avatar_url=picture # Save Avatar
            )
            db.session.add(user)
            db.session.commit()
        else:
            # Update existing user
            if not user.google_id:
                user.google_id = google_id
                user.google_email = email
            
            # Always update avatar if available and different?
            # Or just if missing? Let's override to keep it fresh from Google.
            if picture:
                user.avatar_url = picture
                
            # Update name if missing
            if name and not user.full_name:
                user.full_name = name
                
            db.session.commit()
            
        # Handle Server Auth Code (Get Refresh Token)
        if server_auth_code:
            try:
                # Exchange auth code for credentials
                client_id = os.getenv('GOOGLE_WEB_CLIENT_ID')
                client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
                
                print(f"DEBUG: Client ID Prefix: {client_id[:5] if client_id else 'None'}...")
                print(f"DEBUG: Client Secret Prefix: {client_secret[:3] if client_secret else 'None'}...")
                
                client_config = {
                    "web": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
                
                flow = Flow.from_client_config(
                    client_config,
                    scopes=[
                        'https://www.googleapis.com/auth/drive.file',
                        'openid',
                        'https://www.googleapis.com/auth/userinfo.email',
                        'https://www.googleapis.com/auth/userinfo.profile'
                    ],
                    redirect_uri=''
                )
                
                flow.fetch_token(code=server_auth_code)
                credentials = flow.credentials
                
                if credentials.refresh_token:
                    user.google_refresh_token = credentials.refresh_token
                    db.session.commit()
                    print(f"✅ [AUTH] Saved Refresh Token for {email}")
                
            except Exception as e:
                print(f"❌ [AUTH] Failed to exchange auth code: {str(e)}")
                # Should we fail the login? Maybe not, but Drive backup won't work.

        # Generate OTP for 2FA
        otp = generate_otp()
        from datetime import timezone
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        
        user.otp_code = otp
        user.otp_expiry = expiry
        db.session.commit()
        
        # Send OTP (Soft Fail)
        try:
            send_otp_email(user.email, otp)
        except Exception as e:
            print(f"⚠️ [AUTH] Email sending failed: {e}")
            # Proceed anyway, developer can check DB or logs for OTP
        
        return jsonify({
            'message': 'Please verify OTP',
            'otp_required': True,
            'email': email
        }), 200

    except ValueError as e:
        return jsonify({'message': f'Invalid token: {str(e)}'}), 401
    except Exception as e:
        print(f"Google Login Error: {e}")
        return jsonify({'message': 'Internal server error'}), 500

@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json()
    email = data.get('email')
    otp = data.get('otp')
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        return jsonify({'message': 'User not found'}), 404
        
    if user.otp_code != otp:
        return jsonify({'message': 'Invalid OTP'}), 400
        
    if datetime.utcnow() > user.otp_expiry:
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
            tx_count = Transaction.query.count()
            goals_count = SavingsGoal.query.count()
            categories_count = db.session.query(func.count(func.distinct(Transaction.purpose))).scalar()
            
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
        balance.last_updated = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            "message": "Balance updated manually",
            "account": balance.to_dict()
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
                        bank_acc.last_updated = datetime.utcnow()
                    
                    # SAVE TRANSACTIONS (Always process transactions, just don't overwrite balance if manual)
                    extracted_txs = bank_data.get("transactions", [])
                    new_tx_count = 0
                    for tx in extracted_txs:
                        try:
                            tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
                        except:
                            tx_date = datetime.utcnow()

                        exists = Transaction.query.filter(
                            Transaction.date == tx_date,
                            Transaction.amount == tx["amount"],
                            Transaction.type == tx["type"]
                        ).first()
                        

                        if not exists:
                            new_tx = Transaction(
                                source="bank",
                                date=tx_date,
                                amount=tx["amount"],
                                type=tx["type"],
                                purpose="Uncategorized",
                                sender="Bank Statement",
                                receiver="Me",
                                notes=tx["description"][:250]
                            )
                            db.session.add(new_tx)
                            new_tx_count += 1
                    
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
                # Find ONLY SMS transactions since the last time the bank balance was updated from email/statement
                # If last_updated is missing (first run), we use start of month
                effective_cutoff = acc.last_updated if acc.last_updated else cutoff_date
                
                live_txs = Transaction.query.filter(
                    Transaction.source == 'bank_sms',
                    Transaction.date > effective_cutoff
                ).all()
                
                adjustment = 0.0
                for tx in live_txs:
                    if tx.type == 'credit':
                        adjustment += tx.amount
                    elif tx.type == 'debit':
                        adjustment -= tx.amount
                
                # TOTAL DYNAMIC BALANCE Calculation
                # Balance is Truth (DB) + SMS Adjustment
                live_balance = acc.current_balance + adjustment
                
                # Update response
                acc_dict['balance'] = live_balance
                acc_dict['statement_base'] = acc.current_balance
                acc_dict['live_adjustment'] = adjustment
                acc_dict['savings_reduction'] = 0.0 # Deprecated, handled via transactions
                
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
@app.route("/api/expenses/total", methods=["GET"])
@token_required
def get_total_expenses(current_user):
    try:
        dt = datetime.now()
        
        # Calculate Expense Sum directly from Transactions
        # This is the single source of truth for "Expenses"
        total_expense = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'debit'
        ).scalar() or 0.0
        
        return jsonify({
            "month": dt.strftime("%Y-%m"),
            "total_expense": total_expense
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
        dynamic_expense = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'debit'
        ).scalar() or 0.0

        # Dynamic Income Calculation 
        dynamic_income_tx = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit'
        ).scalar() or 0.0
        
        # Check budget for income override
        budget_entry = Budget.query.filter_by(month=current_month).first()
        final_income = dynamic_income_tx
        if budget_entry and budget_entry.total_budget > 0:
            final_income = budget_entry.total_budget
            
        final_savings = final_income - dynamic_expense

        # 2. Update/Create MonthlyBalance persistence
        summary = MonthlyBalance.query.filter_by(month=current_month).first()
        
        if not summary:
            # Create new
            summary = MonthlyBalance(
                source="auto-dynamic",
                month=current_month,
                opening_balance=0,
                closing_balance=0,
                expense=dynamic_expense,
                # income=final_income, # REMOVED
                savings=final_savings,
                fetched_at=datetime.utcnow()
            )
            db.session.add(summary)
        else:
            # Update existing
            summary.expense = dynamic_expense
            # summary.income = final_income # REMOVED
            summary.savings = final_savings
            summary.fetched_at = datetime.utcnow()
            
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
            "fetched_at": datetime.utcnow().strftime("%d %b %Y %H:%M:%S")
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
            fresh_expense = db.session.query(func.sum(Transaction.amount)).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.type == 'debit'
            ).scalar() or 0.0
            
            fresh_income = db.session.query(func.sum(Transaction.amount)).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.type == 'credit'
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
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    func.date(Transaction.date) == target_date,
                    Transaction.type == 'debit'
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
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    extract('year', Transaction.date) == target_date.year,
                    extract('month', Transaction.date) == target_date.month,
                    Transaction.type == 'debit'
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
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    extract('year', Transaction.date) == year,
                    extract('month', Transaction.date) == i,
                    Transaction.type == 'debit'
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
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    extract('year', Transaction.date) == year,
                    Transaction.type == 'debit'
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
        Transaction.purpose.isnot(None),
        Transaction.amount > 0,
        Transaction.type == 'debit'
    )

    if period == 'week':
        start_date = datetime.now() - timedelta(days=7)
        query = query.filter(Transaction.date >= start_date)
    elif period == 'month':
        latest_date = db.session.query(func.max(Transaction.date)).scalar()
        if not latest_date:
            return jsonify([]), 200
        latest_year = latest_date.year
        latest_month = latest_date.month
        query = query.filter(
            extract('year', Transaction.date) == latest_year,
            extract('month', Transaction.date) == latest_month
        )
    elif period == 'year':
        latest_date = db.session.query(func.max(Transaction.date)).scalar()
        if not latest_date:
            return jsonify([]), 200
        latest_year = latest_date.year
        query = query.filter(extract('year', Transaction.date) == latest_year)
    # 'all' doesn't need extra filtering
    
    categories = (
        query.group_by(Transaction.purpose)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10) # Increased limit for better breakdown
        .all()
    )

    result = [
        {"category": cat.category, "total_spent": cat.total_spent}
        for cat in categories
    ]
    return jsonify(result), 200

# -------------------------
# SMS ROUTES (NEW) 🎉
# -------------------------

@app.route("/api/sms/process", methods=["POST"])
def process_sms():
    """Process bank SMS and create transaction"""
    try:
        if not rate_limit_check('sms_process', 20, 60):
            return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({"error": "SMS message required"}), 400
        
        message = data.get('message')
        sender = data.get('sender', 'BAHL')
        
        if not message or len(message.strip()) < 10:
            return jsonify({"error": "Invalid SMS message"}), 400
        
        # result is now (transaction, is_new)
        transaction, is_new = process_bank_sms(message, sender)
        
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
        if not rate_limit_check('sms_test', 10, 60):
            return jsonify({"error": "Rate limit exceeded"}), 429
        
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
def process_batch_sms():
    """Process multiple SMS messages"""
    try:
        if not rate_limit_check('sms_batch', 5, 60):
            return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'messages' not in data:
            return jsonify({"error": "Messages array required"}), 400
        
        messages = data.get('messages', [])
        
        if not isinstance(messages, list) or len(messages) == 0:
            return jsonify({"error": "Messages must be non-empty array"}), 400
        
        if len(messages) > 500:
            return jsonify({"error": "Maximum 500 messages per batch"}), 400
        
        results = {
            'total': len(messages),
            'created': 0,
            'skipped': 0,
            'failed': 0,
            'transactions': [],
            'errors': []
        }
        
        for idx, msg_data in enumerate(messages):
            try:
                message = msg_data.get('message')
                sender = msg_data.get('sender', 'BAHL')
                
                if not message:
                    results['failed'] += 1
                    results['errors'].append({'index': idx, 'error': 'Empty message'})
                    continue
                
                if not BankAlhabibSMSParser.is_transaction_sms(message):
                    results['skipped'] += 1
                    continue
                
                # result is now (transaction, is_new)
                transaction, is_new = process_bank_sms(message, sender)
                
                if transaction:
                    if is_new:
                        results['created'] += 1
                    else:
                        results['skipped'] += 1
                        
                    results['transactions'].append({
                        'id': transaction.id,
                        'type': transaction.type,
                        'amount': transaction.amount,
                        'purpose': transaction.purpose,
                        'is_new': is_new
                    })
                else:
                    results['failed'] += 1
                    error_msg = f"Parse failed (No regex match)"
                    print(f"❌ {error_msg}: {message}")
                    results['errors'].append({
                        'index': idx, 
                        'error': error_msg, 
                        'message': message  # Include the failing message in the response
                    })
                    
            except Exception as e:
                results['failed'] += 1
                results['errors'].append({'index': idx, 'error': str(e)})
        
        print(f"✅ Batch: {results['created']} created, {results['skipped']} skipped, {results['failed']} failed")
        
        return jsonify(results), 200
        
    except Exception as e:
        return jsonify({"error": "Batch processing failed"}), 500

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

@app.route("/api/transactions/uncategorized", methods=["GET"])
@token_required
def get_uncategorized_transactions(current_user):
    """Get all transactions with categorization_status = 'pending'"""
    try:
        transactions = Transaction.query.filter_by(
            categorization_status='pending'
        ).order_by(Transaction.date.desc()).all()
        
        return jsonify({
            "count": len(transactions),
            "transactions": [t.to_dict() for t in transactions]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        
        return jsonify({
            "message": f"Updated {updated_count} transactions",
            "updated_count": updated_count
        }), 200
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
def get_monthly_insight(current_user):
    try:
        month_str = request.args.get('month')
        if not month_str:
            # Default to current month
            today = datetime.now()
            month_str = today.strftime("%Y-%m")
            
        insight = FinancialInsight.query.filter_by(month=month_str).first()
        
        if not insight:
            # Try previous month if current has no insight yet
            today = datetime.now()
            first = today.replace(day=1)
            last_month = first - timedelta(days=1)
            last_month_str = last_month.strftime("%Y-%m")
            insight = FinancialInsight.query.filter_by(month=last_month_str).first()
            
            if not insight:
                return jsonify({"message": "No insight found", "data": None}), 404
        
        return jsonify({
            "message": "Insight retrieved",
            "data": insight.to_dict()
        }), 200

    except Exception as e:
        print(f"❌ Error fetching insight: {e}")
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
        print(f"📧 Fetching UNREAD or NEW statement for {month_str} from Email...")

        # Fetch statement PDF/Data (Fresh)
        result = fetch_previous_month_statement(reference_date=reference_date)
        if "error" in result:
            return jsonify(result), 404
        extracted_txs = result.get("transactions", [])
        balances = result.get("balances", {})
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
            exists = Transaction.query.filter(
                func.date(Transaction.date) == tx_date.date(),
                Transaction.amount == tx["amount"],
                Transaction.type == tx["type"]
            ).first()
            
            if exists:
                print(f"⚠️ Duplicate Found: {tx_date} | {tx['amount']}")
                status = "skipped"
                skipped_count += 1
                stmt_transaction_ids.append(exists.id)
            else:
                new_tx = Transaction(
                    source="bank_statement",
                    date=tx_date,
                    amount=tx["amount"],
                    type=tx["type"],
                    purpose="Uncategorized",
                    sender="Bank Statement",
                    receiver="Me",
                    notes=clean_desc[:250]
                )
                db.session.add(new_tx)
                db.session.flush() # Get ID
                status = "added"
                added_count += 1
                stmt_transaction_ids.append(new_tx.id)
            
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
                new_balance = old_balance + close_bal
                account_balance.current_balance = new_balance
                account_balance.last_updated = datetime.utcnow()
                account_balance.is_manual = False
                balance_update_message = f"Balance increased by {close_bal:.2f} (Marked Read)"
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
        
        total_expense = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'debit'
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
            print(f"🤖 Triggering Financial Agent for {target_year}-{target_month:02d}...")
            agent = FinancialAgent()
            agent.analyze_month(target_year, target_month)
        except Exception as e:
            print(f"❌ Financial Agent Error: {e}")
            # Don't fail the whole request if analysis fails, just log it.

        # Backup to Google Drive (if enabled)
        backup_status = "skipped"
        if current_user.google_refresh_token:
             # DRY import (better to move to top, but local for now)
             from drive_utils import get_drive_service, ensure_folder_path, upload_json
             try:
                 print(f"☁️ Backing up {month_str} to Drive...")
                 service = get_drive_service(current_user)
                 if service:
                     # Create payload consistent with frontend expectation
                     backup_payload = {
                         "month": month_str,
                         "transactions": extracted_txs,
                         "balances": balances,
                         "summary": {
                            "opening": selected_opening,
                            "closing": selected_closing,
                            "expense": new_summ.expense,
                            "savings": new_summ.savings
                         }
                     }
                     folder_id = ensure_folder_path(service, ["Aurestra Finance", month_str])
                     if folder_id:
                         upload_json(service, folder_id, "statement.json", backup_payload)
             except Exception as e:
                 print(f"⚠️ Drive Backup Failed: {e}")

        response_payload = {
            "message": f"Statement processed for {month_str}",
            "transactions_added": added_count,
            "transactions_skipped": skipped_count,
            "data": {
                "month": month_str,
                "transactions": extracted_txs,
                "balances": balances,
                "summary": {
                    "opening": selected_opening,
                    "closing": selected_closing,
                    "expense": new_summ.expense,
                    "savings": new_summ.savings
                }
            }
        }
        return jsonify(response_payload), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

def get_statement_report(current_user):
    # ... existing implementation ...
    pass


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

@app.route("/api/insights", methods=["GET"])
@token_required
def get_insights(current_user):
    from model import FinancialInsight
    from sqlalchemy import desc
    insights = FinancialInsight.query.order_by(desc(FinancialInsight.month)).all()
    return jsonify([i.to_dict() for i in insights]), 200

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
            new_balance = old_balance + close_bal
            account_balance.current_balance = new_balance
            account_balance.last_updated = datetime.utcnow()
            account_balance.is_manual = False
            print(f"💰 [mark-read] ADDED Balance: {old_balance} + {close_bal} = {new_balance}")
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("✅ Tables ensured in database")
        seed_categories()
    # Use 0.0.0.0 to allow access from other devices/emulator
    app.run(host='0.0.0.0', port=5000, debug=True)