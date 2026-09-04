import os
import sys
import sqlite3
import json
from werkzeug.security import generate_password_hash

# Append backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database import db
from model import User

def test_login():
    print("🧪 Running backend login integration tests...")
    with app.app_context():
        # Test 1: Query User 2 (email user) and ensure password verifies
        user = User.query.filter_by(email="amin.ezaan@gmail.com").first()
        if not user:
            print("❌ User 2 (amin.ezaan@gmail.com) not found in database!")
            return
            
        print(f"✅ Found user 2: {user.email}")
        print(f"  Auth Method: {user.auth_method}")
        print(f"  Verified: {user.is_email_verified}")
        print(f"  Has Hash: {user.decryption_key_hash is not None}")
        print(f"  Has Key: {user.decryption_key is not None} ({user.decryption_key})")
        
        # Test 2: Simulate request to email login
        with app.test_client() as client:
            # We will test login with a dummy password reset first to ensure it matches
            # Let's update user 2 password to 'EndG*ame@201&' for test
            user.password_hash = generate_password_hash('EndG*ame@201&')
            user.is_email_verified = True
            db.session.commit()
            
            print("\n📬 Testing POST /api/auth/login...")
            response = client.post('/api/auth/login', json={
                'email': 'amin.ezaan@gmail.com',
                'password': 'EndG*ame@201&'
            })
            
            print(f"  Response Status: {response.status_code}")
            data = json.loads(response.data.decode('utf-8'))
            print(f"  Response JSON:\n{json.dumps(data, indent=2)}")
            
            if response.status_code == 200 and 'token' in data and 'user' in data:
                print("🎉 Email Login Backend verification SUCCESSFUL!")
            else:
                print("❌ Email Login Backend verification FAILED!")

if __name__ == "__main__":
    test_login()
