
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

# Test Dedicated Expenses API
url = "http://localhost:5000/api/expenses/total"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

try:
    print(f"\n--- Dedicated API Verification ---")
    print(f"GET {url}")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"Total Expense: {data.get('total_expense')}")
        print(f"Month: {data.get('month')}")
        print(f"Full Response: {json.dumps(data, indent=2)}")
        
        if data.get('total_expense') > 0:
             print("✅ Success: API returns non-zero positive expense.")
        else:
             print("⚠️ Warning: API returned 0 expense. Is this expected?")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
