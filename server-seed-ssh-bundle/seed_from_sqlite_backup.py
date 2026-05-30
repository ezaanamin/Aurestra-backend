#!/usr/bin/env python3
"""
Copy rows from an older / backup SQLite Aurestra DB into the current primary SQLite DB.

Handles schema drift by inserting the intersection of column names, plus explicit defaults
for newer transaction columns when the source snapshot predates them.

Usage (from backend/):
    python3 db/seed_from_sqlite_backup.py \\
        --source backups/aurestra_backup_20260503_083451/aurestra.db

Optional:
    --target /path/to/aurestra.db   (default: SQLITE_PATH or ./aurestra.db next to backend)
    --dry-run                       (print plan + row counts only)
    --export-sql                    (write db/seeders/categories.sqlite.sql +
                                     db/seeders/transactions.sqlite.sql from --source; no import)

Import uses INSERT OR REPLACE / INSERT OR IGNORE per table so you can re-run safely in merge
scenarios (skips duplicate hashes / emails where appropriate).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Canonical layout: backend/db/this_script.py  →  backend/
# SSH bundle layout: backend/server-seed-ssh-bundle/this_script.py  →  backend/
_base = os.path.basename(HERE)
if _base == "db" or _base == "server-seed-ssh-bundle":
    BACKEND_ROOT = os.path.dirname(HERE)
else:
    BACKEND_ROOT = os.environ.get("AURESTRA_BACKEND", os.path.dirname(HERE))
SEEDERS_DIR = os.path.join(BACKEND_ROOT, "db", "seeders")

# FK-safe order (matches db/setup.py; device_notifications omitted from backup pulls)
TABLE_IMPORT_ORDER = [
    "users",
    "categories",
    "categorization_rules",
    "account_balances",
    "transactions",
    "budgets",
    "savings_goals",
    "monthly_balances",
    "sms_history",
    "device_tokens",
    "financial_insights",
    "statement_analysis",
]

# SQLite conflict clause per table (main database)
INSERT_MODE: dict[str, str] = {
    "users": "OR IGNORE",  # unique email
    "categories": "OR REPLACE",  # keep ids stable for FKs
    "categorization_rules": "OR IGNORE",
    "account_balances": "OR REPLACE",  # unique source
    "transactions": "OR IGNORE",  # unique transaction_hash / transaction_id
    "budgets": "OR REPLACE",  # unique month
    "savings_goals": "OR REPLACE",
    "monthly_balances": "OR REPLACE",  # unique month in current schema
    "sms_history": "OR IGNORE",  # unique sms_hash
    "device_tokens": "OR IGNORE",  # unique token
    "financial_insights": "OR IGNORE",  # PK id
    "statement_analysis": "OR REPLACE",  # unique month
}


def _default_target_db() -> str:
    env = os.environ.get("SQLITE_PATH", "").strip()
    if env and os.path.isabs(env):
        return env
    if env:
        return os.path.join(BACKEND_ROOT, env)
    return os.path.join(BACKEND_ROOT, "aurestra.db")


def _table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    cur = con.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone()
    return row is not None


def _ordered_common_columns(tgt_order: list[str], src_cols: set[str]) -> list[str]:
    return [c for c in tgt_order if c in src_cols]


def _transactions_insert_select(
    attach: str, table: str, tgt_cols: list[str], src_cols: set[str]
) -> tuple[list[str], list[str]]:
    """(insert_columns, select_expressions) for main.transactions from attached backup."""
    insert_cols = _ordered_common_columns(tgt_cols, src_cols)
    select_exprs = [f'{attach}."{table}"."{c}"' for c in insert_cols]
    if "account_balance_source" in tgt_cols and "account_balance_source" not in src_cols:
        insert_cols.append("account_balance_source")
        select_exprs.append("NULL")
    if "balance_applied" in tgt_cols and "balance_applied" not in src_cols:
        insert_cols.append("balance_applied")
        select_exprs.append("1")
    return insert_cols, select_exprs


def _sql_escape(s: str) -> str:
    return (s or "").replace("'", "''")


def _export_transactions_sql(src: sqlite3.Connection, dest_path: str) -> None:
    cols = _table_columns(src, "transactions")
    quoted = ", ".join(f'"{c}"' for c in cols)
    rows = src.execute(f"SELECT {quoted} FROM transactions ORDER BY id").fetchall()
    lines = [
        "-- transactions seeder (SQLite)",
        f"-- {len(rows)} rows (generated from backup)",
        "PRAGMA foreign_keys=OFF;",
        "",
    ]
    col_list = ", ".join(f'"{c}"' for c in cols)
    for row in rows:
        vals = []
        for v in row:
            if v is None:
                vals.append("NULL")
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(str(int(v)) if isinstance(v, float) and v == int(v) else repr(float(v)))
            else:
                vals.append("'" + _sql_escape(str(v)) + "'")
        lines.append(
            f"INSERT INTO transactions ({col_list}) VALUES ({', '.join(vals)});"
        )
    lines.extend(["", "PRAGMA foreign_keys=ON;", ""])
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _export_categories_sql(src: sqlite3.Connection, dest_path: str) -> None:
    cols = _table_columns(src, "categories")
    quoted = ", ".join(f'"{c}"' for c in cols)
    rows = src.execute(f"SELECT {quoted} FROM categories ORDER BY id").fetchall()
    lines = [
        "-- categories seeder (SQLite)",
        f"-- {len(rows)} rows (generated from backup)",
        "PRAGMA foreign_keys=OFF;",
        "",
    ]
    col_list = ", ".join(cols)
    for row in rows:
        vals = []
        for v in row:
            if v is None:
                vals.append("NULL")
            elif isinstance(v, int):
                vals.append(str(v))
            else:
                vals.append("'" + _sql_escape(str(v)) + "'")
        lines.append(f"INSERT INTO categories ({col_list}) VALUES ({', '.join(vals)});")
    lines.extend(["", "PRAGMA foreign_keys=ON;", ""])
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _bump_sqlite_sequence(main: sqlite3.Connection, table: str) -> None:
    row = main.execute(f'SELECT MAX(id) FROM "{table}"').fetchone()
    if not row or row[0] is None:
        return
    mx = int(row[0])
    main.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
    main.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (table, mx))


def _pragma_cols(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    cur = con.execute(f'PRAGMA {schema}.table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def import_tables(source_path: str, target_path: str, dry_run: bool) -> None:
    attach_name = "bak"
    main = sqlite3.connect(target_path)
    main.execute("PRAGMA foreign_keys=OFF")

    try:
        main.execute(f'ATTACH DATABASE ? AS "{attach_name}"', (source_path,))

        src_cols_cache: dict[str, set[str]] = {}
        tgt_meta: dict[str, list[str]] = {}
        for t in TABLE_IMPORT_ORDER:
            if not _table_exists(main, t):
                print(f"  ⏭️  skip (not in target): {t}")
                continue
            try:
                bak_cols = _pragma_cols(main, attach_name, t)
            except sqlite3.Error:
                print(f"  ⏭️  skip (not in source): {t}")
                continue
            if not bak_cols:
                print(f"  ⏭️  skip (not in source): {t}")
                continue
            src_cols_cache[t] = set(bak_cols)
            tgt_meta[t] = _table_columns(main, t)

        for table in TABLE_IMPORT_ORDER:
            if table not in tgt_meta or table not in src_cols_cache:
                continue
            mode = INSERT_MODE.get(table, "OR IGNORE")
            tgt_cols = tgt_meta[table]
            src_cols = src_cols_cache[table]

            if table == "transactions":
                insert_cols, select_exprs = _transactions_insert_select(
                    attach_name, table, tgt_cols, src_cols
                )
            else:
                insert_cols = _ordered_common_columns(tgt_cols, src_cols)
                select_exprs = [f'{attach_name}."{table}"."{c}"' for c in insert_cols]

            if not insert_cols:
                print(f"  ⚠️  no common columns for {table}, skipping")
                continue

            col_sql = ", ".join(f'"{c}"' for c in insert_cols)
            sel_sql = ", ".join(select_exprs)
            sql = (
                f'INSERT {mode} INTO main."{table}" ({col_sql}) '
                f'SELECT {sel_sql} FROM {attach_name}."{table}"'
            )

            n_src = main.execute(f'SELECT COUNT(*) FROM {attach_name}."{table}"').fetchone()[0]
            if dry_run:
                print(f"  [dry-run] {table}: {n_src} rows in source → {sql[:120]}...")
                continue

            cur = main.execute(sql)
            main.commit()
            print(f"  ✅ {table}: executed (source rows={n_src}, rowcount={cur.rowcount})")

        if not dry_run:
            for table in ("transactions", "categories", "users", "sms_history"):
                if _table_exists(main, table):
                    try:
                        _bump_sqlite_sequence(main, table)
                    except sqlite3.OperationalError:
                        pass
            main.commit()

    finally:
        try:
            main.execute(f'DETACH DATABASE "{attach_name}"')
        except sqlite3.Error:
            pass
        main.execute("PRAGMA foreign_keys=ON")
        main.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed current SQLite DB from a backup .db file")
    parser.add_argument("--source", required=True, help="Path to source aurestra.db (backup)")
    parser.add_argument("--target", default="", help="Path to destination DB (default: primary SQLite)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--export-sql",
        action="store_true",
        help=f"Write {SEEDERS_DIR}/categories.sqlite.sql and transactions.sqlite.sql from --source",
    )
    args = parser.parse_args()

    source_path = os.path.abspath(args.source)
    if not os.path.isfile(source_path):
        print(f"Source not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    if args.export_sql:
        os.makedirs(SEEDERS_DIR, exist_ok=True)
        src = sqlite3.connect(source_path)
        try:
            cat_out = os.path.join(SEEDERS_DIR, "categories.sqlite.sql")
            txn_out = os.path.join(SEEDERS_DIR, "transactions.sqlite.sql")
            _export_categories_sql(src, cat_out)
            _export_transactions_sql(src, txn_out)
            print(f"Wrote:\n  {cat_out}\n  {txn_out}")
        finally:
            src.close()
        return

    target_path = os.path.abspath(args.target) if args.target else _default_target_db()
    if not os.path.isfile(target_path):
        print(f"Target not found: {target_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Source: {source_path}\nTarget: {target_path}")
    if args.dry_run:
        print("Dry run — no writes.\n")
    import_tables(source_path, target_path, args.dry_run)
    if not args.dry_run:
        print("\nDone. If new auto-increment IDs misbehave, run migrations/setup once; sqlite_sequence was bumped for key tables.")


if __name__ == "__main__":
    main()
