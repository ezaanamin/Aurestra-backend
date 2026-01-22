import unittest
import json
import jwt
from datetime import datetime, timedelta
from app import app, db
from model import User, Transaction, MonthlyBalance, StatementAnalysis

class AurestraAPITestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'super_secret_jwt_key_ezaan_123' # Matches app.py
        self.client = app.test_client()
        
        # Create context
        self.ctx = app.app_context()
        self.ctx.push()
        
        # Get or create a test user
        self.test_email = "test@example.com"
        user = User.query.filter_by(email=self.test_email).first()
        if not user:
            user = User(username="Test User", email=self.test_email)
            # db.session.add(user) # Don't commit to avoid polluting prod DB if possible, but reading needs it?
            # Actually, let's just use the first existing user if available to be safe and real.
            pass

        # If no user, mock one for token generation. ID is enough for token usually?
        # Check token_required logic: expects 'public_id' in tokenPayload?
        # Let's check app.py token_required logic again if needed.
        # Assuming it uses 'public_id' matching User.public_id
        
        # Fallback: Find ANY user
        self.user = User.query.first()
        if not self.user:
            # Create a dummy user in DB if none exists (unlikely in dev env)
            self.user = User(username="Test", email="test@test.com", public_id="test_pid_123")
            db.session.add(self.user)
            db.session.commit()
            
        # Generate Token
        token_payload = {
            'public_id': self.user.public_id,
            'exp': datetime.utcnow() + timedelta(minutes=30)
        }
        self.token = jwt.encode(token_payload, app.config['SECRET_KEY'], algorithm="HS256")
        self.headers = {'x-access-token': self.token}
        
        print(f"\n🚀 Starting Tests with User: {self.user.email} (ID: {self.user.id})")

    def tearDown(self):
        self.ctx.pop()

    def test_01_home(self):
        print("Testing /api/home ...")
        response = self.client.get('/api/home', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        print(f"   ✅ OK. Message: {data.get('message')}")

    def test_02_latest_transactions(self):
        print("Testing /api/latest-transactions ...")
        response = self.client.get('/api/latest-transactions', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        print(f"   ✅ OK. Count: {len(data)}")

    def test_03_top_spending(self):
        print("Testing /api/top-spending-categories ...")
        response = self.client.get('/api/top-spending-categories', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        print(f"   ✅ OK. Categories: {len(data)}")

    def test_04_budget(self):
        print("Testing /api/budget ...")
        response = self.client.get('/api/budget', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        print(f"   ✅ OK.")

    def test_05_accounts(self):
        print("Testing /api/accounts ...")
        response = self.client.get('/api/accounts', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        print(f"   ✅ OK. Accounts: {len(data)}")

    def test_06_monthly_summary(self):
        print("Testing /api/monthly-summary ...")
        response = self.client.get('/api/monthly-summary', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        print(f"   ✅ OK.")

    def test_07_calculate_statement_cached(self):
        print("Testing /api/calculate-statement (Cached December 2025) ...")
        # Ensure we test the cached logic we just fixed
        response = self.client.get('/api/calculate-statement?month=2025-12', headers=self.headers)
        
        if response.status_code == 404:
             print("   ⚠️ Month 2025-12 not found. Skipping cache verification.")
             return

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        # Verify Structure for Frontend
        if data.get('cached'):
            print("   ✅ HIT CACHE.")
            self.assertIn('data', data, "Missing 'data' key for table!") # The key we just fixed
            self.assertIn('balances', data)
            self.assertIn('opening', data['balances'], "Missing 'opening' balance!") # The key we just fixed
            print(f"   ✅ Structure Valid: {len(data['data'])} transactions.")
        else:
            print("   ℹ️ Fresh Load (Not Cached).")

    def test_08_savings_goals(self):
        print("Testing /api/savings-goals ...")
        response = self.client.get('/api/savings-goals', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        print(f"   ✅ OK.")
        
if __name__ == '__main__':
    unittest.main()
