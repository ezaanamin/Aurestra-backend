
import requests
import json
import datetime
from database import app
from model import User
import jwt

# Config is already loaded in database.py
app.config['SECRET_KEY'] = 'super_secret_jwt_key_ezaan_123'

# Get token for first user
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

# Test POST API
url = "http://localhost:5000/api/calculate-summary"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
payload = {
    "month": datetime.datetime.now().strftime("%Y-%m")
}

try:
    print(f"\n--- API POST Verification ---")
    print(f"POST {url} with {payload}")
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print(f"API Expense: {data.get('total_expense')}")
        print(f"API Income: {data.get('total_income')}")
        print(f"Full Response: {json.dumps(data, indent=2)}")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
