# utils/auth.py  —  Shared authentication decorator

import jwt
from functools import wraps
from flask import request, jsonify, current_app
from model import User
from database import app


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

            with app.app_context():
                current_user = User.query.get(user_id)

            if not current_user:
                return jsonify({'message': 'User invalid! (DB Record Missing)'}), 401

        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({'message': f'Token is invalid: {e}'}), 401
        except Exception as e:
            return jsonify({'message': 'Token error'}), 401

        return f(current_user, *args, **kwargs)
    return decorated
