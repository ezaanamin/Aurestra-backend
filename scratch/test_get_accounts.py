import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from app import app
from database import db
from model import User, AccountBalance
from services.account_service import get_all_accounts

def run_test():
    with app.app_context():
        user = User.query.filter_by(email="ezaan.amin@gmail.com").first()
        if not user:
            print("❌ User not found!")
            return
        
        print(f"👤 Found User: ID={user.id}, Email={user.email}")
        
        # Test direct query
        try:
            accs = AccountBalance.query.filter_by(user_id=user.id).all()
            print(f"📊 Query AccountBalance directly: found {len(accs)} accounts.")
            for a in accs:
                print(f"  - {a.display_name} (slug: {a.source}): balance={a.current_balance}")
        except Exception as e:
            print(f"❌ Direct query failed: {e}")
            import traceback
            traceback.print_exc()
            
        # Test get_all_accounts service
        try:
            result = get_all_accounts(user.id)
            print(f"✅ get_all_accounts returned {len(result)} items:")
            for r in result:
                print(f"  - {r}")
        except Exception as e:
            print(f"❌ get_all_accounts failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_test()
