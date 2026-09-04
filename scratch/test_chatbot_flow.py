# scratch/test_chatbot_flow.py
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
from flask import Flask, g
from database import db
from app import app
from model import User, ChatMessage, AccountBalance, FinancialInsight

def test_chatbot():
    with app.app_context():
        # Get or create primary test user
        user1 = User.query.filter_by(email="test@aurestra.com").first()
        if not user1:
            user1 = User(email="test@aurestra.com", full_name="Test User One")
            db.session.add(user1)
            db.session.commit()
            print(f"Created primary user: {user1.email}")
            
        # Get or create secondary test user (to verify multi-tenant isolation)
        user2 = User.query.filter_by(email="other_user@aurestra.com").first()
        if not user2:
            user2 = User(email="other_user@aurestra.com", full_name="Leak Target User")
            db.session.add(user2)
            db.session.commit()
            print(f"Created secondary user: {user2.email}")

        # Bind encryption key to g for encrypted message/insight support
        g.encryption_key = b"1" * 32
        
        # Setup data for user 1 (Primary)
        acct1 = AccountBalance.query.filter_by(user_id=user1.id, source="bank").first()
        if not acct1:
            acct1 = AccountBalance(
                user_id=user1.id,
                source="bank",
                display_name="Bank Account 1",
                current_balance=25000.0,
                account_kind="bank"
            )
            db.session.add(acct1)
            db.session.commit()
            print("Created test account balance of 25,000 PKR for User 1")

        insight1 = FinancialInsight.query.filter_by(user_id=user1.id, month="2026-05").first()
        if not insight1:
            insight1 = FinancialInsight(
                user_id=user1.id,
                month="2026-05",
                content="User 1 Insight: Your food spending increased by 15%.",
                tags="dining_out_trend"
            )
            db.session.add(insight1)
            db.session.commit()
            print("Created test financial insight for User 1")

        # Setup data for user 2 (Secondary)
        acct2 = AccountBalance.query.filter_by(user_id=user2.id, source="bank").first()
        if not acct2:
            acct2 = AccountBalance(
                user_id=user2.id,
                source="bank",
                display_name="Bank Account 2",
                current_balance=999999.0,
                account_kind="bank"
            )
            db.session.add(acct2)
            db.session.commit()
            print("Created SECRET account balance of 999,999 PKR for User 2")

        insight2 = FinancialInsight.query.filter_by(user_id=user2.id, month="2026-05").first()
        if not insight2:
            insight2 = FinancialInsight(
                user_id=user2.id,
                month="2026-05",
                content="User 2 Insight: Secret offshore account details.",
                tags="secret_wealth"
            )
            db.session.add(insight2)
            db.session.commit()
            print("Created SECRET financial insight for User 2")

        from controllers import chat_controller as ctrl
        
        # Test Case 1: LIVE_FINANCIAL_DATA (Verify only User 1 data is returned, no leak of 999,999)
        print("\n" + "="*80)
        print("TEST CASE 1: LIVE_FINANCIAL_DATA (Balance Query for User 1)")
        print("="*80)
        with app.test_request_context(
            path='/api/chat',
            method='POST',
            json={"message": "What is my current bank balance?"}
        ):
            response, status = ctrl.chat_session(user1)
            print(f"Status Code: {status}")
            print(f"LLM Reply: {response.get_json() if hasattr(response, 'get_json') else response}")

        # Test Case 2: RAG_FINANCIAL_MEMORY (Verify only User 1 insight is returned, no leak of secret)
        print("\n" + "="*80)
        print("TEST CASE 2: RAG_FINANCIAL_MEMORY (Historical Query for User 1)")
        print("="*80)
        with app.test_request_context(
            path='/api/chat',
            method='POST',
            json={"message": "How did my spending change between January and February?"}
        ):
            response, status = ctrl.chat_session(user1)
            print(f"Status Code: {status}")
            print(f"LLM Reply: {response.get_json() if hasattr(response, 'get_json') else response}")

        # Test Case 3: FINANCIAL_PLANNING (Verify both Live and RAG insights are parsed and combined)
        print("\n" + "="*80)
        print("TEST CASE 3: FINANCIAL_PLANNING / HYBRID (Planning Query for User 1)")
        print("="*80)
        with app.test_request_context(
            path='/api/chat',
            method='POST',
            json={"message": "I want to budget and plan my finances for next month using my recent balances and trends."}
        ):
            response, status = ctrl.chat_session(user1)
            print(f"Status Code: {status}")
            print(f"LLM Reply: {response.get_json() if hasattr(response, 'get_json') else response}")

        # Clean history
        with app.test_request_context(path='/api/chat/history', method='DELETE'):
            ctrl.clear_chat_history(user1)
            ctrl.clear_chat_history(user2)

if __name__ == "__main__":
    test_chatbot()
