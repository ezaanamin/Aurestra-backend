import pandas as pd
from sqlalchemy import inspect
from database import get_sqlite_engine, get_postgres_engine

PG_SKIP_TABLES = {"users", "device_tokens", "device_notifications"}

class BackupPostgresStorage:
    """Manages the incremental sync of SQLite data to PostgreSQL."""
    
    @staticmethod
    def sync_to_postgres() -> bool:
        """
        Incremental SQLite → PostgreSQL sync using UPSERT.
        Skips tables in PG_SKIP_TABLES.
        Returns True if successful (or partial success).
        """
        try:
            sqlite_eng = get_sqlite_engine()
            pg_eng = get_postgres_engine()

            if pg_eng is None:
                return False

            inspector = inspect(sqlite_eng)
            tables = inspector.get_table_names()

            if not tables:
                return False

            PARENTS = ["users", "categories", "account_balances", "transactions"]
            ordered = [t for t in PARENTS if t in tables] + [t for t in tables if t not in PARENTS]

            pg_insp = inspect(pg_eng)
            pg_exist = set(pg_insp.get_table_names())

            synced = 0
            failed_tables = []
            raw_conn = pg_eng.raw_connection()

            try:
                raw_conn.autocommit = False
                cur = raw_conn.cursor()

                for table in ordered:
                    if table in PG_SKIP_TABLES:
                        synced += 1
                        continue

                    df = pd.read_sql_table(table, sqlite_eng)
                    pk_cols = inspector.get_pk_constraint(table).get("constrained_columns", [])
                    if not pk_cols:
                        pk_cols = ["id"] if "id" in df.columns else list(df.columns)

                    if table not in pg_exist:
                        cur.execute(f'SAVEPOINT "table_create_{table}";')
                        try:
                            with pg_eng.begin() as tmp:
                                df.iloc[0:0].to_sql(table, tmp, if_exists="fail", index=False)
                            pg_exist.add(table)
                            if pk_cols != list(df.columns):
                                pk_str = ", ".join([f'"{c}"' for c in pk_cols])
                                cur.execute(f'ALTER TABLE "{table}" ADD PRIMARY KEY ({pk_str});')
                            cur.execute(f'RELEASE SAVEPOINT "table_create_{table}";')
                        except Exception:
                            cur.execute(f'ROLLBACK TO SAVEPOINT "table_create_{table}";')
                            failed_tables.append(table)
                            continue

                    if not df.empty:
                        cols = list(df.columns)
                        col_str = ", ".join(f'"{c}"' for c in cols)
                        placeholders = ", ".join(["%s"] * len(cols))
                        conflict_target = ", ".join([f'"{c}"' for c in pk_cols])
                        update_cols = ", ".join(
                            [f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in pk_cols]
                        )

                        if update_cols:
                            sql = (
                                f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders}) '
                                f'ON CONFLICT ({conflict_target}) DO UPDATE SET {update_cols}'
                            )
                        else:
                            sql = (
                                f'INSERT INTO "{table}" ({col_str}) VALUES ({placeholders}) '
                                f'ON CONFLICT ({conflict_target}) DO NOTHING'
                            )

                        def _safe_val(v):
                            if hasattr(v, "__class__") and v.__class__.__name__ == "NaTType":
                                return None
                            import math
                            if isinstance(v, float):
                                if math.isnan(v):
                                    return None
                                if v == int(v):
                                    return int(v)
                            return v

                        rows = [
                            tuple(_safe_val(v) for v in row)
                            for row in df.itertuples(index=False, name=None)
                        ]

                        cur.execute(f'SAVEPOINT "upsert_{table}";')
                        try:
                            cur.executemany(sql, rows)
                            cur.execute(f'RELEASE SAVEPOINT "upsert_{table}";')
                            synced += 1
                        except Exception:
                            cur.execute(f'ROLLBACK TO SAVEPOINT "upsert_{table}";')
                            failed_tables.append(table)
                    else:
                        synced += 1

                raw_conn.commit()
                cur.close()

            except Exception:
                raw_conn.rollback()
                return False
            finally:
                raw_conn.close()

            if synced == len(ordered):
                return True

            critical_tables = {"transactions", "account_balances", "categories"}
            failed_critical = [t for t in failed_tables if t in critical_tables]

            if len(ordered) > 0 and (synced / len(ordered)) >= 0.5 and not failed_critical:
                return True

            return False

        except Exception:
            return False
