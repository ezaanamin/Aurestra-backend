import unittest
from datetime import datetime
from database import app, db
from model import Transaction, Category, User
from services.analytics_service import get_analytics_dashboard_data
from services.transaction_service import create_manual_transaction, update_transaction_category
from migrate_bank_reduction import migrate_bank_reduction_data

class TestAnalyticsAndCategorization(unittest.TestCase):

    def setUp(self):
        self.app_context = app.app_context()
        self.app_context.push()
        db.create_all()

        # Ensure bank_reduction_reason exists in SQLite DB
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                tx_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(transactions);")).fetchall()]
                if 'bank_reduction_reason' not in tx_cols:
                    conn.execute(text("ALTER TABLE transactions ADD COLUMN bank_reduction_reason VARCHAR(255);"))
                    conn.commit()
        except Exception:
            pass

        # Create test user
        self.user = User.query.filter_by(email="analytics_test@example.com").first()
        if not self.user:
            self.user = User(
                email="analytics_test@example.com",
                name="Analytics Test User",
                password_hash="dummy_hash"
            )
            db.session.add(self.user)
            db.session.commit()

        # Seed categories if needed
        self.bank_cat = Category.query.filter(Category.name.ilike("bank reduction")).first()
        if not self.bank_cat:
            self.bank_cat = Category(
                name="Bank Reduction",
                icon="bank",
                color="#FF6B8A",
                cat_type="spending",
                is_default=True
            )
            db.session.add(self.bank_cat)

        self.uber_cat = Category.query.filter(Category.name.ilike("uber")).first()
        if not self.uber_cat:
            self.uber_cat = Category(
                name="Uber",
                icon="car",
                color="#4ECDC4",
                cat_type="spending",
                is_default=True
            )
            db.session.add(self.uber_cat)

        db.session.commit()

        # Clean old test transactions for this user
        Transaction.query.filter_by(user_id=self.user.id).delete()
        db.session.commit()

    def tearDown(self):
        Transaction.query.filter_by(user_id=self.user.id).delete()
        db.session.commit()
        self.app_context.pop()

    def test_canonical_aggregation_eliminates_duplicates(self):
        """Test that multiple Uber transactions are aggregated into a single entry with summed amount."""
        t1 = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=515.0,
            type="debit",
            purpose="Uber",
            category_id=self.uber_cat.id,
            categorization_status="confirmed"
        )
        t2 = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=500.0,
            type="debit",
            purpose="Uber",
            category_id=self.uber_cat.id,
            categorization_status="confirmed"
        )
        t3 = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=300.0,
            type="debit",
            purpose="Uber",
            category_id=self.uber_cat.id,
            categorization_status="confirmed"
        )
        db.session.add_all([t1, t2, t3])
        db.session.commit()

        data = get_analytics_dashboard_data(self.user.id, period="month")
        breakdown = data["breakdown"]

        uber_entries = [b for b in breakdown if b["name"] == "Uber"]
        self.assertEqual(len(uber_entries), 1, "There must be exactly one Uber entry in the spending breakdown")
        self.assertEqual(uber_entries[0]["amount"], 1315.0, "Uber total amount must be aggregated to 1315.0")

    def test_self_transfer_exclusion(self):
        """Test that self-transfers are strictly excluded from analytics dashboard totals and breakdowns."""
        t_spending = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=200.0,
            type="debit",
            purpose="Food & Snacks",
            categorization_status="confirmed"
        )
        t_self = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=5000.0,
            type="debit",
            purpose="Self-transfer",
            categorization_status="confirmed"
        )
        db.session.add_all([t_spending, t_self])
        db.session.commit()

        data = get_analytics_dashboard_data(self.user.id, period="month")
        self.assertEqual(data["total_spent"], 200.0, "Self transfers must not be included in total_spent")
        breakdown_names = [b["name"] for b in data["breakdown"]]
        self.assertNotIn("Self-transfer", breakdown_names)

    def test_bank_reduction_mandatory_reason_validation(self):
        """Test that Bank Reduction transactions require a bank_reduction_reason."""
        from model import AccountBalance
        acc = AccountBalance.query.filter_by(user_id=self.user.id, source="bank").first()
        if not acc:
            acc = AccountBalance(user_id=self.user.id, source="bank", display_name="Bank", current_balance=10000.0)
            db.session.add(acc)
        else:
            acc.current_balance = 10000.0
        db.session.commit()

        with self.assertRaises(ValueError):
            create_manual_transaction(self.user.id, {
                "amount": 100,
                "type": "debit",
                "category": "Bank Reduction",
                "account_balance_source": "bank"
            })

        # Valid creation
        tx, _ = create_manual_transaction(self.user.id, {
            "amount": 100,
            "type": "debit",
            "category": "Bank Reduction",
            "bank_reduction_reason": "Tax / FED",
            "account_balance_source": "bank"
        })
        self.assertEqual(tx.bank_reduction_reason, "Tax / FED")

    def test_bank_reduction_historical_migration(self):
        """Test that historical bank reduction transactions are mapped properly."""
        t_legacy = Transaction(
            user_id=self.user.id,
            source="manual",
            date=datetime.now(),
            amount=50.0,
            type="debit",
            purpose="Bank Reduction",
            notes="FED on transaction",
            categorization_status="confirmed"
        )
        db.session.add(t_legacy)
        db.session.commit()

        migrate_bank_reduction_data()
        db.session.refresh(t_legacy)

        self.assertEqual(t_legacy.bank_reduction_reason, "Tax / FED")

if __name__ == "__main__":
    unittest.main()
