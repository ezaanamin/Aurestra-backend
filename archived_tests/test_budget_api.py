
import requests
import json
import datetime
from database import app
from model import User
import jwt

# Config
app.config['SECRET_KEY'] = 'super_secret_jwt_key_ezaan_123'

# Get token
with app.app_context():
    user = User.query.first()
    if not user:
        print("No user found")
        exit()
    
    secret_key = 'super_secret_jwt_key_ezaan_123'
    token = jwt.encode({
        'user_id': user.id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }, secret_key, algorithm="HS256")

# Test Budget API
url = "http://localhost:5000/api/budget"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

try:
    print(f"\n--- Checking Backend Budget API ---")
    print(f"GET {url}")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"Full Response:\n{json.dumps(data, indent=2)}")
        
        needs = data.get('needs', 0)
        wants = data.get('wants', 0)
        limit = data.get('spending_limit')
        total = data.get('total_budget')
        
        print(f"\nVerification:")
        print(f"Needs: {needs}")
        print(f"Wants: {wants}")
        print(f"Spending Limit (from API): {limit}")
        print(f"Total Budget (Income): {total}")
        
        calc_limit = needs + wants
        if limit == calc_limit:
             print("✅ Success: spending_limit matches Needs + Wants.")
        else:
             print(f"❌ Mismatch: spending_limit {limit} != {calc_limit}")
             
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
