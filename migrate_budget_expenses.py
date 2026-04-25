"""
Migration: Add total_expenses column to the budgets table.
Run once: python migrate_budget_expenses.py
"""
from app import app, db
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text(
            "ALTER TABLE budgets ADD COLUMN total_expenses FLOAT NOT NULL DEFAULT 0.0"
        ))
        db.session.commit()
        print("✅ Column 'total_expenses' added to budgets table.")
    except Exception as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            print("ℹ️  Column 'total_expenses' already exists — skipping.")
        else:
            print(f"❌ Migration failed: {e}")
