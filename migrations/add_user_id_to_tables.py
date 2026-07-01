#!/usr/bin/env python3
"""
migrations/add_user_id_to_tables.py
────────────────────────────────────
Phase 2 Migration: Add user_id FK (nullable) to all data tables.
Assigns all existing rows to user id=1 (the existing single user).

Uses only stdlib (sqlite3, os, sys) — zero dependency issues.
Safe to run multiple times.

Usage (from backend/):
    python3 migrations/add_user_id_to_tables.py
"""

import os
import sys
import sqlite3


def find_sqlite_db() -> str:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(backend_dir, '.env')
    sqlite_path = None
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith('SQLITE_PATH='):
                    sqlite_path = line.split('=', 1)[1].strip()
                    break
    db_name = sqlite_path or 'aurestra.db'
    if not os.path.isabs(db_name):
        db_name = os.path.join(backend_dir, db_name)
    return db_name


# Tables that need user_id added.
# Format: (table_name, column_ddl)
TABLES = [
    ("transactions",       "INTEGER REFERENCES users(id)"),
    ("budgets",            "INTEGER REFERENCES users(id)"),
    ("account_balances",   "INTEGER REFERENCES users(id)"),
    ("savings_goals",      "INTEGER REFERENCES users(id)"),
    ("categories",         "INTEGER REFERENCES users(id)"),
    ("monthly_balances",   "INTEGER REFERENCES users(id)"),
    ("financial_insights", "INTEGER REFERENCES users(id)"),
    ("statement_analysis", "INTEGER REFERENCES users(id)"),
    ("sms_history",        "INTEGER REFERENCES users(id)"),
]


def migrate():
    sqlite_path = find_sqlite_db()
    print(f"\n👤 Phase 2 Migration — SQLite: {sqlite_path}\n")

    if not os.path.exists(sqlite_path):
        print(f"❌ Database not found: {sqlite_path}")
        sys.exit(1)

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=OFF")   # OFF during schema changes

    # Target specific user email
    TARGET_EMAIL = "ezaan.amin@gmail.com"
    cursor.execute("SELECT id, email FROM users WHERE email = ?", (TARGET_EMAIL,))
    existing_user = cursor.fetchone()
    
    if not existing_user:
        print(f"❌ User with email {TARGET_EMAIL} not found in DB. Cannot assign existing data.")
        conn.close()
        sys.exit(1)

    target_user_id = existing_user["id"]
    print(f"  📌 Will assign all existing rows to user id={target_user_id} ({existing_user['email']})\n")

    total_added   = []
    total_updated = {}

    for table, col_ddl in TABLES:
        # Check table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cursor.fetchone():
            print(f"  ⏭️  Table '{table}' does not exist — skipping")
            continue

        # Check existing columns
        cursor.execute(f"PRAGMA table_info({table})")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "user_id" in existing_cols:
            print(f"  ⏭️  {table}.user_id already exists — skipping ALTER")
        else:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id {col_ddl}")
                conn.commit()
                print(f"  ✅ Added {table}.user_id")
                total_added.append(table)
            except sqlite3.OperationalError as e:
                print(f"  ❌ Could not add {table}.user_id: {e}")
                continue

        # Assign existing NULL rows to target user
        try:
            cursor.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
                (target_user_id,)
            )
            count = cursor.rowcount
            conn.commit()
            total_updated[table] = count
            if count > 0:
                print(f"  ✅ Assigned {count} existing {table} rows → user_id={target_user_id}")
            else:
                print(f"  ℹ️  {table}: no NULL user_id rows to assign")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Could not update {table}: {e}")

    cursor.execute("PRAGMA foreign_keys=ON")
    conn.close()

    # ── Summary ──────────────────────────────────────────────
    print("\n" + "─" * 56)
    print("📊 Phase 2 Migration Summary")
    print("─" * 56)
    if total_added:
        print(f"  Columns added:  {', '.join(total_added)}")
    else:
        print("  No new columns needed (all already present).")
    print(f"  Rows assigned to user {target_user_id}:")
    for tbl, cnt in total_updated.items():
        print(f"    {tbl:30s}: {cnt:>5} rows")

    # Quick verification
    print("\n  Verification (user_id counts per table):")
    conn2 = sqlite3.connect(sqlite_path)
    conn2.row_factory = sqlite3.Row
    cur2 = conn2.cursor()
    for table, _ in TABLES:
        try:
            cur2.execute(f"SELECT COUNT(*) as n FROM {table} WHERE user_id = ?", (target_user_id,))
            n = cur2.fetchone()["n"]
            cur2.execute(f"SELECT COUNT(*) as total FROM {table}")
            total = cur2.fetchone()["total"]
            status = "✅" if n == total else "⚠️ "
            print(f"    {status} {table:30s}: {n}/{total} rows owned by user {target_user_id}")
        except Exception as e:
            print(f"    ⚠️  {table}: {e}")
    conn2.close()

    print("\n✅ Phase 2 Migration complete!\n")


if __name__ == "__main__":
    migrate()
