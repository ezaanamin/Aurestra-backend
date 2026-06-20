"""
Run this script once to get a valid JWT Bearer token for Postman testing.
Usage:  venv/bin/python generate_test_token.py
"""
import os, sys
from dotenv import load_dotenv
load_dotenv()

# ── Make sure the backend packages are importable ──────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

import jwt
from datetime import datetime, timedelta
from database import db
from model import User

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    print("❌  SECRET_KEY not found in .env")
    sys.exit(1)

# ── Pick the first user in the DB ──────────────────────────────────────────────
from app import app
with app.app_context():
    user = User.query.first()
    if not user:
        print("❌  No user found in the database.  Login via the app first.")
        sys.exit(1)

    token = jwt.encode(
        {
            "user_id": user.id,
            "email":   user.email,
            "exp":     datetime.utcnow() + timedelta(days=30),   # 30-day token for testing
        },
        SECRET_KEY,
        algorithm="HS256",
    )

    print("\n✅  Test JWT Token (valid for 30 days):\n")
    print(f"Bearer {token}")
    print(f"\n   User ID : {user.id}")
    print(f"   Email   : {user.email}")
    print("\nPaste the full 'Bearer <token>' string into Postman → Authorization → Bearer Token")
