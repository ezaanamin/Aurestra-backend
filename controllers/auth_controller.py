# controllers/auth_controller.py  —  Parse request → call service → return response

import os
from datetime import datetime
from flask import request, jsonify, current_app
import jwt as pyjwt
import requests as http_requests

from services.auth_service import (
    verify_google_id_token,
    check_centralized_auth,
    get_or_create_user,
    issue_jwt,
    check_auth_api_status,
    register_email_user,
    login_email_user,
    verify_email_token,
    resend_verification_email,
    generate_password_reset,
    reset_password,
)
from model import User
from database import db, limiter  # SECURITY FIX (HIGH-1): import rate limiter

AUTH_API_URL = os.getenv('AUTH_API_URL', '').rstrip('/')


# ─────────────────────────────────────────────────────────────
# Email / Password Auth
# ─────────────────────────────────────────────────────────────

@limiter.limit("5 per minute")  # SECURITY FIX (HIGH-1): prevent registration spam
def email_register():
    """POST /api/auth/register — Create account with email+password."""
    data      = request.get_json() or {}
    email     = (data.get('email') or '').strip().lower()
    password  = data.get('password') or ''
    full_name = (data.get('full_name') or data.get('name') or '').strip()

    if not email or not password:
        return jsonify({'message': 'Email and password are required.'}), 400

    try:
        user = register_email_user(email, password, full_name)
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    return jsonify({
        'message':            'Account created. Please check your email to verify your account.',
        'user':               user.to_dict(),
        'requires_verification': True,
    }), 201


@limiter.limit("10 per minute")  # SECURITY FIX (HIGH-1): prevent brute-force login
def email_login():
    """POST /api/auth/login — Authenticate with email+password."""
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'message': 'Email and password are required.'}), 400

    try:
        user = login_email_user(email, password)
    except PermissionError as e:
        return jsonify({
            'message':            str(e),
            'code':               'EMAIL_NOT_VERIFIED',
            'requires_verification': True,
        }), 403
    except ValueError as e:
        return jsonify({'message': str(e)}), 401

    token = issue_jwt(user, current_app.config['SECRET_KEY'])
    return jsonify({
        'message': 'Login successful.',
        'token':   token,
        'user':    user.to_dict(),
    }), 200


def verify_email():
    """GET/POST /api/auth/verify-email — Confirm email with verification token."""
    if request.method == 'GET':
        token = request.args.get('token', '').strip()
        if not token:
            return "<h1>Missing Token</h1><p>No verification token provided.</p>", 400
        
        try:
            verify_email_token(token)
            return "<h1>Email Verified!</h1><p>Your Aurestra account has been successfully verified. You can now close this window and log in to the app.</p>", 200
        except ValueError as e:
            return f"<h1>Verification Failed</h1><p>{str(e)}</p>", 400

    # POST logic
    data  = request.get_json() or {}
    token = (data.get('token') or '').strip()

    if not token:
        return jsonify({'message': 'Verification token is required.'}), 400

    try:
        user = verify_email_token(token)
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    jwt_token = issue_jwt(user, current_app.config['SECRET_KEY'])
    return jsonify({
        'message': 'Email verified successfully.',
        'token':   jwt_token,
        'user':    user.to_dict(),
    }), 200


def resend_verification():
    """POST /api/auth/resend-verification — Resend verification email."""
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'message': 'Email is required.'}), 400

    try:
        resend_verification_email(email)
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    return jsonify({'message': 'Verification email sent. Please check your inbox.'}), 200


@limiter.limit("3 per minute")  # SECURITY FIX (HIGH-1): prevent email flooding
def forgot_password():
    """POST /api/auth/forgot-password — Request password reset email."""
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({'message': 'Email is required.'}), 400

    # Always return 200 — do not reveal whether account exists
    generate_password_reset(email)
    return jsonify({
        'message': 'If an account exists with that email, a reset link has been sent.'
    }), 200


