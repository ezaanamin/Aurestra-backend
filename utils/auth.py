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
            return jsonify({'message': 'Token is missing!'}), 401

        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
            user_id = data.get('user_id')

            # Query in the CURRENT request context — no nested app_context()
            current_user = User.query.get(user_id)

            if not current_user:
                return jsonify({'message': 'User invalid! (DB Record Missing)'}), 401

            # Check for decryption key header and derive encryption key
            dec_key = request.headers.get("X-Decryption-Key")
            if dec_key and current_user.decryption_key_hash:
                # Hash is registered — verify the key
                if not verify_decryption_key(dec_key, current_user.decryption_key_hash):
                    return jsonify({'message': 'Invalid decryption key provided.'}), 401

                # Derive symmetric key and bind to request-scoped g
                g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)
            # If dec_key header present but no hash yet → first-time setup, allow through

        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({'message': f'Token is invalid: {e}'}), 401
        except Exception as e:
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

        g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)
        return f(current_user, *args, **kwargs)
    return decorated
