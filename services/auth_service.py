# services/auth_service.py  —  Auth business logic (no Flask)

import os
import jwt
import requests as http_requests
from datetime import datetime, timedelta
from database import db
from model import User


AUTH_API_URL = os.getenv('AUTH_API_URL', '').rstrip('/')


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
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, full_name=name, google_id=google_id,
                    google_email=email, avatar_url=picture)
        db.session.add(user)
        db.session.commit()
    else:
        if not user.google_id and google_id:
            user.google_id = google_id
            user.google_email = email
        if picture:
            user.avatar_url = picture
        if name and not user.full_name:
            user.full_name = name
        db.session.commit()
    return user


def issue_jwt(user: User, secret: str, exp: datetime = None) -> str:
    if not exp:
        exp = datetime.utcnow() + timedelta(hours=2)
    return jwt.encode(
        {'user_id': user.id, 'email': user.email, 'exp': exp},
        secret, algorithm="HS256",
    )


def check_auth_api_status(email: str):
    """Raw auth-api /auth/check response dict (for status endpoint)."""
    try:
        res = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": email}, timeout=8)
        return res.json(), res.status_code
    except Exception as e:
        return {"authenticated": False, "error": str(e)}, 503
