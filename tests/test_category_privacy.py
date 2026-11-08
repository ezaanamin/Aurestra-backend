import unittest
import os
import json
from datetime import datetime, timedelta
import jwt
from app import app
from database import db
from model import User, Category, CategoryPrivacySetting, Transaction, AccountBalance
from utils.crypto_helpers import hash_decryption_key

class TestCategoryPrivacy(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        self.user_email = "test_privacy_user@example.com"
        self.decryption_key = "SuperSecretDecryptionKey123!"
        self.user = User.query.filter_by(email=self.user_email).first()
        if not self.user:
            self.user = User(
                email=self.user_email,
                name="Privacy Test User",
                password_hash="test_pw_hash",
                decryption_key_hash=hash_decryption_key(self.decryption_key)
            )
            db.session.add(self.user)
            db.session.commit()
        else:
            self.user.decryption_key_hash = hash_decryption_key(self.decryption_key)
            db.session.commit()

        self.user_id = self.user.id

        # Clean existing test data
        CategoryPrivacySetting.query.filter_by(user_id=self.user_id).delete()
        Transaction.query.filter_by(user_id=self.user_id).delete()
        db.session.commit()

        secret = os.getenv("SECRET_KEY") or "0aefb44af279f5bb0ad9ecce393be138"
        self.app.config["SECRET_KEY"] = secret
        self.token = jwt.encode(
            {
                "user_id": self.user_id,
                "email": self.user_email,
                "exp": datetime.utcnow() + timedelta(days=1),
            },
            secret,
            algorithm="HS256"
        )
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        # Create or find test categories
        self.cat1 = Category.query.filter_by(name="Shopping", user_id=self.user_id).first()
        if not self.cat1:
            self.cat1 = Category(name="Shopping", user_id=self.user_id, color="#A78BFA", is_default=False)
            db.session.add(self.cat1)
            db.session.commit()

        self.cat2 = Category.query.filter_by(name="Therapy", user_id=self.user_id).first()
        if not self.cat2:
            self.cat2 = Category(name="Therapy", user_id=self.user_id, color="#8B5CF6", is_default=False)
            db.session.add(self.cat2)
            db.session.commit()

    def tearDown(self):
        CategoryPrivacySetting.query.filter_by(user_id=self.user_id).delete()
        Transaction.query.filter_by(user_id=self.user_id).delete()
        db.session.commit()
        self.app_context.pop()

    def test_verify_privacy_key_endpoint(self):
        # 1. Invalid key should be rejected
        res = self.client.post(
            "/api/categories/privacy/verify-key",
            headers=self.headers,
            data=json.dumps({"decryption_key": "WrongKey"})
        )
        self.assertEqual(res.status_code, 403)

        # 2. Valid key should succeed
        res = self.client.post(
            "/api/categories/privacy/verify-key",
            headers=self.headers,
            data=json.dumps({"decryption_key": self.decryption_key})
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json.get("success"))

    def test_set_category_privacy_requires_key(self):
        # Without key should fail 400
        res = self.client.post(
            "/api/categories/privacy",
            headers=self.headers,
            data=json.dumps({"category_id": self.cat1.id, "is_retracted": True})
        )
        self.assertEqual(res.status_code, 400)

        # With wrong key should fail 403
        res = self.client.post(
            "/api/categories/privacy",
            headers=self.headers,
            data=json.dumps({"decryption_key": "BadKey", "category_id": self.cat1.id, "is_retracted": True})
        )
        self.assertEqual(res.status_code, 403)

        # With correct key should succeed 200
        res = self.client.post(
            "/api/categories/privacy",
            headers=self.headers,
            data=json.dumps({
                "decryption_key": self.decryption_key,
                "category_id": self.cat1.id,
                "is_retracted": True
            })
        )
        self.assertEqual(res.status_code, 200)

        # Verify persisted in CategoryPrivacySetting
        setting = CategoryPrivacySetting.query.filter_by(user_id=self.user_id, category_id=self.cat1.id).first()
        self.assertIsNotNone(setting)
        self.assertTrue(setting.is_retracted)

    def test_analytics_dashboard_privacy_masking_and_grouping(self):
        # Set cat1 and cat2 as retracted
        s1 = CategoryPrivacySetting(user_id=self.user_id, category_id=self.cat1.id, is_retracted=True)
        s2 = CategoryPrivacySetting(user_id=self.user_id, category_id=self.cat2.id, is_retracted=True)
        db.session.add_all([s1, s2])

        # Add debits for both categories
        now = datetime.now()
        t1 = Transaction(
            user_id=self.user_id,
            category_id=self.cat1.id,
            purpose="Shopping",
            amount=5000.0,
            type="debit",
            source="manual",
            date=now
        )
        t2 = Transaction(
            user_id=self.user_id,
            category_id=self.cat2.id,
            purpose="Therapy",
            amount=3000.0,
            type="debit",
            source="manual",
            date=now
        )
        db.session.add_all([t1, t2])
        db.session.commit()

        # Query analytics dashboard
        from services.analytics_service import get_analytics_dashboard_data
        data = get_analytics_dashboard_data(self.user_id, period="month")

        # Total spent should be 8000.0
        self.assertEqual(data["total_spent"], 8000.0)

        # Breakdown should consolidate both retracted categories under a single "Retracted" item
        breakdown = data["breakdown"]
        retracted_entries = [b for b in breakdown if b["name"] == "Retracted"]
        self.assertEqual(len(retracted_entries), 1)
        self.assertEqual(retracted_entries[0]["amount"], 8000.0)
        self.assertEqual(retracted_entries[0]["color"], "#64748B")
        self.assertTrue(retracted_entries[0]["is_retracted"])
        # Original category names Shopping and Therapy must NOT appear
        self.assertNotIn("Shopping", [b["name"] for b in breakdown])
        self.assertNotIn("Therapy", [b["name"] for b in breakdown])

if __name__ == "__main__":
    unittest.main()
