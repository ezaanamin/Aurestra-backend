# utils/auth.py  —  Shared authentication decorator

import jwt
from functools import wraps
from flask import request, jsonify, current_app, g
from model import User
from utils.crypto_helpers import verify_decryption_key, derive_encryption_key


def token_required(f):
    """JWT bearer-token guard. Injects `current_user` as the first argument."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if not token:
            print("DEBUG [token_required]: Token is missing!")
            return jsonify({'message': 'Token is missing!'}), 401

        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
            user_id = data.get('user_id')

            # Query in the CURRENT request context — no nested app_context()
            current_user = User.query.get(user_id)

            if not current_user:
                print(f"DEBUG [token_required]: User {user_id} invalid (DB Record Missing)!")
                return jsonify({'message': 'User invalid! (DB Record Missing)'}), 401

            # Check for decryption key header and derive encryption key
            # Skip key verification on /api/profile since that endpoint handles its own key verification/setup
            dec_key = request.headers.get("X-Decryption-Key")
            if dec_key and current_user.decryption_key_hash and request.path != '/api/profile':
                # Hash is registered — verify the key
                if not verify_decryption_key(dec_key, current_user.decryption_key_hash):
                    print(f"DEBUG [token_required]: Invalid decryption key for user {current_user.email}!")
                    return jsonify({'message': 'Invalid decryption key provided.'}), 401

                # Self-healing: if decryption_key is NULL or mismatched in DB but hash verifies, populate it
                if current_user.decryption_key != dec_key:
                    from database import db
                    current_user.decryption_key = dec_key
                    try:
                        db.session.commit()
                        print(f"✅ [Auth] Self-healed decryption_key column for {current_user.email}")
                    except Exception as e:
                        db.session.rollback()
                        print(f"⚠️ [Auth] Failed to auto-save decryption_key for {current_user.email}: {e}")

                # Derive symmetric key and bind to request-scoped g
                g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)
            # If dec_key header present but no hash yet → first-time setup, allow through

        except jwt.ExpiredSignatureError:
            print("DEBUG [token_required]: Token expired!")
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            print(f"DEBUG [token_required]: Token invalid: {e}")
            return jsonify({'message': f'Token is invalid: {e}'}), 401
        except Exception as e:
            print(f"DEBUG [token_required]: Token error: {e}")
            return jsonify({'message': f'Token error: {e}'}), 401

        return f(current_user, *args, **kwargs)
    return decorated


def decryption_key_required(f):
    """Enforces that a valid decryption key is provided and derived in the request.
    Must be stacked INSIDE token_required: token_required(decryption_key_required(fn))
    """
    @wraps(f)
    def decorated(current_user, *args, **kwargs):
        # Already derived this request (token_required verified it)
        if getattr(g, 'encryption_key', None):
            return f(current_user, *args, **kwargs)

        # User hasn't set up a key yet at all
        if not current_user.decryption_key_hash:
            return jsonify({
                'message': 'Decryption key not configured. Please set up your vault key in the app.',
                'code': 'KEY_NOT_CONFIGURED'
            }), 403

        # Key configured on account but header missing from this request
        dec_key = request.headers.get("X-Decryption-Key")
        if not dec_key:
            return jsonify({
                'message': 'X-Decryption-Key header is required for this operation.',
                'code': 'KEY_MISSING'
            }), 400

        if not verify_decryption_key(dec_key, current_user.decryption_key_hash):
            return jsonify({
                'message': 'Invalid decryption key.',
                'code': 'KEY_INVALID'
            }), 401

        # Self-healing: if decryption_key is NULL or mismatched in DB but hash verifies, populate it
        if current_user.decryption_key != dec_key:
            from database import db
            current_user.decryption_key = dec_key
            try:
                db.session.commit()
                print(f"✅ [Auth] Self-healed decryption_key column for {current_user.email} (required decorator)")
            except Exception as e:
                db.session.rollback()

        g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)
        return f(current_user, *args, **kwargs)
    return decorated
