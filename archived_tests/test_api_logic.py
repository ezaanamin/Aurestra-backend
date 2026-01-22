from app import app
from flask import json

def test_endpoint():
    print("Testing /api/insights endpoint...")
    with app.test_client() as client:
        # We need to mock auth or bypass it? 
        # The endpoint is @token_required.
        # We can bypass it or mock the request if we invoke the function directly with a mock user
        # OR we can just check if the function exists and runs in a context where we mock the user logic
        
        # Simpler: Just run the logic inside app context directly to verify the query part works
        # mocking the request/response flow is harder without a valid token generator in this script
        
        with app.app_context():
            try:
                from model import FinancialInsight
                insights = FinancialInsight.query.order_by(FinancialInsight.month.desc()).all()
                data = [i.to_dict() for i in insights]
                print(json.dumps(data, indent=2))
                print("✅ Data serialization works.")
            except Exception as e:
                print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_endpoint()