def get_reset_password_form():
    """GET /api/auth/reset-password?token=... — Render the HTML reset form."""
    token = request.args.get('token', '').strip()

    if not token:
        return _reset_html_error('Missing Token', 'No reset token was found in the link. Please request a new password reset.'), 400

    # Validate token early so we can show a clear expired message
    from model import User
    from datetime import datetime
    user = User.query.filter_by(password_reset_token=token).first()
    if not user:
        return _reset_html_error('Invalid Token', 'This reset link is invalid or has already been used. Please request a new one.'), 400
    if not user.password_reset_expires_at or datetime.utcnow() > user.password_reset_expires_at:
        return _reset_html_error('Link Expired', 'This reset link has expired (links are valid for 1 hour). Please request a new one.'), 400

    return _reset_html_form(token)


def do_reset_password():
    """POST /api/auth/reset-password — Set new password using reset token."""
    # Support both JSON (from app) and form-encoded (from web form)
    if request.is_json:
        data         = request.get_json() or {}
        token        = (data.get('token') or '').strip()
        # Accept both 'new_password' (sent by app thunk) and 'password' (legacy)
        new_password = data.get('new_password') or data.get('password') or ''

        if not token or not new_password:
            return jsonify({'message': 'Token and new password are required.'}), 400

        try:
            user = reset_password(token, new_password)
        except ValueError as e:
            return jsonify({'message': str(e)}), 400

        jwt_token = issue_jwt(user, current_app.config['SECRET_KEY'])
        return jsonify({
            'message': 'Password reset successfully. You are now logged in.',
            'token':   jwt_token,
            'user':    user.to_dict(),
        }), 200

    else:
        # HTML form submission
        token        = (request.form.get('token') or '').strip()
        new_password = request.form.get('new_password', '')
        confirm      = request.form.get('confirm_password', '')

        if not token or not new_password:
            return _reset_html_error('Missing Fields', 'Both token and new password are required.'), 400
        if new_password != confirm:
            return _reset_html_form(token, error='Passwords do not match. Please try again.')
        if len(new_password) < 8:
            return _reset_html_form(token, error='Password must be at least 8 characters long.')

        try:
            reset_password(token, new_password)
        except ValueError as e:
            return _reset_html_error('Reset Failed', str(e)), 400

        return _reset_html_success()


# ── HTML helpers for the browser reset form ───────────────────────────────────

