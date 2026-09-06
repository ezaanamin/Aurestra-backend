import traceback
import pandas as pd
from sqlalchemy import inspect, text
from database import get_sqlite_engine, get_postgres_engine

PG_SKIP_TABLES = {"users", "device_tokens", "device_notifications"}

class BackupPostgresStorage:
    """Manages the incremental sync of SQLite data to PostgreSQL."""
    
    @staticmethod
    def _align_table_schema(sqlite_eng, pg_eng, sqlite_insp, pg_insp, table):
        """Adds missing columns to PostgreSQL if SQLite has them."""
        s_cols = {c["name"]: c for c in sqlite_insp.get_columns(table)}
        p_cols = {c["name"] for c in pg_insp.get_columns(table)}
        missing = set(s_cols.keys()) - p_cols
        
        for col_name in missing:
            col_type = str(s_cols[col_name]["type"])
            try:
                with pg_eng.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{col_name}" {col_type};'))
            except Exception as e:
                print(f"⚠️ [PG Sync] Could not add column {col_name} to {table}: {e}")

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
                print("⚠️ [PG Sync] No PostgreSQL engine available.")
                return False

            inspector = inspect(sqlite_eng)
            tables = inspector.get_table_names()

            if not tables:
                return False

            PARENTS = ["users", "categories", "account_balances", "transactions"]
            ordered = [t for t in PARENTS if t in tables] + [t for t in tables if t not in PARENTS]

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

                    try:
                        df = pd.read_sql_table(table, sqlite_eng)
                        pk_cols = inspector.get_pk_constraint(table).get("constrained_columns", [])
                        if not pk_cols:
                            pk_cols = ["id"] if "id" in df.columns else list(df.columns)

                        pg_insp = inspect(pg_eng)
                        if table not in pg_insp.get_table_names():
                            cur.execute(f'SAVEPOINT "table_create_{table}";')
                            try:
                                with pg_eng.begin() as tmp:
                                    df.iloc[0:0].to_sql(table, tmp, if_exists="fail", index=False)
                                if pk_cols != list(df.columns):
                                    pk_str = ", ".join([f'"{c}"' for c in pk_cols])
                                    cur.execute(f'ALTER TABLE "{table}" ADD PRIMARY KEY ({pk_str});')
                                cur.execute(f'RELEASE SAVEPOINT "table_create_{table}";')
                                pg_insp = inspect(pg_eng)  # refresh inspector
                            except Exception as e:
                                cur.execute(f'ROLLBACK TO SAVEPOINT "table_create_{table}";')
                                print(f"❌ [PG Sync] Failed creating table {table}: {e}")
                                failed_tables.append(table)
                                continue

                        # Align schema if needed
                        BackupPostgresStorage._align_table_schema(sqlite_eng, pg_eng, inspector, pg_insp, table)
                        
                        # Filter dataframe columns to only what exists in PG
                        pg_insp = inspect(pg_eng) # Refresh again after possible ALTER TABLE
                        pg_cols = {c["name"] for c in pg_insp.get_columns(table)}
                        valid_cols = [c for c in df.columns if c in pg_cols]
                        df = df[valid_cols]

                        if not df.empty:
                            # Handle unique constraint ID reconciliations
                            uqs = pg_insp.get_unique_constraints(table)
                            if uqs and "id" in df.columns:
                                uq_cols = uqs[0]["column_names"]
                                uq_str = ", ".join(f'"{c}"' for c in uq_cols)
                                cur.execute(f'SELECT "id", {uq_str} FROM "{table}"')
                                pg_rows = cur.fetchall()
                                
                                # fetchall returns tuples. We map UQ tuple -> id
                                pg_map = {tuple(r[1:]): r[0] for r in pg_rows}
                                pg_ids = {r[0] for r in pg_rows}
                                
                                max_id = max(pg_ids.union(set(df["id"]))) if pg_ids else (df["id"].max() or 0)
                                for idx, row in df.iterrows():
                                    key = tuple(row[c] for c in uq_cols)
                                    if key in pg_map:
                                        df.at[idx, "id"] = pg_map[key]
                                    elif row["id"] in pg_ids:
                                        max_id += 1
                                        df.at[idx, "id"] = max_id
                                        pg_ids.add(max_id)

                            cols = list(df.columns)
                            col_str = ", ".join(f'"{c}"' for c in cols)
                            placeholders = ", ".join(["%s"] * len(cols))
                            
                            valid_pk_cols = [c for c in pk_cols if c in pg_cols]
                            if not valid_pk_cols:
                                valid_pk_cols = ["id"] if "id" in pg_cols else list(pg_cols)
                                
                            conflict_target = ", ".join([f'"{c}"' for c in valid_pk_cols])
                            update_cols = ", ".join(
                                [f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in valid_pk_cols]
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
                            except Exception as e:
                                cur.execute(f'ROLLBACK TO SAVEPOINT "upsert_{table}";')
                                print(f"❌ [PG Sync] Failed upserting {table}: {e}")
                                failed_tables.append(table)
                        else:
                            synced += 1

                    except Exception as err:
                        print(f"❌ [PG Sync] Unexpected error processing {table}: {err}")
                        traceback.print_exc()
                        failed_tables.append(table)

                raw_conn.commit()
                cur.close()

            except Exception as e:
                print(f"❌ [PG Sync] Transaction error: {e}")
                raw_conn.rollback()
                return False
            finally:
                raw_conn.close()

            if failed_tables:
                print(f"⚠️ [PG Sync] Failed tables: {failed_tables}")

            if synced == len(ordered):
                return True

            critical_tables = {"transactions", "account_balances", "categories"}
            failed_critical = [t for t in failed_tables if t in critical_tables]

            if len(ordered) > 0 and (synced / len(ordered)) >= 0.5 and not failed_critical:
                return True

            return False

        except Exception as e:
            print(f"❌ [PG Sync] Fatal error: {e}")
            return False
