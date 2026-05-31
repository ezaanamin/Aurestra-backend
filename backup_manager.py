import os
import io
import shutil
import datetime
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

# FIX 1: Import once at module level with a safe fallback stub.
# The original code imported send_push_to_all both here AND inside
# perform_backup(), where the inner try/except stub silently shadowed
# any ImportError from this top-level import.
try:
    from fcm_utils import send_push_to_all
except ImportError:
    def send_push_to_all(title, body):
        print(f"[PUSH stub] {title}: {body}")


class BackupManager:
    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    # ─────────────────────────────────────────────
    #  Init
    # ─────────────────────────────────────────────
    def init_app(self, app):
        self.backup_password   = os.getenv("BACKUP_PASSWORD", "default_secure_password")
        print(f"🔑 [Backup] Password: {self.backup_password}")
        self.local_backup_path = (
            os.getenv("LOCAL_BACKUP_PATH")
            or os.getenv("BACKUP_EXTERNAL_PATH")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
        )
        self.gdrive_local_path = os.getenv("BACKUP_GDRIVE_PATH")  # optional Google Drive local mount
        self.base_dir          = os.path.dirname(os.path.abspath(__file__))

    # ─────────────────────────────────────────────
    #  AES-256 helpers
    # ─────────────────────────────────────────────
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
            backend=default_backend(),
        )
        return kdf.derive(password.encode())

    def encrypt_file(self, src: str, dst: str) -> None:
        """AES-256-CBC encrypt *src* → *dst*.  Format: [16B salt][16B IV][ciphertext]."""
        salt = os.urandom(16)
        iv   = os.urandom(16)
        key  = self._derive_key(self.backup_password, salt)

        cipher    = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()

        with open(src, "rb") as f:
            data = f.read()

        padder     = sym_padding.PKCS7(128).padder()
        padded     = padder.update(data) + padder.finalize()
        ciphertext = encryptor.update(padded) + encryptor.finalize()

        with open(dst, "wb") as f:
            f.write(salt + iv + ciphertext)

    # ─────────────────────────────────────────────
    #  Main entry point
    # ─────────────────────────────────────────────
    def perform_backup(self) -> dict:
        """
        Run the full midnight backup.  Returns a dict with per-destination status.
        """
        print("⏳ [Backup] ── Starting Midnight Backup ──────────────────")

        # FIX 2: Removed the redundant inner import of send_push_to_all that was
        # here originally. The module-level import (with fallback stub) is sufficient.

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir  = os.path.join(self.base_dir, "temp_backups")

        results = {
            "google_drive": False,
            "local":        False,
            "postgresql":   False,
            "timestamp":    timestamp,
        }

        try:
            os.makedirs(temp_dir, exist_ok=True)

            # ── Build encrypted ZIP of SQLite file ────────────────────────
            sqlite_path = self._get_sqlite_path()
            if not sqlite_path or not os.path.exists(sqlite_path):
                print(f"❌ [Backup] SQLite file not found at '{sqlite_path}'. Aborting file-based backups.")
                encrypted_path = None
            else:
                zip_path       = os.path.join(temp_dir, f"aurestra_backup_{timestamp}.zip")
                encrypted_path = zip_path + ".enc"

                try:
                    import zipfile
                    import sqlite3

                    # Use SQLite backup API to flush WAL and get a consistent snapshot
                    consistent_db_path = zip_path + ".snapshot.db"
                    src_conn = sqlite3.connect(sqlite_path)
                    dst_conn = sqlite3.connect(consistent_db_path)
                    src_conn.backup(dst_conn)
                    dst_conn.close()
                    src_conn.close()

                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(consistent_db_path, arcname="aurestra.db")
                    os.remove(consistent_db_path)
                    print(f"📦 [Backup] Zipped  → {zip_path}")

                    self.encrypt_file(zip_path, encrypted_path)
                    print(f"🔒 [Backup] Encrypted → {encrypted_path}")
                    os.remove(zip_path)
                except Exception as e:
                    print(f"❌ [Backup] Archive/encrypt error: {e}")
                    encrypted_path = None

            # ── Destination 1: Google Drive ───────────────────────────────
            if encrypted_path:
                results["google_drive"] = self._backup_to_google_drive(encrypted_path, timestamp)

            # ── Destination 2: Local disk ─────────────────────────────────
            if encrypted_path:
                results["local"] = self._backup_to_local(encrypted_path, timestamp)

            # ── Destination 3: PostgreSQL ─────────────────────────────────
            results["postgresql"] = self._backup_to_postgres()

            # ── Cleanup temp ──────────────────────────────────────────────
            try:
                if encrypted_path and os.path.exists(encrypted_path):
                    os.remove(encrypted_path)
                shutil.rmtree(temp_dir, ignore_errors=True)
                os.makedirs(temp_dir, exist_ok=True)
                print("🧹 [Backup] Temp directory cleaned.")
            except Exception as e:
                print(f"⚠️  [Backup] Cleanup warning: {e}")

            # ── Summary ───────────────────────────────────────────────────
            ok = sum(1 for v in [results["google_drive"], results["local"], results["postgresql"]] if v is True)
            print(f"✅ [Backup] Done — {ok}/3 destinations succeeded.")
            print(f"   Google Drive : {'✅' if results['google_drive'] else '❌'}")
            print(f"   Local disk   : {'✅' if results['local']        else '❌'}")
            print(f"   PostgreSQL   : {'✅' if results['postgresql']   else '❌'}")
            print("─────────────────────────────────────────────────────────")

            # FIX 3: Push notification block was previously OUTSIDE the try/except
            # due to broken indentation, meaning exceptions in it were uncaught and
            # would propagate upward instead of being logged as warnings.
            try:
                print("🔥 [PUSH] Starting backup notification sequence...")

                google_result = send_push_to_all(
                    "Backup: Google Drive",
                    "✅ Successfully synced to cloud." if results["google_drive"]
                    else "❌ Failed to upload.",
                )
                print(f"🔥 [PUSH] Google Drive Result: {google_result}")

                postgres_result = send_push_to_all(
                    "Backup: PostgreSQL",
                    "✅ Database incremental sync successful." if results["postgresql"]
                    else "❌ Database sync failed.",
                )
                print(f"🔥 [PUSH] PostgreSQL Result: {postgres_result}")

                local_result = send_push_to_all(
                    "Backup: Local Storage",
                    "✅ Local encrypted backup saved." if results["local"]
                    else "❌ Failed to save locally.",
                )
                print(f"🔥 [PUSH] Local Result: {local_result}")

                print("🔥 [PUSH] Backup notification sequence completed.")

            except Exception as push_err:
                print(f"⚠️ [Backup] Failed to send summary push notifications:")
                print(push_err)
                import traceback
                traceback.print_exc()

        except Exception as e:
            print(f"❌ [Backup] Unexpected error in perform_backup: {e}")
            import traceback
            traceback.print_exc()

        # FIX 4: Missing return statement — the original function never returned
        # `results`, so every caller received None instead of the status dict.
        return results

    # ─────────────────────────────────────────────
    #  Destination helpers
    # ─────────────────────────────────────────────
    def _backup_to_google_drive(self, encrypted_path: str, timestamp: str) -> bool:
        """Upload the encrypted ZIP to Google Drive under 'Aurestra Backups/YYYY-MM-DD/'."""
        try:
            from model import User
            from drive_utils import get_drive_service, ensure_folder_path, upload_file_from_path
            from types import SimpleNamespace

            admin_email = os.getenv("BANK_EMAIL_ACCOUNT")
            admin_user  = None

            if admin_email:
                admin_user = SimpleNamespace(email=admin_email, google_refresh_token=None)
                print(f"📡 [Backup → Drive] Attempting centralized auth for {admin_email}...")
                service = get_drive_service(admin_user)
                if service:
                    print(f"✅ [Backup → Drive] Centralized auth successful for {admin_email}")
                else:
                    admin_user = None  # Signal to fall back

            if not admin_user:
                admin_user = User.query.filter(User.google_refresh_token.isnot(None)).first()
                if not admin_user:
                    print("⚠️  [Backup → Drive] No Google-linked user found (Centralized or Local). Skipping.")
                    return False
                service = get_drive_service(admin_user)

            if not service:
                print("⚠️  [Backup → Drive] Could not build Drive service.")
                return False

            date_folder = datetime.datetime.now().strftime("%Y-%m-%d")
            folder_id   = ensure_folder_path(service, ["Aurestra Backups", date_folder])
            if not folder_id:
                print("❌ [Backup → Drive] Could not create/find Drive folder.")
                return False

            filename = os.path.basename(encrypted_path)
            success  = upload_file_from_path(service, folder_id, filename, encrypted_path)
            if success:
                print(f"✅ [Backup → Drive] Uploaded: {filename}")
            return bool(success)

        except Exception as e:
            print(f"❌ [Backup → Drive] Error: {e}")
            return False

    def _backup_to_local(self, encrypted_path: str, timestamp: str) -> bool:
        """Copy the encrypted ZIP to LOCAL_BACKUP_PATH."""
        try:
            dest_dir  = self.local_backup_path
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, os.path.basename(encrypted_path))
            shutil.copy2(encrypted_path, dest_file)
            print(f"✅ [Backup → Local] Saved: {dest_file}")

            self._rotate_local_backups(dest_dir, keep=14)
            return True
        except Exception as e:
            print(f"❌ [Backup → Local] Error at primary path: {e}")
            try:
                print("⚠️ [Backup → Local] Attempting fallback to local 'backups' directory...")
                fallback_dir  = os.path.join(self.base_dir, "backups")
                os.makedirs(fallback_dir, exist_ok=True)
                fallback_file = os.path.join(fallback_dir, os.path.basename(encrypted_path))
                shutil.copy2(encrypted_path, fallback_file)
                print(f"✅ [Backup → Local] Saved to fallback: {fallback_file}")
                self._rotate_local_backups(fallback_dir, keep=14)
                return True
            except Exception as fallback_e:
                print(f"❌ [Backup → Local] Fallback error: {fallback_e}")
                return False

    def _rotate_local_backups(self, directory: str, keep: int = 14) -> None:
        """Delete oldest encrypted backups, retaining *keep* most recent."""
        try:
            files = sorted(
                [f for f in os.listdir(directory) if f.endswith(".enc")],
                reverse=True,
            )
            for old in files[keep:]:
                os.remove(os.path.join(directory, old))
                print(f"🗑️  [Backup → Local] Rotated out: {old}")
        except Exception as e:
            print(f"⚠️  [Backup → Local] Rotation warning: {e}")

    def _backup_to_postgres(self) -> bool:
        """
        Incremental SQLite → PostgreSQL sync (no data wiping).
        Uses PostgreSQL UPSERT (ON CONFLICT DO UPDATE) to safely merge data
        without ever truncating or deleting existing backup data.
        """
        try:
            import pandas as pd
            from database import get_sqlite_engine, get_postgres_engine
            from sqlalchemy import inspect, text

            sqlite_eng = get_sqlite_engine()
            pg_eng     = get_postgres_engine()

            if pg_eng is None:
                print("⚠️  [Backup → PG] PostgreSQL not configured. Skipping.")
                return False

            inspector = inspect(sqlite_eng)
            tables    = inspector.get_table_names()

            if not tables:
                print("⚠️  [Backup → PG] SQLite has no tables yet.")
                return False

            PARENTS = ["users", "categories", "account_balances", "transactions"]
            ordered = [t for t in PARENTS if t in tables] + [t for t in tables if t not in PARENTS]

            print(f"📊 [Backup → PG] Incremental Syncing {len(ordered)} tables ...")

            pg_insp  = inspect(pg_eng)
            pg_exist = set(pg_insp.get_table_names())

            synced   = 0
            raw_conn = pg_eng.raw_connection()
            try:
                raw_conn.autocommit = False
                cur = raw_conn.cursor()

                for table in ordered:
                    if table in ("users", "device_tokens"):
                        print(f"   ⏭️  {table}: skipped (excluded from backup sync)")
                        synced += 1
                        continue

                    df      = pd.read_sql_table(table, sqlite_eng)
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
                        except Exception as create_err:
                            print(f"⚠️ [Backup → PG] Failed to create table structure for {table}: {create_err}")
                            cur.execute(f'ROLLBACK TO SAVEPOINT "table_create_{table}";')
                            continue

                    if not df.empty:
                        cols         = list(df.columns)
                        col_str      = ", ".join(f'"{c}"' for c in cols)
                        placeholders = ", ".join(["%s"] * len(cols))
                        conflict_target = ", ".join([f'"{c}"' for c in pk_cols])
                        update_cols     = ", ".join(
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

                        PG_BIGINT_MAX = 9_223_372_036_854_775_807
                        PG_BIGINT_MIN = -9_223_372_036_854_775_808

                        def _safe_val(v, tbl):
                            if hasattr(v, "__class__") and v.__class__.__name__ == "NaTType":
                                return None
                            return v

                        rows = [
                            tuple(_safe_val(v, table) for v in row)
                            for row in df.itertuples(index=False, name=None)
                        ]

                        cur.execute(f'SAVEPOINT "upsert_{table}";')
                        try:
                            cur.executemany(sql, rows)
                            cur.execute(f'RELEASE SAVEPOINT "upsert_{table}";')
                            print(f"   ✅ {table}: {len(df)} rows upserted → PG")
                            synced += 1
                        except Exception as e:
                            cur.execute(f'ROLLBACK TO SAVEPOINT "upsert_{table}";')
                            print(f"   ❌ {table} upsert failed: {e}")
                    else:
                        print(f"   ✅ {table}: 0 rows (empty) → PG")
                        synced += 1

                raw_conn.commit()
                cur.close()

            except Exception as outer:
                raw_conn.rollback()
                print(f"❌ [Backup → PG] Transaction error: {outer}")
                return False
            finally:
                raw_conn.close()

            print(f"✅ [Backup → PG] {synced}/{len(ordered)} tables incrementally synced.")
            # FIX 5: Return True when all tables were processed successfully (synced == len(ordered)),
            # not just synced > 0, which would return True even with partial failures.
            # Also handles the edge case where all tables are empty (synced == 0 but len(ordered) == 0).
            return synced == len(ordered)

        except Exception as e:
            print(f"❌ [Backup → PG] Fatal error: {e}")
            return False

    # ─────────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────────
    def _get_sqlite_path(self) -> str | None:
        """Resolve the absolute path to the live SQLite file."""
        sqlite_file = os.getenv("SQLITE_PATH", "aurestra.db")
        if not os.path.isabs(sqlite_file):
            sqlite_file = os.path.join(self.base_dir, sqlite_file)
        return sqlite_file