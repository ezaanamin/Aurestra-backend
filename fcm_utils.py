# fcm_utils.py
import firebase_admin
from firebase_admin import credentials, messaging
import os

import json

if not firebase_admin._apps:
    cred = None
    
    # Priority 1: JSON String from Env (Production/Render)
    firebase_json = os.getenv("FIREBASE_CREDENTIALS")
    if firebase_json:
        try:
            cred_dict = json.loads(firebase_json)
            cred = credentials.Certificate(cred_dict)
            print("🔥 Firebase initialized via FIREBASE_CREDENTIALS env var")
        except Exception as e:
            print(f"❌ Failed to parse FIREBASE_CREDENTIALS env: {e}")

    # Priority 2: File Path (Local Development)
    if not cred and os.getenv("FIREBASE_CRED_PATH"):
        cred_path = os.getenv("FIREBASE_CRED_PATH")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            print(f"🔥 Firebase initialized via file: {cred_path}")
        else:
            print(f"⚠️ FIREBASE_CRED_PATH defined but file not found: {cred_path}")

    if cred:
        firebase_admin.initialize_app(cred)
    else:
        print("⚠️ Firebase not initialized: No credentials found.")

def send_push_to_all(title, body, tokens=None):
    if tokens is None:
        try:
            from model import DeviceToken
            # We assume this is called within an app context or we create one
            # If called from a route, context is already there.
            tokens = [t.token for t in DeviceToken.query.all()]
        except Exception as e:
            print(f"⚠️ Could not fetch tokens from DB: {e}")
            tokens = []

    if not tokens:
        print("⚠️ No device tokens registered. No push sent.")
        return

    for token in tokens:
        try:
            messaging.send(messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token
            ))
            print("✅ Push sent:", token)
        except firebase_admin.exceptions.FirebaseError as e:
            print(f"❌ Push failed for token {token}: {e}")
            # If token is invalid or not found, remove it from DB
            if 'registration-token-not-registered' in str(e).lower() or 'invalid-argument' in str(e).lower():
                try:
                    from model import db, DeviceToken
                    DeviceToken.query.filter_by(token=token).delete()
                    db.session.commit()
                    print(f"🗑️ Removed invalid token from DB: {token}")
                except Exception as db_err:
                    print(f"⚠️ Failed to remove token from DB: {db_err}")
        except Exception as e:
            print("❌ Push failed:", token, e)
