import jwt
from functools import wraps
from flask import request, jsonify, current_app
from model import User

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
        
        if not token:
            print("❌ [Auth] Token missing in headers")
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
            user_id = data.get('user_id')
            
            current_user = User.query.get(user_id)
                
            if not current_user:
                print(f"❌ [Auth] User {user_id} NOT found in DB. Table empty or reset?")
                return jsonify({'message': 'User invalid! (DB Record Missing)'}), 401
        except jwt.ExpiredSignatureError:
            print("❌ [Auth] Token Expired")
            return jsonify({'message': 'Token expired!'}), 401
        except jwt.InvalidTokenError as e:
            print(f"❌ [Auth] Token Invalid: {e}")
            return jsonify({'message': 'Token is invalid!'}), 401
        except Exception as e:
            print(f"❌ [Auth] Unexpected Auth Error: {e}")
            return jsonify({'message': 'Token error'}), 401
            
        return f(current_user, *args, **kwargs)
    
    return decorated
