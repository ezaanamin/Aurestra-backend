import jwt
from datetime import datetime, timedelta
import requests
import os
from database import app
import json

def get_token(user_id=1):
    secret = os.getenv("SECRET_KEY", "0aefb44af279f5bb0ad9ecce393be138")
    token = jwt.encode({
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(hours=1)
    }, secret, algorithm="HS256")
    return token

def test_calculate_api(month="2026-03"):
    token = get_token()
    url = "http://127.0.0.1:5000/api/reports/statement/calculate"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"month": month}
    
    print(f"🚀 [Test] Sending request to {url} for month {month}...")
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"📊 [Test] Response Code: {response.status_code}")
        print(f"📄 [Test] Response Body: {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        print(f"❌ [Test] Request failed: {e}")

if __name__ == "__main__":
    test_calculate_api()
