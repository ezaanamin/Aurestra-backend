import os
import shutil
import time
import datetime
import subprocess

from services.backup.local_storage import BackupLocalStorage
from services.backup.drive_storage import BackupDriveStorage
from services.backup.postgres_storage import BackupPostgresStorage
from services.backup.backup_encryption import BackupEncryption
from services.backup.backup_service import BackupService

try:
    from fcm_utils import send_push_to_all
except ImportError:
    def send_push_to_all(title, body):
        pass

class BackupOrchestrator:
    """Central orchestrator for executing System and User backups with structured logging."""

    def __init__(self, app=None):
        self.app = app
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.local_storage = BackupLocalStorage(self.base_dir)
        self.drive_storage = BackupDriveStorage()
        self.backup_service = BackupService()
        self.backup_password = os.getenv("BACKUP_PASSWORD", "default_secure_password")
        self.start_time = None
        
        # Summary metrics
        self.summary = {
            "system": {
                "sqlite": False,
                "google_drive": False,
                "postgres": False
            },
            "users": {
                "total_processed": 0,
                "successful": 0,
                "failed": 0,
                "failures": []
            }
        }

    def _init_password(self):
        try:
            from model import User
            # Select the primary admin user to use their decryption key for system backup
            user = User.query.get(1)
            if user and user.decryption_key:
                self.backup_password = user.decryption_key
        except Exception:
            pass

    def perform_full_backup(self) -> dict:
        self.start_time = time.time()
        self._init_password()
        
        print("\n" + "="*50)
        print("SYSTEM BACKUP")
        print("="*50)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = self.local_storage.get_temp_dir()

        # 1. System: SQLite Backup
        print("SQLite Backup...", end=" ")
        sqlite_path = os.getenv("SQLITE_PATH", "aurestra.db")
        if not os.path.isabs(sqlite_path):
            sqlite_path = os.path.join(self.base_dir, sqlite_path)

        encrypted_path = None
        if os.path.exists(sqlite_path):
            try:
                import zipfile
                import sqlite3
                
                zip_path = os.path.join(temp_dir, f"aurestra_backup_{timestamp}.zip")
                encrypted_path = zip_path + ".enc"
                consistent_db_path = zip_path + ".snapshot.db"

                src_conn = sqlite3.connect(sqlite_path)
                dst_conn = sqlite3.connect(consistent_db_path)
                src_conn.backup(dst_conn)
                dst_conn.close()
                src_conn.close()

                pg_dump_path = None
                pg_dump_name = None
                try:
                    from database import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
                    if DB_USER and DB_HOST:
                        pg_dump_bin = os.getenv("PG_DUMP_PATH") or shutil.which("pg_dump")
                        if not pg_dump_bin:
                            import glob
                            for c in glob.glob("/usr/lib/postgresql/*/bin/pg_dump") + ["/usr/bin/pg_dump", "/usr/local/bin/pg_dump"]:
                                if os.path.isfile(c) and os.access(c, os.X_OK):
                                    pg_dump_bin = c
                                    break
                        if not pg_dump_bin:
                            raise FileNotFoundError("pg_dump utility not found. Install postgresql-client (e.g. apt-get install postgresql-client) or set PG_DUMP_PATH.")

                        pg_dump_name = f"postgres_{timestamp}.dump"
                        pg_dump_path = os.path.join(temp_dir, pg_dump_name)
                        cmd = [
                            
                            pg_dump_bin,
                            "-h", DB_HOST,
                            "-p", str(DB_PORT),
                            "-U", DB_USER,
                            "-d", DB_NAME,
                            "-Fc",
                            "-f", pg_dump_path,
                        ]
                        env = os.environ.copy()
                        env["PGPASSWORD"] = DB_PASSWORD or ""
                        subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except Exception as pg_err:
                    if hasattr(pg_err, "stderr") and pg_err.stderr:
                        print(f"⚠️ [Backup] PostgreSQL dump skipped: {pg_err}\nStderr: {pg_err.stderr.decode()}")
                    else:
                        print(f"⚠️ [Backup] PostgreSQL dump skipped: {pg_err}")

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(consistent_db_path, arcname="aurestra.db")
                    if pg_dump_path and os.path.exists(pg_dump_path):
                        zf.write(pg_dump_path, arcname=pg_dump_name)
                os.remove(consistent_db_path)
                if pg_dump_path and os.path.exists(pg_dump_path):
                    os.remove(pg_dump_path)

                BackupEncryption.encrypt_system_file(zip_path, encrypted_path, self.backup_password)
                os.remove(zip_path)
                
                self.local_storage.save_system_backup(encrypted_path, os.path.basename(encrypted_path))
                self.summary["system"]["sqlite"] = True
                print("✅")
            except Exception as e:
                print(f"❌ (Error: {e})")
        else:
            print("❌ (File not found)")

        # 2. System: Google Drive Upload
        print("Google Drive Upload...", end=" ")
        if encrypted_path:
            success = self.drive_storage.upload_system_backup(encrypted_path)
            self.summary["system"]["google_drive"] = success
            if success:
                print("✅")
            else:
                print("❌")
        else:
            print("⏭️ (Skipped)")

        # 3. System: PostgreSQL Sync
        print("PostgreSQL Sync...", end=" ")
        pg_success = BackupPostgresStorage.sync_to_postgres()
        self.summary["system"]["postgres"] = pg_success
        if pg_success:
            print("✅")
        else:
            print("❌")

        # 4. System: Cleanup
        print("Cleanup...", end=" ")
        self.local_storage.cleanup_temp_dir()
        self.local_storage.rotate_system_backups()
        print("✅")


        # ─────────────────────────────────────────────────────────────
        # USER BACKUPS
        # ─────────────────────────────────────────────────────────────
        print("\n" + "="*50)
        print("USER BACKUPS")
        print("="*50)

        try:
            from model import User
            from database import db
            from utils.crypto_helpers import verify_decryption_key
            
            users = User.query.filter(User.decryption_key_hash.isnot(None)).all()
            for u in users:
                print(f"User {u.id} ({u.email})")
                self.summary["users"]["total_processed"] += 1
                try:
                    dec_key = u.decryption_key
                    if not dec_key:
                        sys_pass = os.getenv("BACKUP_PASSWORD", "default_secure_password")
                        if sys_pass and verify_decryption_key(sys_pass, u.decryption_key_hash):
                            u.decryption_key = sys_pass
                            db.session.commit()
                            dec_key = sys_pass
                            print("  └─ Self-healed missing decryption key using BACKUP_PASSWORD ✅")

                    if not dec_key:
                        raise ValueError("Plaintext decryption key is missing in DB (user has not logged in since update).")

                    # Print the backup password for this user
                    print(f"  └─ Backup Password: {dec_key}")

                    # Set g.encryption_key so SQLAlchemy can decrypt fields during export
                    from flask import g
                    from utils.crypto_helpers import derive_encryption_key
                    g.encryption_key = derive_encryption_key(dec_key, u.decryption_key_salt)
                    db.session.expire_all() # Ensure cached objects don't use old/missing encryption_key

                    meta = self.backup_service.create_user_backup(u.id, dec_key)
                    if meta:
                        print("  └─ Backup Created ✅")
                        print("  └─ Encrypted ✅")
                        print("  └─ Uploaded (via Service) ✅")
                        print("  └─ Metadata Updated ✅")
                        self.summary["users"]["successful"] += 1
                    else:
                        print("  └─ Skipped (No changes or no data) ⏭️")
                        self.summary["users"]["successful"] += 1
                except Exception as e:
                    print(f"  └─ Failed ❌ ({e})")
                    self.summary["users"]["failed"] += 1
                    self.summary["users"]["failures"].append(f"User {u.id}: {e}")
        except Exception as e:
            print(f"Failed to fetch users: {e}")

        # ─────────────────────────────────────────────────────────────
        # BACKUP SUMMARY
        # ─────────────────────────────────────────────────────────────
        duration = time.time() - self.start_time
        print("\n" + "="*50)
        print("BACKUP SUMMARY")
        print("="*50)
        
        sys_summary = "✅" if all(self.summary["system"].values()) else "⚠️"
        print(f"System Backup: {sys_summary}")
        print(f"  └─ Google Drive: {'✅' if self.summary['system']['google_drive'] else '❌'}")
        print(f"  └─ SQLite: {'✅' if self.summary['system']['sqlite'] else '❌'}")
        print(f"  └─ PostgreSQL: {'✅' if self.summary['system']['postgres'] else '❌'}")
        
        print("\nUser Backups:")
        print(f"  └─ Users Processed: {self.summary['users']['total_processed']}")
        print(f"  └─ Successful: {self.summary['users']['successful']}")
        print(f"  └─ Failed: {self.summary['users']['failed']}")
        
        if self.summary["users"]["failures"]:
            print("\nFailure Details:")
            for failure in self.summary["users"]["failures"]:
                print(f"  - {failure}")

        print(f"\nDuration: {duration:.2f} seconds")
        print("="*50 + "\n")

        # Push Notification
        self._send_push_summary()

        return self.summary

    def _send_push_summary(self):
        sys_ok = all(self.summary["system"].values())
        users_ok = self.summary["users"]["failed"] == 0
        
        if sys_ok and users_ok:
            send_push_to_all("Backup Complete", "All system and user backups succeeded. ✅")
        else:
            send_push_to_all("Backup Finished with Warnings", "Some backup components failed. Check server logs. ⚠️")
