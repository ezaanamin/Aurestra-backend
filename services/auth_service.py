# services/auth_service.py  —  Auth business logic (no Flask)

import os
import re
import secrets
import smtplib
import jwt
import requests as http_requests

from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from werkzeug.security import generate_password_hash, check_password_hash

from database import db
from model import User


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

AUTH_API_URL = os.getenv('AUTH_API_URL', '').rstrip('/')
SMTP_EMAIL   = os.getenv('SMTP_EMAIL', os.getenv('BANK_EMAIL_ACCOUNT', ''))
SMTP_PASS    = os.getenv('APP_PASSWORD', '')
SMTP_HOST    = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT    = int(os.getenv('SMTP_PORT', '587'))

APP_NAME     = "Aurestra"
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://aurestra.app')
BACKEND_URL  = os.getenv('BACKEND_URL', 'https://vernon-msie-store-convenient.trycloudflare.com').rstrip('/')

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _is_valid_email(email: str) -> bool:
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))


def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(length)


def hash_password(plain: str) -> str:
    return generate_password_hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return check_password_hash(hashed, plain)


# ─────────────────────────────────────────────────────────────
# Email Sending
# ─────────────────────────────────────────────────────────────

from drive_utils import get_gmail_service, create_message, send_gmail_message
import re

def _send_email(to_addr: str, subject: str, html_body: str) -> bool:
    """Send an HTML email via Gmail API instead of SMTP. Returns True on success."""
    try:
        service = get_gmail_service()
        if not service:
            print("⚠️ [Auth] Gmail API service could not be initialized.")
            return False

        sender = "me"
        plain_text = re.sub('<[^<]+?>', '', html_body)
        
        message = create_message(sender, to_addr, subject, plain_text, html_body)
        result = send_gmail_message(service, "me", message)
        
        if result:
            print(f"✅ [Auth] Email sent to {to_addr}: {subject} via Gmail API")
            return True
        else:
            print(f"❌ [Auth] Email send failed to {to_addr} via Gmail API")
            return False
    except Exception as e:
        print(f"❌ [Auth] Email send failed to {to_addr}: {e}")
        return False


def send_verification_email(user: User) -> bool:
    """Send an email verification link to the user."""
    token = generate_secure_token()
    user.email_verification_token = token
    user.email_verification_sent_at = datetime.utcnow()
    db.session.commit()

    # Use the actual backend tunnel URL
    base_url = BACKEND_URL
    verify_url = f"{base_url}/api/auth/verify-email?token={token}"

    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;background:#050D1A;color:#EAF2FF;border-radius:16px;padding:40px;">
      <h2 style="color:#00C896;margin-bottom:8px;">Verify your email</h2>
      <p style="color:#8DA0B8;margin-bottom:24px;">
        Welcome to <strong style="color:#EAF2FF;">{APP_NAME}</strong>!
        Click the button below to activate your account.
        This link expires in <strong>24 hours</strong>.
      </p>
      <a href="{verify_url}"
         style="display:inline-block;background:linear-gradient(135deg,#00C896,#007BFF);
                color:#fff;font-weight:700;padding:14px 32px;border-radius:12px;
                text-decoration:none;font-size:15px;letter-spacing:0.3px;">
        Verify Email Address
      </a>
      <p style="color:#5A6E82;margin-top:32px;font-size:12px;">
        If you didn't create an {APP_NAME} account, you can safely ignore this email.<br>
        Token: <code style="color:#00C896">{token}</code>
      </p>
    </div>
    """
    return _send_email(user.email, f"Verify your {APP_NAME} email", html)


def send_password_reset_email(user: User) -> bool:
    """Send a password reset link to the user. Token valid for 1 hour."""
    token = generate_secure_token()
    user.password_reset_token      = token
    user.password_reset_expires_at = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()

    base_url = BACKEND_URL
    reset_url = f"{base_url}/api/auth/reset-password?token={token}"

    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:auto;background:#050D1A;color:#EAF2FF;border-radius:16px;padding:40px;">
      <h2 style="color:#00C896;margin-bottom:8px;">Reset your password</h2>
      <p style="color:#8DA0B8;margin-bottom:24px;">
        We received a request to reset your <strong style="color:#EAF2FF;">{APP_NAME}</strong> password.
        Click the button below. This link expires in <strong>1 hour</strong>.
      </p>
      <a href="{reset_url}"
         style="display:inline-block;background:linear-gradient(135deg,#00C896,#007BFF);
                color:#fff;font-weight:700;padding:14px 32px;border-radius:12px;
                text-decoration:none;font-size:15px;letter-spacing:0.3px;">
        Reset Password
      </a>
      <p style="color:#5A6E82;margin-top:32px;font-size:12px;">
        If you didn't request this, you can safely ignore this email.<br>
        Token: <code style="color:#00C896">{token}</code>
      </p>
    </div>
    """
    return _send_email(user.email, f"{APP_NAME} — Password Reset Request", html)


