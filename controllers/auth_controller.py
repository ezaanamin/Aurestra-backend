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
)
from model import User
from database import db

AUTH_API_URL = os.getenv('AUTH_API_URL', '').rstrip('/')


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
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'message': 'Email is required'}), 400

    try:
        res       = http_requests.get(f"{AUTH_API_URL}/auth/check", params={"email": email}, timeout=10)
        check_data = res.json()
    except Exception as e:
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
        user = User(email=email, google_email=email)
        db.session.add(user)
        db.session.commit()
    elif not user.google_email:
        user.google_email = email
        db.session.commit()

    from datetime import timedelta
    exp_date = datetime.utcnow() + timedelta(hours=2)
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
    data = request.get_json() or {}
    if 'full_name'              in data: current_user.full_name = data['full_name']
    if 'email'                  in data: current_user.email     = data['email']
    if 'avatar_url'             in data: current_user.avatar_url = data['avatar_url']
    if 'notifications_enabled'  in data: current_user.notifications_enabled = bool(data['notifications_enabled'])
    db.session.commit()
    return jsonify(current_user.to_dict())