def _reset_html_form(token: str, error: str = None) -> str:
    error_html = f'<p style="color:#FF6B6B;text-align:center;margin-bottom:16px;">{error}</p>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reset Password — Aurestra</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ min-height: 100vh; display: flex; align-items: center; justify-content: center;
            background: #050D1A; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
    .card {{ background: #0A1628; border: 1px solid rgba(0,200,150,0.2); border-radius: 20px;
             padding: 40px 32px; width: 100%; max-width: 420px; margin: 24px; }}
    .logo {{ text-align: center; margin-bottom: 28px; }}
    .logo-icon {{ width: 64px; height: 64px; border-radius: 50%; background: rgba(0,200,150,0.12);
                  display: flex; align-items: center; justify-content: center; margin: 0 auto 12px;
                  border: 1px solid rgba(0,200,150,0.3); font-size: 28px; }}
    h1 {{ font-size: 22px; font-weight: 800; color: #F0F6FF; text-align: center; margin-bottom: 6px; }}
    p.sub {{ color: #7A8FA8; font-size: 14px; text-align: center; margin-bottom: 28px; line-height: 1.5; }}
    label {{ display: block; color: #8DA0B8; font-size: 13px; font-weight: 600;
             margin-bottom: 6px; letter-spacing: 0.5px; }}
    input {{ width: 100%; background: #050D1A; border: 1px solid rgba(0,200,150,0.2);
             border-radius: 12px; padding: 14px 16px; color: #F0F6FF; font-size: 15px;
             margin-bottom: 18px; outline: none; transition: border-color 0.2s; }}
    input:focus {{ border-color: #00C896; }}
    button {{ width: 100%; padding: 15px; border: none; border-radius: 14px; cursor: pointer;
              background: linear-gradient(135deg, #00C896, #007BFF);
              color: #fff; font-size: 16px; font-weight: 700; letter-spacing: 0.3px;
              transition: opacity 0.2s; }}
    button:hover {{ opacity: 0.9; }}
    .hint {{ color: #4A5F78; font-size: 12px; margin-top: -12px; margin-bottom: 18px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🔐</div>
      <h1>Set New Password</h1>
      <p class="sub">Enter your new password below. It must be at least 8 characters long.</p>
    </div>
    {error_html}
    <form method="POST" action="/api/auth/reset-password">
      <input type="hidden" name="token" value="{token}">
      <label for="new_password">New Password</label>
      <input type="password" id="new_password" name="new_password" placeholder="Enter new password" required minlength="8">
      <label for="confirm_password">Confirm Password</label>
      <input type="password" id="confirm_password" name="confirm_password" placeholder="Repeat new password" required minlength="8">
      <p class="hint">Must be at least 8 characters long.</p>
      <button type="submit">Reset Password</button>
    </form>
  </div>
</body>
</html>"""


def _reset_html_success() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Password Reset — Aurestra</title>
  <style>
    body { min-height: 100vh; display: flex; align-items: center; justify-content: center;
           background: #050D1A; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
    .card { background: #0A1628; border: 1px solid rgba(0,200,150,0.25); border-radius: 20px;
            padding: 40px 32px; max-width: 400px; text-align: center; margin: 24px; }
    .icon { font-size: 48px; margin-bottom: 16px; }
    h1 { color: #00C896; font-size: 22px; font-weight: 800; margin-bottom: 10px; }
    p { color: #7A8FA8; font-size: 14px; line-height: 1.6; }
    .tag { display: inline-block; margin-top: 20px; background: rgba(0,200,150,0.1);
           color: #00C896; border-radius: 20px; padding: 6px 16px; font-size: 13px; font-weight: 600; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Password Reset!</h1>
    <p>Your password has been changed successfully.<br>You can now close this tab and log in to the Aurestra app with your new password.</p>
    <span class="tag">You're all set</span>
  </div>
</body>
</html>"""


def _reset_html_error(title: str, message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Aurestra</title>
  <style>
    body {{ min-height: 100vh; display: flex; align-items: center; justify-content: center;
           background: #050D1A; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
    .card {{ background: #0A1628; border: 1px solid rgba(255,80,80,0.25); border-radius: 20px;
            padding: 40px 32px; max-width: 400px; text-align: center; margin: 24px; }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h1 {{ color: #FF6B6B; font-size: 22px; font-weight: 800; margin-bottom: 10px; }}
    p {{ color: #7A8FA8; font-size: 14px; line-height: 1.6; }}
    a {{ display: inline-block; margin-top: 20px; color: #00C896; font-size: 14px; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">❌</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# Google OAuth (existing — unchanged)
# ─────────────────────────────────────────────────────────────

@limiter.limit("10 per minute")  # SECURITY FIX (HIGH-1): rate-limit Google OAuth
def google_login():
    data = request.get_json() or {}
    id_token_str = data.get('idToken')
    if not id_token_str:
        return jsonify({'message': 'Missing ID token'}), 400

    try:
        id_info = verify_google_id_token(id_token_str)
        email     = id_info.get('email')
        google_id = id_info.get('sub')
        name      = id_info.get('name')
        picture   = id_info.get('picture')

        centralized_auth = check_centralized_auth(email)
        user = get_or_create_user(email, google_id, name, picture)

        # Do not use Google ID token exp as Flask JWT exp to prevent instant/short expiration.
        # This defaults to a standard 30-day session in issue_jwt.
        token    = issue_jwt(user, current_app.config['SECRET_KEY'])

        auth_url = os.getenv("AUTH_SERVICE_URL")
        return jsonify({
            'message':          'Login successful',
            'token':            token,
            'user':             user.to_dict(),
            'email':            email,
            'centralized_auth': centralized_auth,
            'auth_service_url': auth_url if not centralized_auth else None,
        }), 200

    except ValueError as e:
        return jsonify({'message': f'Invalid token: {str(e)}'}), 401
    except Exception as e:
        print(f"Google Login Error: {e}")
        return jsonify({'message': 'Internal server error'}), 500


def auth_status(current_user):
    data, status_code = check_auth_api_status(current_user.email)
    return jsonify(data), status_code


def auth_verify():
    """POST /api/auth/verify — legacy Google email-based verify (kept for backward-compat)."""
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'message': 'Email is required'}), 400

    try:
        res        = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": email}, timeout=10)
        check_data = res.json()
    except Exception:
        return jsonify({'message': 'Auth service unreachable. Please try again.'}), 503

    if not check_data.get("authenticated"):
        return jsonify({
            'message': 'Not authenticated with Google. Please sign in first.',
            'reason':  check_data.get("reason", "unknown"),
            'action':  'login_required',
            'auth_url': f"{AUTH_API_URL}/auth/google/login",
        }), 401

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, google_email=email, is_email_verified=True, auth_method='google')
        db.session.add(user)
        db.session.commit()
    elif not user.google_email:
        user.google_email      = email
        user.is_email_verified = True
        db.session.commit()

    # Do not use Google ID token exp as Flask JWT exp to prevent instant/short expiration.
    # This defaults to a standard 30-day session in issue_jwt.
    token = issue_jwt(user, current_app.config['SECRET_KEY'])
    return jsonify({'message': 'Login successful', 'token': token,
                    'user': user.to_dict(), 'email': email}), 200


# ─────────────────────────────────────────────────────────────
# Profile (token_required routes)
# ─────────────────────────────────────────────────────────────

def get_profile(current_user):
    from services.auth_service import ensure_user_has_decryption_key
    ensure_user_has_decryption_key(current_user)

    from model import Transaction, SavingsGoal, Category
    from sqlalchemy import or_, and_
    
    tx_count         = Transaction.query.filter_by(user_id=current_user.id, is_deleted=False).count()
    goals_count      = SavingsGoal.query.filter_by(user_id=current_user.id).count()
    categories_count = Category.query.filter(
        or_(
            Category.user_id == current_user.id, 
            and_(Category.is_default == True, Category.user_id == None)
        )
    ).count()
    
    user_data        = current_user.to_dict()
    user_data['stats'] = {
        'transactions': tx_count,
        'goals':        goals_count,
        'categories':   categories_count,
    }
    return jsonify(user_data)



def update_profile(current_user):
    from utils.crypto_helpers import (
        hash_decryption_key, verify_decryption_key, generate_crypto_salt
    )
    import re
    data = request.get_json() or {}
    if 'full_name'             in data: current_user.full_name            = data['full_name']
    if 'email'                 in data: current_user.email                = data['email']
    if 'avatar_url'            in data: current_user.avatar_url           = data['avatar_url']
    if 'notifications_enabled' in data: current_user.notifications_enabled = bool(data['notifications_enabled'])
    
    if 'decryption_key' in data:
        key_val = data['decryption_key']
        is_reset = data.get('reset_decryption_key', False)
        
        # 1. Strength validation
        if len(key_val) < 8 or not re.search(r'[A-Z]', key_val) or not re.search(r'[0-9]', key_val) or not re.search(r'[^A-Za-z0-9]', key_val):
            return jsonify({"error": "Key must be at least 8 characters long and contain uppercase, lowercase, numbers, and special characters."}), 400
            
        # 2. Check if a hash already exists
        # 2. Check if a hash already exists
        if current_user.decryption_key_hash and not is_reset:
            # Verify existing key
            if not verify_decryption_key(key_val, current_user.decryption_key_hash):
                return jsonify({"error": "Incorrect decryption key. Please try again."}), 400
            current_user.decryption_key = key_val
        else:
            # First time setup OR forced reset
            salt = generate_crypto_salt()
            hashed_key = hash_decryption_key(key_val)
            current_user.decryption_key_salt = salt
            current_user.decryption_key_hash = hashed_key
            current_user.decryption_key = key_val
            
    db.session.commit()

    return jsonify(current_user.to_dict())
