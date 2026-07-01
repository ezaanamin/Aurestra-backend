#!/usr/bin/env python3
"""
migrations/add_auth_fields.py
─────────────────────────────
Phase 1 Migration: Add email/password auth fields to the users table.

Uses only stdlib (sqlite3, os, sys) — zero dependency issues.
Safe to run multiple times (checks for column existence first).

Usage (from backend/):
    python3 migrations/add_auth_fields.py
"""

import os
import sys
import sqlite3


def find_sqlite_db() -> str:
    """Locate aurestra.db relative to this script."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Try reading SQLITE_PATH from .env manually
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


NEW_COLUMNS = [
    # (column_name, DDL_definition)
    ("password_hash",               "VARCHAR(255)"),
    ("is_email_verified",           "INTEGER DEFAULT 0"),
    ("email_verification_token",    "VARCHAR(64)"),
    ("email_verification_sent_at",  "DATETIME"),
    ("password_reset_token",        "VARCHAR(64)"),
    ("password_reset_expires_at",   "DATETIME"),
    ("auth_method",                 "VARCHAR(20) DEFAULT 'google'"),
]


def migrate():
    sqlite_path = find_sqlite_db()
    print(f"\n🔐 Phase 1 Migration — SQLite: {sqlite_path}\n")

    if not os.path.exists(sqlite_path):
        print(f"❌ Database not found: {sqlite_path}")
        sys.exit(1)

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Enable WAL mode for safety
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")

    # Get existing columns
    cursor.execute("PRAGMA table_info(users)")
    existing = {row["name"] for row in cursor.fetchall()}
    print(f"  Current users columns: {', '.join(sorted(existing))}\n")

    added = []
    for col_name, col_ddl in NEW_COLUMNS:
        if col_name in existing:
            print(f"  ⏭️  users.{col_name} already exists — skipping")
            continue
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_ddl}")
            conn.commit()
            print(f"  ✅ Added users.{col_name}")
            added.append(col_name)
        except sqlite3.OperationalError as e:
            print(f"  ❌ Could not add users.{col_name}: {e}")

    # Mark existing Google users as email-verified and set auth_method
    try:
        cursor.execute("""
            UPDATE users
            SET is_email_verified = 1,
                auth_method       = 'google'
            WHERE (google_id IS NOT NULL OR google_email IS NOT NULL)
        """)
        rows = cursor.rowcount
        conn.commit()
        print(f"\n  ✅ Marked {rows} existing Google user(s) as verified (auth_method='google').")
    except sqlite3.OperationalError as e:
        print(f"  ⚠️  Could not update existing users: {e}")

    # Show final state
    cursor.execute("PRAGMA table_info(users)")
    final_cols = [row["name"] for row in cursor.fetchall()]
    print(f"\n  Final users columns: {', '.join(final_cols)}")

    cursor.execute("SELECT id, email, is_email_verified, auth_method FROM users")
    rows_data = cursor.fetchall()
    print(f"\n  Users in DB ({len(rows_data)} total):")
    for r in rows_data:
        print(f"    id={r['id']} email={r['email']} verified={r['is_email_verified']} method={r['auth_method']}")

    conn.close()

    if added:
        print(f"\n✅ Phase 1 Migration complete! Added columns: {', '.join(added)}\n")
    else:
        print("\n✅ Phase 1 Migration — all columns already present. Nothing to do.\n")


if __name__ == "__main__":
    migrate()
