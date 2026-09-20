import os
import shutil
import tempfile
import unittest
from datetime import datetime
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 1. Copy the real database to an isolated temporary file BEFORE importing app
REAL_DB_PATH = os.path.abspath("aurestra.db")
temp_dir = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(temp_dir, "test_copy_aurestra.db")
shutil.copyfile(REAL_DB_PATH, TEST_DB_PATH)

# Point SQLALCHEMY_DATABASE_URI to the test copy
os.environ["SQLITE_PATH"] = TEST_DB_PATH

from app import app, db
from model import User, AccountBalance, Transaction

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{TEST_DB_PATH}"

class TestAssistantAPIIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.test_token = "test-secret-assistant-token-48-characters-long-example"
        cls.primary_email = "ezaan.amin@gmail.com"
        cls.secondary_email = "second_user_isolated@example.com"
        
        os.environ["ASSISTANT_API_TOKEN"] = cls.test_token
        os.environ["ASSISTANT_USER_EMAIL"] = cls.primary_email

        with app.app_context():
            # Verify primary user exists
            primary_user = User.query.filter_by(email=cls.primary_email).first()
            if not primary_user:
                primary_user = User(email=cls.primary_email, name="Ezaan Amin")
                db.session.add(primary_user)
                db.session.commit()
            cls.primary_user_id = primary_user.id

            # Create normal app login JWT for primary user
            cls.primary_jwt = jwt.encode(
                {"user_id": cls.primary_user_id},
                app.config["SECRET_KEY"],
                algorithm="HS256"
            )

            # Insert SECOND user into the TEST COPY with distinctive accounts and transactions
            second_user = User(email=cls.secondary_email, name="Second Isolated User")
            db.session.add(second_user)
            db.session.commit()
            cls.second_user_id = second_user.id

            # Distinctive wallet for second user
            sec_account = AccountBalance(
                user_id=cls.second_user_id,
                source="secret_second_user_wallet",
                display_name="Second User Secret Wallet",
                account_kind="bank",
                current_balance=999999.00
            )
            db.session.add(sec_account)

            # Distinctive transaction for second user
            sec_tx = Transaction(
                user_id=cls.second_user_id,
                source="manual",
                date=datetime.now(),
                amount=88888.00,
                type="debit",
                purpose="SecretSecondUserCategory",
                receiver="SecretMerchant999",
                notes="TopSecretPurchase",
                categorization_status="confirmed",
                account_balance_source="secret_second_user_wallet"
            )
            db.session.add(sec_tx)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        # Remove the temporary test database copy and directory
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_01_no_token_returns_401(self):
        resp = self.client.get("/api/assistant/finance-snapshot")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json(), {"error": "Unauthorized"})

    def test_02_wrong_token_returns_401(self):
        resp = self.client.get(
            "/api/assistant/finance-snapshot",
            headers={"Authorization": "Bearer wrong-token-value"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json(), {"error": "Unauthorized"})

    def test_03_app_jwt_on_assistant_route_returns_401(self):
        resp = self.client.get(
            "/api/assistant/finance-snapshot",
            headers={"Authorization": f"Bearer {self.primary_jwt}"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json(), {"error": "Unauthorized"})

    def test_04_assistant_token_on_app_routes_returns_401(self):
        resp = self.client.get(
            "/api/accounts",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_05_correct_token_snapshot_returns_200_and_schema(self):
        resp = self.client.get(
            "/api/assistant/finance-snapshot",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["currency"], "PKR")
        self.assertIn("generated_at", data)
        self.assertIn("net_worth", data)
        self.assertIn("cash", data)
        self.assertIn("investments", data)
        self.assertIn("wallets", data)
        self.assertIn("spending", data)
        self.assertIn("alerts", data)

    def test_06_spending_invalid_period_returns_400(self):
        for invalid_period in ["2026-13", "abc", "2026", "2026/05", "2026-00"]:
            resp = self.client.get(
                f"/api/assistant/spending?period={invalid_period}",
                headers={"Authorization": f"Bearer {self.test_token}"}
            )
            self.assertEqual(resp.status_code, 400, f"Period {invalid_period} must return 400")
            self.assertIn("error", resp.get_json())

    def test_07_spending_valid_period_no_category_returns_200(self):
        resp = self.client.get(
            "/api/assistant/spending?period=2026-09",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["currency"], "PKR")
        self.assertEqual(data["period"], "2026-09")
        self.assertEqual(data["transactions"], [])

    def test_08_spending_with_category_returns_200_and_transactions(self):
        resp = self.client.get(
            "/api/assistant/spending?period=2026-09&category=Food%20%26%20Snacks",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["currency"], "PKR")
        self.assertIsInstance(data["transactions"], list)
        self.assertLessEqual(len(data["transactions"]), 20)

    def test_09_user_isolation_second_user_data_never_leaks(self):
        # 1. Snapshot must NOT contain second user's wallet or transaction category
        resp = self.client.get(
            "/api/assistant/finance-snapshot",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        
        wallet_slugs = [w["slug"] for w in data["wallets"]]
        self.assertNotIn("secret_second_user_wallet", wallet_slugs)
        self.assertTrue(all(w["balance"] != 999999.00 for w in data["wallets"]))
        
        cat_names = [c["category"] for c in data["spending"]["top_categories"]]
        self.assertNotIn("SecretSecondUserCategory", cat_names)

        # 2. Attacking with query params (?user_id=, ?email=, ?account_id=) changes NOTHING
        resp_attack = self.client.get(
            f"/api/assistant/finance-snapshot?user_id={self.second_user_id}&email={self.secondary_email}&account_id=999",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp_attack.status_code, 200)
        data_attack = resp_attack.get_json()
        self.assertEqual(data["net_worth"]["amount"], data_attack["net_worth"]["amount"])
        self.assertEqual(data["wallets"], data_attack["wallets"])
        self.assertEqual(data["spending"]["total"], data_attack["spending"]["total"])

        # 3. Querying spending for second user's category returns 0 for primary user
        curr_period = datetime.now().strftime("%Y-%m")
        resp_spend = self.client.get(
            f"/api/assistant/spending?period={curr_period}&category=SecretSecondUserCategory&user_id={self.second_user_id}",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp_spend.status_code, 200)
        spend_data = resp_spend.get_json()
        self.assertEqual(spend_data["total"], 0)
        self.assertEqual(spend_data["categories"], [])
        self.assertEqual(spend_data["transactions"], [])

    def test_10_assistant_user_email_not_found_returns_401(self):
        os.environ["ASSISTANT_USER_EMAIL"] = "does_not_exist_in_db@example.com"
        resp = self.client.get(
            "/api/assistant/finance-snapshot",
            headers={"Authorization": f"Bearer {self.test_token}"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json(), {"error": "Unauthorized"})
        os.environ["ASSISTANT_USER_EMAIL"] = self.primary_email

if __name__ == "__main__":
    unittest.main()
