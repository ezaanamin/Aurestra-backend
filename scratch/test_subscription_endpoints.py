import os
import sys
import json
from datetime import datetime, timedelta
import jwt

# Append backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database import db
from model import User

def test_subscription_routes():
    print("🧪 Running subscription endpoints integration tests...")
    with app.app_context():
        # Find test user
        user = User.query.filter_by(email="amin.ezaan@gmail.com").first() or User.query.first()
        if not user:
            print("❌ No users found in database!")
            return
            
        print(f"✅ Using user: {user.email}")
        
        # Reset plan to free first
        user.current_plan_id = "free"
        db.session.commit()
        
        # Generate token
        payload = {
            "user_id": user.id,
            "exp": datetime.utcnow() + timedelta(days=1)
        }
        token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm="HS256")
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        with app.test_client() as client:
            # 1. GET /api/subscription/plans
            print("\n📬 Testing GET /api/subscription/plans...")
            response = client.get('/api/subscription/plans', headers=headers)
            print(f"  Response Status: {response.status_code}")
            plans = json.loads(response.data.decode('utf-8'))
            print(f"  Available plans: {[p['plan_id'] for p in plans]}")
            
            # 2. GET /api/subscription/status (Free)
            print("\n📬 Testing GET /api/subscription/status (Initial Free)...")
            response = client.get('/api/subscription/status', headers=headers)
            print(f"  Response Status: {response.status_code}")
            status = json.loads(response.data.decode('utf-8'))
            print(f"  Current Plan ID: {status['current_plan']['plan_id']}")
            print(f"  AI Chat feature allowed: {status['features_allowed']['ai_chat']}")
            print(f"  Investment planning allowed: {status['features_allowed']['investment_planning']}")
            
            # 3. POST /api/subscription/subscribe (Upgrade to Pro)
            print("\n📬 Testing POST /api/subscription/subscribe (Upgrade to Pro)...")
            response = client.post('/api/subscription/subscribe', headers=headers, json={"plan_id": "pro"})
            print(f"  Response Status: {response.status_code}")
            resp_data = json.loads(response.data.decode('utf-8'))
            print(f"  Message: {resp_data['message']}")
            print(f"  New Plan ID: {resp_data['subscription']['current_plan']['plan_id']}")
            print(f"  New features allowed: {list(resp_data['subscription']['features_allowed'].keys())}")
            
            # 4. GET /api/subscription/status (Pro)
            print("\n📬 Testing GET /api/subscription/status (After Upgrade)...")
            response = client.get('/api/subscription/status', headers=headers)
            print(f"  Response Status: {response.status_code}")
            status_pro = json.loads(response.data.decode('utf-8'))
            print(f"  Current Plan ID: {status_pro['current_plan']['plan_id']}")
            print(f"  Investment planning allowed: {status_pro['features_allowed']['investment_planning']}")
            
            # 5. Clean up (Reset to free)
            print("\n🔄 Resetting user back to free plan...")
            response = client.post('/api/subscription/subscribe', headers=headers, json={"plan_id": "free"})
            print(f"  Response Status: {response.status_code}")
            
            print("\n🎉 All subscription endpoints tests PASSED successfully!")

if __name__ == "__main__":
    test_subscription_routes()
