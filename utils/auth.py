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
            if dec_key:
                if not current_user.decryption_key_hash:
                    return jsonify({'message': 'Decryption key is not set up on this account.'}), 400
                if not verify_decryption_key(dec_key, current_user.decryption_key_hash):
                    return jsonify({'message': 'Invalid decryption key provided.'}), 401

                # Derive symmetric key and bind to request-scoped g
                # (this is what EncryptedString/EncryptedText TypeDecorators read)
                g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)

        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({'message': f'Token is invalid: {e}'}), 401
        except Exception as e:
            return jsonify({'message': f'Token error: {e}'}), 401

        return f(current_user, *args, **kwargs)
    return decorated


def decryption_key_required(f):
    """Enforces that a valid decryption key is provided and derived in the request."""
    @wraps(f)
    def decorated(current_user, *args, **kwargs):
        if not getattr(g, 'encryption_key', None):
            dec_key = request.headers.get("X-Decryption-Key")
            if not dec_key:
                return jsonify({'message': 'Decryption key required. Please provide it in the X-Decryption-Key header.'}), 400
            
            if not current_user.decryption_key_hash:
                return jsonify({'message': 'Decryption key is not set up on this account.'}), 400
                
            if not verify_decryption_key(dec_key, current_user.decryption_key_hash):
                return jsonify({'message': 'Invalid decryption key provided.'}), 401
                
            g.encryption_key = derive_encryption_key(dec_key, current_user.decryption_key_salt)
            
        return f(current_user, *args, **kwargs)
    return decorated
