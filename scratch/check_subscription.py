from app import app, db
from model import User, Plan
from services.subscription_service import can_access, get_user_subscription_info, has_remaining_limit, seed_plans

def check_subscription_logic():
    with app.app_context():
        print("🌱 Seeding plans just in case...")
        seed_plans()
        
        # Get first user
        user = User.query.first()
        if not user:
            print("❌ No users found in database to test with.")
            return
            
        print(f"\n👤 Testing with user: {user.email}")
        print(f"Current plan_id: {user.current_plan_id}")
        
        # Test get subscription info
        print("\n📊 Getting user subscription info:")
        info = get_user_subscription_info(user)
        import json
        print(json.dumps(info, indent=2))
        
        # Test can_access
        print("\n🔑 Feature Permissions:")
        for feature in ["ai_chat", "investment_planning", "deep_ai_analysis", "ai_reports"]:
            allowed = can_access(user, feature)
            print(f" - {feature}: {'ALLOWED' if allowed else 'BLOCKED'}")
            
        # Test limits
        print("\n📈 Limits Remaining:")
        for limit in ["ai_chat", "ocr"]:
            has_limit = has_remaining_limit(user, limit)
            print(f" - {limit} has remaining: {has_limit}")

        # Simulate upgrading to Pro
        print("\n🚀 Simulating upgrade to Pro...")
        user.current_plan_id = "pro"
        db.session.commit()
        
        info = get_user_subscription_info(user)
        print(f"New plan: {info['current_plan']['name']}")
        print(f"Investment planning allowed: {can_access(user, 'investment_planning')}")
        print(f"Deep AI analysis allowed: {can_access(user, 'deep_ai_analysis')}")

        # Simulate developer bypass
        print("\n🛠️ Simulating Developer tier...")
        user.current_plan_id = "developer"
        db.session.commit()
        
        info = get_user_subscription_info(user)
        print(f"New plan: {info['current_plan']['name']}")
        print(f"Investment planning allowed: {can_access(user, 'investment_planning')}")
        print(f"AI Chat limit remaining: {info['limits_remaining']['ai_chat']['remaining']}")
        
        # Reset back to free
        print("\n🔄 Resetting back to Free plan...")
        user.current_plan_id = "free"
        db.session.commit()
        print("Done.")
            
if __name__ == "__main__":
    check_subscription_logic()
