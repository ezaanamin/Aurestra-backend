#!/usr/bin/env python3
"""
db/setup.py — One-shot database setup for Aurestra.
Runs migrations + seeds for both SQLite and PostgreSQL (schemas match backend/model.py).

Usage (from backend/):
    python3 db/setup.py                    # SQLite + PostgreSQL (if DATABASE_URL or DB_* set)
    python3 db/setup.py --sqlite-only
    python3 db/setup.py --pg-only
    python3 db/setup.py --reset            # DROP all tables first (asks for confirmation)

PostgreSQL: set DATABASE_URL or DB_USER + DB_NAME (+ DB_HOST / DB_PASSWORD as needed),
same as backend/database.py. After seeding, SERIAL sequences are synced for explicit ids.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect as sa_inspect, Boolean
from database import db, app, get_sqlite_engine, get_postgres_engine

HERE = os.path.dirname(os.path.abspath(__file__))

SQLITE_MIGRATION = os.path.join(HERE, "migrations", "sqlite.sql")
PG_MIGRATION     = os.path.join(HERE, "migrations", "postgres.sql")
SEEDERS_DIR      = os.path.join(HERE, "seeders")

# Tables must be created in this order (FK dependencies)
TABLE_ORDER = [
    "users",
    "categories",
    "categorization_rules",
    "account_balances",
    "transactions",
    "device_notifications",  # FK → users, transactions
    "budgets",
    "savings_goals",
    "monthly_balances",
    "sms_history",
    "device_tokens",
    "financial_insights",
    "statement_analysis",
]


def _sync_postgres_serial_sequences(engine) -> None:
    """
    After seeders INSERT explicit ids, bump SERIAL sequences so the next ORM INSERT does not
    collide (PostgreSQL only).
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        for table in TABLE_ORDER:
            try:
                seq = conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
                ).scalar()
                if not seq:
                    continue
                mx = conn.execute(text(f"SELECT MAX(id) FROM {table}")).scalar()
                if mx is None:
                    continue
                conn.execute(text("SELECT setval(:seq, :mx, true)"), {"seq": seq, "mx": int(mx)})
            except Exception as e:
                print(f"  ⚠️  [PostgreSQL] sequence sync skipped for {table}: {e}")
        conn.commit()
    print("  🔢 [PostgreSQL] SERIAL sequences synced to MAX(id).")


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments from a SQL chunk."""
    return "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    ).strip()


def run_sql_file(engine, filepath: str, label: str):
    """Execute a .sql file statement by statement."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Strip comments first so chunks that START with a comment line
    # are not mistakenly discarded along with their SQL body.
    statements = [
        clean for raw in content.split(";")
        if (clean := _strip_sql_comments(raw))
    ]

    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                print(f"  ⚠️  [{label}] statement error: {e}\n      SQL: {stmt[:80]}...")
        conn.commit()


def drop_all_tables(engine, dialect: str):
    """Drop all Aurestra tables (for --reset)."""
    with engine.connect() as conn:
        if dialect == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for table in reversed(TABLE_ORDER):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
        else:
            for table in reversed(TABLE_ORDER):
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        conn.commit()
    print(f"  🗑️  [{dialect}] All tables dropped.")


def ensure_missing_columns(engine, label: str):
    """Add any ORM-defined columns missing from existing tables (safe ALTER TABLE)."""
    import model  # noqa: F401 — registers all ORM models
    insp = sa_inspect(engine)
    is_pg = engine.dialect.name == "postgresql"

    with engine.connect() as conn:
        for table_name, table_obj in db.metadata.tables.items():
            if not insp.has_table(table_name):
                continue
            existing = {c["name"] for c in insp.get_columns(table_name)}
            for col in table_obj.columns:
                if col.name in existing:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                # PostgreSQL uses BOOLEAN, SQLite uses INTEGER for booleans
                if isinstance(col.type, Boolean) and not is_pg:
                    col_type = "INTEGER"
                nullable = "" if col.nullable else " NOT NULL"
                default = ""
                if col.default and col.default.is_scalar:
                    val = col.default.arg
                    if isinstance(val, bool):
                        default = f" DEFAULT {'TRUE' if val else 'FALSE'}" if is_pg else f" DEFAULT {1 if val else 0}"
                    elif isinstance(val, str):
                        default = f" DEFAULT '{val}'"
                    else:
                        default = f" DEFAULT {val}"
                ddl = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{nullable}{default}"
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                    print(f"  ➕ [{label}] Added missing column: {table_name}.{col.name}")
                except Exception as e:
                    print(f"  ⚠️  [{label}] Could not add {table_name}.{col.name}: {e}")


def run_all_seeders(engine, ext: str, label: str):
    """Run all <table>.<ext>.sql seeders in FK-safe order."""
    seeded = 0
    for table in TABLE_ORDER:
        seed_file = os.path.join(SEEDERS_DIR, f"{table}.{ext}.sql")
        if not os.path.exists(seed_file):
            continue
        # Skip if table already has rows
        try:
            with engine.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                if count and count > 0:
                    print(f"  ⏭️  [{label}] {table}: already has {count} rows — skipping")
                    continue
        except Exception:
            pass
        run_sql_file(engine, seed_file, label)
        with engine.connect() as conn:
            try:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"  ✅ [{label}] {table}: {n} rows")
                seeded += 1
            except Exception:
                pass
    return seeded


def setup_sqlite(reset: bool = False):
    engine = get_sqlite_engine()
    print("\n📦 Setting up SQLite...")

    if reset:
        drop_all_tables(engine, "sqlite")

    run_sql_file(engine, SQLITE_MIGRATION, "SQLite")
    print("  ✅ Schema applied.")
    ensure_missing_columns(engine, "SQLite")

    print("  🌱 Seeding tables...")
    run_all_seeders(engine, "sqlite", "SQLite")


def setup_postgres(reset: bool = False):
    engine = get_postgres_engine()
    if not engine:
        print("\n⚠️  PostgreSQL not configured — skipping.")
        return

    print("\n📦 Setting up PostgreSQL...")

    if reset:
        drop_all_tables(engine, "postgresql")

    run_sql_file(engine, PG_MIGRATION, "PostgreSQL")
    print("  ✅ Schema applied.")
    ensure_missing_columns(engine, "PostgreSQL")

    print("  🌱 Seeding tables...")
    run_all_seeders(engine, "postgres", "PostgreSQL")
    _sync_postgres_serial_sequences(engine)


def main():
    parser = argparse.ArgumentParser(description="Aurestra DB setup")
    parser.add_argument("--sqlite-only", action="store_true")
    parser.add_argument("--pg-only",     action="store_true")
    parser.add_argument("--reset",       action="store_true", help="DROP all tables first (destroys data!)")
    args = parser.parse_args()

    if args.reset:
        print("⚠️  WARNING: --reset will destroy all existing data!")
        confirm = input("Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)

    with app.app_context():
        import model  # noqa: F401

        if not args.pg_only:
            setup_sqlite(reset=args.reset)

        if not args.sqlite_only:
            setup_postgres(reset=args.reset)

    print("\n✅ Setup complete!")


if __name__ == "__main__":
    main()
