import unittest
import os
from datetime import datetime, timedelta
import json
import jwt
from app import app
from database import db
from model import User, AccountBalance, Transaction, Category, MonthlyBalance
from services import account_service, transaction_service, budget_service


class TestFinanceBalanceLogic(unittest.TestCase):
    """
    Automated test suite verifying the 8 core finance balance and transaction logic requirements:
    TEST 1: Income additions increase balance.
    TEST 2: Expense additions decrease balance and are stored positively.
    TEST 3: Overdraft prevention rejects expenses exceeding balance with exact message.
    TEST 4: Account isolation ensures Bank expense doesn't mutate Cash.
    TEST 5: Account isolation ensures Cash income doesn't mutate Bank.
    TEST 6: Dashboard calculations (positive expenses, exact total income, net cash flow).
    TEST 7: Direct account balance updates persist to DB, survive reload, and return via API.
    TEST 8: Full expense lifecycle (create -> edit lower -> edit higher -> edit overdraft -> delete).
    """

    def setUp(self):
        self.app = app
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        # Unique test user
        self.user_email = "test_finance_balance_logic@example.com"
        self.user = User.query.filter_by(email=self.user_email).first()
        if not self.user:
            self.user = User(
                email=self.user_email,
                name="Finance Test User",
                password_hash="test_password_hash"
            )
            db.session.add(self.user)
            db.session.commit()

        self.user_id = self.user.id

        # Clean existing test data for this user
        Transaction.query.filter_by(user_id=self.user_id).delete()
        AccountBalance.query.filter_by(user_id=self.user_id).delete()
        MonthlyBalance.query.filter_by(user_id=self.user_id).delete()
        db.session.commit()

        # Ensure SECRET_KEY on app.config
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

        from utils.crypto_helpers import hash_decryption_key, generate_crypto_salt
        self.test_vault_key = "test_vault_key_1234567890"
        self.user.decryption_key_salt = generate_crypto_salt()
        self.user.decryption_key_hash = hash_decryption_key(self.test_vault_key)
        self.user.decryption_key = self.test_vault_key
        db.session.commit()

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Decryption-Key": self.test_vault_key,
            "Content-Type": "application/json"
        }

    def tearDown(self):
        # Clean up test user's data
        try:
            Transaction.query.filter_by(user_id=self.user_id).delete()
            AccountBalance.query.filter_by(user_id=self.user_id).delete()
            MonthlyBalance.query.filter_by(user_id=self.user_id).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
        self.app_context.pop()

    def _create_account(self, source, display_name, initial_balance=0.0, account_kind="bank"):
        acc = AccountBalance(
            user_id=self.user_id,
            source=source,
            display_name=display_name,
            account_kind=account_kind,
            current_balance=float(initial_balance),
            accent_color="#00C9A7",
            is_manual=True
        )
        db.session.add(acc)
        db.session.commit()
        return acc

    # -------------------------------------------------------------------------
    # TEST 1: Account has 10,000. Add income of 2,000. Balance becomes 12,000.
    # -------------------------------------------------------------------------
    def test_1_account_income(self):
        acc = self._create_account("bank_test1", "Test Bank 1", 10000.00)

        payload = {
            "amount": 2000.00,
            "type": "credit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source,
            "sender": "Employer",
            "receiver": "Self",
            "purpose": "Salary",
            "category": "Salary",
            "date": datetime.utcnow().isoformat()
        }

        res = self.client.post("/api/transactions", headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 201, f"Expected 201, got {res.status_code}: {res.data}")

        # Check DB balance
        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 12000.00)

        # Check API accounts balance
        res_acc = self.client.get("/api/accounts", headers=self.headers)
        self.assertEqual(res_acc.status_code, 200)
        acc_data = next((a for a in res_acc.get_json() if a["id"] == acc.id), None)
        self.assertIsNotNone(acc_data)
        self.assertEqual(round(acc_data["balance"], 2), 12000.00)

    # -------------------------------------------------------------------------
    # TEST 2: Account has 10,000. Add expense of 2,000. Balance becomes 8,000.
    # -------------------------------------------------------------------------
    def test_2_account_expense(self):
        acc = self._create_account("bank_test2", "Test Bank 2", 10000.00)

        payload = {
            "amount": 2000.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source,
            "sender": "Self",
            "receiver": "Grocery Store",
            "purpose": "Groceries",
            "category": "Groceries",
            "date": datetime.utcnow().isoformat()
        }

        res = self.client.post("/api/transactions", headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 201, f"Expected 201, got {res.status_code}: {res.data}")

        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 8000.00)

        # Ensure transaction amount stored as positive 2000.00
        tx = Transaction.query.filter_by(user_id=self.user.id).order_by(Transaction.id.desc()).first()
        self.assertIsNotNone(tx)
        self.assertEqual(round(tx.amount, 2), 2000.00)
        self.assertEqual(tx.type, "debit")

    # -------------------------------------------------------------------------
    # TEST 3: Account has 10,000. Attempt expense of 11,000.
    # Rejected with error "Insufficient balance for this expense." Balance remains 10,000.
    # -------------------------------------------------------------------------
    def test_3_overdraft_prevention(self):
        acc = self._create_account("bank_test3", "Test Bank 3", 10000.00)

        payload = {
            "amount": 11000.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source,
            "sender": "Self",
            "receiver": "Electronics Store",
            "purpose": "Laptop",
            "category": "Shopping",
            "date": datetime.utcnow().isoformat()
        }

        res = self.client.post("/api/transactions", headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 400)
        res_json = res.get_json()
        self.assertEqual(res_json.get("error"), "Insufficient balance for this expense.")

        # Balance remains untouched at 10,000.00
        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 10000.00)

        # No transaction was recorded
        tx_count = Transaction.query.filter_by(user_id=self.user.id).count()
        self.assertEqual(tx_count, 0)

    # -------------------------------------------------------------------------
    # TEST 4: Two accounts: Cash (5,000) and Bank (10,000). Add expense of 2,000 to Bank.
    # Bank becomes 8,000. Cash remains 5,000.
    # -------------------------------------------------------------------------
    def test_4_account_isolation_expense(self):
        cash_acc = self._create_account(f"cash_{self.user.id}", "Cash Wallet", 5000.00, account_kind="cash")
        bank_acc = self._create_account(f"bank_test4_{self.user.id}", "Bank Account", 10000.00, account_kind="bank")

        payload = {
            "amount": 2000.00,
            "type": "debit",
            "financial_account_id": bank_acc.id,
            "account_balance_source": bank_acc.source,
            "sender": "Self",
            "receiver": "Restaurant",
            "purpose": "Dining",
            "category": "Food",
            "date": datetime.utcnow().isoformat()
        }

        res = self.client.post("/api/transactions", headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 201)

        db.session.expire_all()
        refreshed_bank = AccountBalance.query.get(bank_acc.id)
        refreshed_cash = AccountBalance.query.get(cash_acc.id)

        self.assertEqual(round(refreshed_bank.current_balance, 2), 8000.00)
        self.assertEqual(round(refreshed_cash.current_balance, 2), 5000.00, "Cash balance must NOT be affected by Bank expense")

    # -------------------------------------------------------------------------
    # TEST 5: Two accounts: Cash (5,000) and Bank (10,000). Add income of 3,000 to Cash.
    # Cash becomes 8,000. Bank remains 10,000.
    # -------------------------------------------------------------------------
    def test_5_account_isolation_income(self):
        cash_acc = self._create_account(f"cash_{self.user.id}", "Cash Wallet", 5000.00, account_kind="cash")
        bank_acc = self._create_account(f"bank_test5_{self.user.id}", "Bank Account", 10000.00, account_kind="bank")

        payload = {
            "amount": 3000.00,
            "type": "credit",
            "financial_account_id": cash_acc.id,
            "account_balance_source": cash_acc.source,
            "sender": "Client",
            "receiver": "Self",
            "purpose": "Cash Payment",
            "category": "Income",
            "date": datetime.utcnow().isoformat()
        }

        res = self.client.post("/api/transactions", headers=self.headers, json=payload)
        self.assertEqual(res.status_code, 201)

        db.session.expire_all()
        refreshed_cash = AccountBalance.query.get(cash_acc.id)
        refreshed_bank = AccountBalance.query.get(bank_acc.id)

        self.assertEqual(round(refreshed_cash.current_balance, 2), 8000.00)
        self.assertEqual(round(refreshed_bank.current_balance, 2), 10000.00, "Bank balance must NOT be affected by Cash income")

    # -------------------------------------------------------------------------
    # TEST 6: Multiple transactions:
    # Income 10,000, Income 5,000, Expense 3,000, Expense 6,785.
    # Dashboard shows: Total Income = 15,000, Total Expenses = 9,785 (positive number),
    # Net Cash Flow = 5,215. Expense is NOT negative.
    # -------------------------------------------------------------------------
    def test_6_dashboard_income_positive_expenses_net_cash_flow(self):
        acc = self._create_account(f"bank_test6_{self.user.id}", "Bank Account", 20000.00)

        current_month = datetime.utcnow().strftime("%Y-%m")
        now_iso = datetime.utcnow().isoformat()

        # Add 2 incomes: 10,000 and 5,000
        self.client.post("/api/transactions", headers=self.headers, json={
            "amount": 10000.00, "type": "credit", "financial_account_id": acc.id,
            "account_balance_source": acc.source, "date": now_iso
        })
        self.client.post("/api/transactions", headers=self.headers, json={
            "amount": 5000.00, "type": "credit", "financial_account_id": acc.id,
            "account_balance_source": acc.source, "date": now_iso
        })

        # Add 2 expenses: 3,000 and 6,785
        self.client.post("/api/transactions", headers=self.headers, json={
            "amount": 3000.00, "type": "debit", "financial_account_id": acc.id,
            "account_balance_source": acc.source, "date": now_iso
        })
        self.client.post("/api/transactions", headers=self.headers, json={
            "amount": 6785.00, "type": "debit", "financial_account_id": acc.id,
            "account_balance_source": acc.source, "date": now_iso
        })

        # Test calculation via budget_service and /api/calculate endpoint
        summary = budget_service.get_monthly_summary(self.user.id)

        self.assertEqual(round(summary["total_income"], 2), 15000.00, "Total income should be 15,000.00")
        self.assertEqual(round(summary["total_expense"], 2), 9785.00, "Total expenses should be 9,785.00 (positive)")
        self.assertGreater(summary["total_expense"], 0, "Expenses must be strictly positive")
        self.assertEqual(round(summary["net_cash_flow"], 2), 5215.00, "Net cash flow should be 5,215.00 (15000 - 9785)")

        # Verify via API endpoint /api/monthly-summary
        res = self.client.get("/api/monthly-summary", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        api_data = res.get_json()
        self.assertEqual(round(api_data["total_income"], 2), 15000.00)
        self.assertEqual(round(api_data["total_expense"], 2), 9785.00)
        self.assertGreater(api_data["total_expense"], 0)
        self.assertEqual(round(api_data["net_cash_flow"], 2), 5215.00)

        # Also verify via /api/calculate-summary
        res_calc = self.client.post("/api/calculate-summary", headers=self.headers, json={"month": current_month})
        self.assertEqual(res_calc.status_code, 200)
        calc_data = res_calc.get_json()["data"]["summary"]
        self.assertEqual(round(calc_data.get("expense", 0), 2), 9785.00)
        self.assertGreater(calc_data.get("expense", 0), 0)
        self.assertEqual(round(calc_data.get("savings", 0), 2), 5215.00)

        # Verify DB MonthlyBalance record does not have negative expense
        mb = MonthlyBalance.query.filter_by(user_id=self.user.id, month=current_month).first()
        self.assertIsNotNone(mb)
        self.assertEqual(round(mb.expense, 2), 9785.00)
        self.assertGreater(mb.expense, 0, "DB MonthlyBalance.expense must be positive")

    # -------------------------------------------------------------------------
    # TEST 7: Update account balance directly: Account balance changed from X to Y.
    # DB reflects Y. API returns Y. Reloading app shows Y.
    # -------------------------------------------------------------------------
    def test_7_direct_balance_persistence_and_reload(self):
        # Start at X = 5,000.00
        acc = self._create_account("bank_test7", "Test Bank 7", 5000.00)

        # Update balance directly to Y = 12,500.00 via set_balance endpoint
        set_payload = {"account_id": acc.id, "amount": 12500.00}
        res = self.client.post("/api/accounts/set_balance", headers=self.headers, json=set_payload)
        self.assertEqual(res.status_code, 200)

        # 1. DB reflects Y
        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 12500.00)

        # 2. API returns Y
        res_acc = self.client.get("/api/accounts", headers=self.headers)
        self.assertEqual(res_acc.status_code, 200)
        acc_dict = next(a for a in res_acc.get_json() if a["id"] == acc.id)
        self.assertEqual(round(acc_dict["balance"], 2), 12500.00)

        # 3. Simulate reload by querying with fresh DB session and fresh API fetch
        db.session.remove()
        res_reload = self.client.get("/api/accounts", headers=self.headers)
        self.assertEqual(res_reload.status_code, 200)
        reloaded_dict = next(a for a in res_reload.get_json() if a["id"] == acc.id)
        self.assertEqual(round(reloaded_dict["balance"], 2), 12500.00)

        # Also test updating via PUT /api/accounts/<id> with balance: 14000.00
        put_payload = {"display_name": "Test Bank 7 Renamed", "balance": 14000.00}
        res_put = self.client.put(f"/api/accounts/{acc.id}", headers=self.headers, json=put_payload)
        self.assertEqual(res_put.status_code, 200)
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 14000.00)

        # Also test Cash account: verify PUT /api/accounts/<cash_id> succeeds and updates balance
        cash_acc = AccountBalance.query.filter_by(user_id=self.user_id, source="cash").first()
        if not cash_acc:
            cash_acc = self._create_account("cash", "Cash", 5000.00, account_kind="cash")
        put_cash = {"id": cash_acc.id, "display_name": "Cash", "account_kind": "cash", "balance": 8200.00, "accent_color": "#00C9A7"}
        res_cash_put = self.client.put(f"/api/accounts/{cash_acc.id}", headers=self.headers, json=put_cash)
        self.assertEqual(res_cash_put.status_code, 200)
        db.session.expire_all()
        refreshed_cash = AccountBalance.query.get(cash_acc.id)
        self.assertEqual(round(refreshed_cash.current_balance, 2), 8200.00)

        # Test Cash account balance update via /api/accounts/set_balance
        res_cash_set = self.client.post("/api/accounts/set_balance", headers=self.headers, json={"account_id": cash_acc.id, "amount": 9500.00})
        self.assertEqual(res_cash_set.status_code, 200)
        db.session.expire_all()
        refreshed_cash = AccountBalance.query.get(cash_acc.id)
        self.assertEqual(round(refreshed_cash.current_balance, 2), 9500.00)

    # -------------------------------------------------------------------------
    # TEST 8: Create expense, verify balance decreases. Edit expense to a lower amount,
    # verify balance adjusts correctly. Edit expense to a higher amount, verify balance adjusts.
    # Delete expense, verify balance fully restored.
    # -------------------------------------------------------------------------
    def test_8_expense_lifecycle_create_edit_delete(self):
        acc = self._create_account("bank_test8", "Test Bank 8", 10000.00)

        # Step 1: Create expense of 4,000 -> balance becomes 6,000
        create_res = self.client.post("/api/transactions", headers=self.headers, json={
            "amount": 4000.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source,
            "date": datetime.utcnow().isoformat()
        })
        self.assertEqual(create_res.status_code, 201)
        tx_id = create_res.get_json()["transaction"]["id"]

        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 6000.00)

        # Step 2: Edit expense to lower amount: 2,500 -> balance becomes 7,500
        edit_res1 = self.client.put(f"/api/transactions/{tx_id}", headers=self.headers, json={
            "amount": 2500.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source
        })
        self.assertEqual(edit_res1.status_code, 200)

        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 7500.00, "Balance should adjust up to 7,500.00")

        # Step 3: Edit expense to higher amount: 5,000 -> balance becomes 5,000
        edit_res2 = self.client.put(f"/api/transactions/{tx_id}", headers=self.headers, json={
            "amount": 5000.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source
        })
        self.assertEqual(edit_res2.status_code, 200)

        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 5000.00, "Balance should adjust down to 5,000.00")

        # Step 3b: Edit expense to overdraft amount: 16,000 (available is 5000 + 5000 = 10000) -> rejected
        edit_res_overdraft = self.client.put(f"/api/transactions/{tx_id}", headers=self.headers, json={
            "amount": 16000.00,
            "type": "debit",
            "financial_account_id": acc.id,
            "account_balance_source": acc.source
        })
        self.assertEqual(edit_res_overdraft.status_code, 400)
        self.assertEqual(edit_res_overdraft.get_json().get("error"), "Insufficient balance for this expense.")

        # Balance remains 5,000.00
        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 5000.00)

        # Step 4: Delete expense -> balance restored to 10,000.00
        del_res = self.client.delete(f"/api/transactions/{tx_id}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)

        db.session.expire_all()
        refreshed_acc = AccountBalance.query.get(acc.id)
        self.assertEqual(round(refreshed_acc.current_balance, 2), 10000.00, "Balance must be fully restored to 10,000.00 after deletion")


if __name__ == "__main__":
    unittest.main()
