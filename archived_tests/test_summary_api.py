
import requests
import json
import datetime
from database import db, app
from model import User, Transaction
import jwt

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
    
    # Calculate expected sum from DB directly
    target_date = datetime.datetime.now()
    expected_expense = db.session.query(db.func.sum(Transaction.amount)).filter(
        db.extract('year', Transaction.date) == target_date.year,
        db.extract('month', Transaction.date) == target_date.month,
        Transaction.type == 'debit'
    ).scalar() or 0.0
    
    print(f"--- DB Verification ---")
    print(f"Expected Expense Sum (from DB query): {expected_expense}")

# Test API
url = "http://localhost:5000/api/monthly-summary"
headers = {"Authorization": f"Bearer {token}"}

try:
    print(f"\n--- API Verification ---")
    print(f"GET {url}")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"API Expense: {data.get('total_expense')}")
        print(f"API Income: {data.get('total_income')}")
        
        if abs(data.get('total_expense', 0) - expected_expense) < 0.1:
            print("✅ SUCCESS: API matches DB sum exactly.")
        else:
            print(f"❌ MISMATCH: API {data.get('total_expense')} != DB {expected_expense}")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")
        
except Exception as e:
    print(f"Error: {e}")