# ─────────────────────────────────────────────────────────────
# Email / Password Registration & Login
# ─────────────────────────────────────────────────────────────

def register_email_user(email: str, password: str, full_name: str = None) -> User:
    """
    Create a new user with email+password.
    Raises ValueError on validation failures.
    Returns the new (unverified) User.
    """
    email = email.strip().lower()

    if not _is_valid_email(email):
        raise ValueError("Invalid email address.")

    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    existing = User.query.filter_by(email=email).first()
    if existing:
        raise ValueError("An account with this email already exists.")

    user = User(
        email=email,
        full_name=(full_name or '').strip() or None,
        password_hash=hash_password(password),
        auth_method='email',
        is_email_verified=False,
    )
    db.session.add(user)
    db.session.commit()

    # Send verification email (non-blocking failure)
    send_verification_email(user)

    # Seed default categories for this new user
    try:
        from decorator.helpers import seed_categories_for_user
        seed_categories_for_user(user.id)
    except Exception as e:
        print(f"⚠️ [Auth] Category seeding failed for user {user.id}: {e}")

    return user


def login_email_user(email: str, password: str) -> User:
    """
    Authenticate email+password. Raises ValueError on failure.
    Returns the authenticated User.
    """
    email = email.strip().lower()
    user  = User.query.filter_by(email=email).first()

    if not user or not user.password_hash:
        raise ValueError("Invalid email or password.")

    if not verify_password(password, user.password_hash):
        raise ValueError("Invalid email or password.")

    if not user.is_email_verified:
        raise PermissionError("Email not verified. Please check your inbox.")

    return user


# ─────────────────────────────────────────────────────────────
# Email Verification
# ─────────────────────────────────────────────────────────────

def verify_email_token(token: str) -> User:
    """
    Mark email as verified. Raises ValueError if token is invalid/expired.
    Returns the verified User.
    """
    user = User.query.filter_by(email_verification_token=token).first()
    if not user:
        raise ValueError("Invalid or expired verification token.")

    # Token valid for 24 hours
    if user.email_verification_sent_at:
        age = datetime.utcnow() - user.email_verification_sent_at
        if age > timedelta(hours=24):
            raise ValueError("Verification link has expired. Please request a new one.")

    user.is_email_verified         = True
    user.email_verification_token  = None
    user.email_verification_sent_at = None
    db.session.commit()
    return user


def resend_verification_email(email: str) -> bool:
    """
    Re-send verification email to the given address.
    Raises ValueError if already verified or user not found.
    Returns True if email was sent.
    """
    email = email.strip().lower()
    user  = User.query.filter_by(email=email).first()

    if not user:
        # Don't reveal whether the account exists
        return True

    if user.is_email_verified:
        raise ValueError("Email is already verified.")

    # Rate-limit: at most once per 60 seconds
    if user.email_verification_sent_at:
        elapsed = (datetime.utcnow() - user.email_verification_sent_at).total_seconds()
        if elapsed < 60:
            wait = int(60 - elapsed)
            raise ValueError(f"Please wait {wait} seconds before requesting another email.")

    return send_verification_email(user)


# ─────────────────────────────────────────────────────────────
# Password Reset
# ─────────────────────────────────────────────────────────────

