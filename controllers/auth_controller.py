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
from database import db

AUTH_API_URL = os.getenv('AUTH_API_URL', '').rstrip('/')


# ─────────────────────────────────────────────────────────────
# Email / Password Auth
# ─────────────────────────────────────────────────────────────

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


def do_reset_password():
    """POST /api/auth/reset-password — Set new password using reset token."""
    data         = request.get_json() or {}
    token        = (data.get('token') or '').strip()
    new_password = data.get('password') or ''

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


# ─────────────────────────────────────────────────────────────
# Google OAuth (existing — unchanged)
# ─────────────────────────────────────────────────────────────

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

        exp_ts   = id_info.get('exp')
        exp_date = datetime.utcfromtimestamp(exp_ts) if exp_ts else None
        token    = issue_jwt(user, current_app.config['SECRET_KEY'], exp_date)

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

    from datetime import timedelta
    exp_date     = datetime.utcnow() + timedelta(hours=2)
    id_token_str = data.get('idToken')
    if id_token_str:
        try:
            decoded = pyjwt.decode(id_token_str, options={"verify_signature": False})
            exp_ts  = decoded.get('exp')
            if exp_ts:
                exp_date = datetime.utcfromtimestamp(exp_ts)
        except Exception:
            pass

    token = issue_jwt(user, current_app.config['SECRET_KEY'], exp_date)
    return jsonify({'message': 'Login successful', 'token': token,
                    'user': user.to_dict(), 'email': email}), 200


# ─────────────────────────────────────────────────────────────
# Profile (token_required routes)
# ─────────────────────────────────────────────────────────────

def get_profile(current_user):
    from model import Transaction, SavingsGoal, Category
    tx_count         = Transaction.query.filter_by(is_deleted=False).count()
    goals_count      = SavingsGoal.query.count()
    categories_count = Category.query.count()
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