def generate_password_reset(email: str) -> bool:
    """
    Issue a password reset token and email it.
    Always returns True (do not reveal whether account exists).
    """
    email = email.strip().lower()
    user  = User.query.filter_by(email=email).first()
    if user and user.password_hash:
        send_password_reset_email(user)
    return True


def reset_password(token: str, new_password: str) -> User:
    """
    Validate reset token and set new password.
    Raises ValueError on invalid/expired token or weak password.
    """
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    user = User.query.filter_by(password_reset_token=token).first()
    if not user:
        raise ValueError("Invalid or expired reset token.")

    if not user.password_reset_expires_at or datetime.utcnow() > user.password_reset_expires_at:
        user.password_reset_token      = None
        user.password_reset_expires_at = None
        db.session.commit()
        raise ValueError("Reset link has expired. Please request a new one.")

    user.password_hash             = hash_password(new_password)
    user.password_reset_token      = None
    user.password_reset_expires_at = None
    user.is_email_verified         = True  # Reset proves email ownership
    db.session.commit()
    return user


# ─────────────────────────────────────────────────────────────
# Google OAuth (existing — unchanged)
# ─────────────────────────────────────────────────────────────

def verify_google_id_token(id_token_str: str):
    """Verify a Google ID token locally. Returns id_info dict or raises ValueError."""
    from google.oauth2 import id_token
    from google.auth.transport import requests
    return id_token.verify_oauth2_token(
        id_token_str,
        requests.Request(),
        os.getenv('GOOGLE_WEB_CLIENT_ID'),
    )


def check_centralized_auth(email: str) -> bool:
    """Ask Auth-api whether this email is currently authenticated."""
    auth_url = os.getenv("AUTH_SERVICE_URL", "")
    try:
        res = http_requests.get(f"{auth_url}/auth/check", params={"email": email}, timeout=5)
        return res.status_code == 200 and res.json().get("authenticated", False)
    except Exception as e:
        print(f"⚠️ [AUTH] Could not reach Auth-api: {e}")
        return False


def get_or_create_user(email: str, google_id: str = None, name: str = None, picture: str = None) -> User:
    """Get or create a user from Google OAuth data. Google users are auto-verified."""
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            email=email,
            full_name=name,
            google_id=google_id,
            google_email=email,
            avatar_url=picture,
            auth_method='google',
            is_email_verified=True,   # Google pre-verifies emails
        )
        db.session.add(user)
        db.session.commit()

        # Seed default categories for this new Google user
        try:
            from decorator.helpers import seed_categories_for_user
            seed_categories_for_user(user.id)
        except Exception as e:
            print(f"⚠️ [Auth] Category seeding failed for user {user.id}: {e}")

    else:
        changed = False
        if not user.google_id and google_id:
            user.google_id    = google_id
            user.google_email = email
            # Mark as 'both' if they previously registered with email
            user.auth_method  = 'both' if user.password_hash else 'google'
            changed = True
        if picture and not user.avatar_url:
            user.avatar_url = picture
            changed = True
        if name and not user.full_name:
            user.full_name = name
            changed = True
        if not user.is_email_verified:
            user.is_email_verified = True
            changed = True
        if changed:
            db.session.commit()

    return user


# ─────────────────────────────────────────────────────────────
# JWT
# ─────────────────────────────────────────────────────────────

def issue_jwt(user: User, secret: str, exp: datetime = None) -> str:
    if not exp:
        exp = datetime.utcnow() + timedelta(days=30)  # 30-day sessions
    return jwt.encode(
        {'user_id': user.id, 'email': user.email, 'exp': exp},
        secret, algorithm="HS256",
    )


# ─────────────────────────────────────────────────────────────
# Backward-compat helpers
# ─────────────────────────────────────────────────────────────

def check_auth_api_status(email: str):
    """Raw auth-api /auth/check response dict (for status endpoint)."""
    try:
        res = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": email}, timeout=8)
        return res.json(), res.status_code
    except Exception as e:
        return {"authenticated": False, "error": str(e)}, 503
