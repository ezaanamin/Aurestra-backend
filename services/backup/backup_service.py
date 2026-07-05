import os
import gzip
import json
import hashlib
import datetime

from database import db
from model import UserBackup

from services.backup.backup_encryption import BackupEncryption, APP_VERSION, DB_VERSION, ENC_VERSION
from services.backup.metadata import BackupMetadataManager
from services.backup.local_storage import BackupLocalStorage
from services.backup.drive_storage import BackupDriveStorage
from services.backup.user_data import export_user_data, count_tables, restore_user_data, get_latest_dates

def _map_metadata_to_frontend(m: dict) -> dict | None:
    if not m:
        return None
    size_bytes = m.get("Backup Size", 0)
    return {
        "id": m.get("Backup ID", -1),
        "filename": m.get("Filename", ""),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / 1024 / 1024, 2),
        "app_version": m.get("Application Version", "1.0.0"),
        "db_version": m.get("Database Version", 1),
        "enc_version": m.get("Encryption Version", "AES256GCM-v1"),
        "status": m.get("Status", "completed"),
        "table_counts": m.get("Table Counts", {}),
        "latest_dates": m.get("Latest Dates", {}),
        "checksum": m.get("Checksum", ""),
        "created_at": m.get("Created At"),
    }

class BackupService:
    """Core Service API for managing user backups and restorations."""

    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.local_storage = BackupLocalStorage(base_dir)
        self.drive_storage = BackupDriveStorage()

    def create_user_backup(self, user_id: int, decryption_key: str) -> dict | None:
        """
        Creates an AES-256-GCM encrypted backup of user data.
        Returns the new backup metadata dict, or None if skipped (no data/no changes).
        """
        data = export_user_data(user_id, APP_VERSION, DB_VERSION)
        counts = count_tables(data)

        # 1. Skip if user has absolutely no data
        if sum(counts.values()) == 0:
            return None

        # 2. Skip if nothing has changed since the last backup
        data_str = json.dumps(data, default=str, sort_keys=True)
        new_checksum = hashlib.sha256(data_str.encode("utf-8")).hexdigest()

        user_dir = self.local_storage.get_user_backup_dir(user_id)
        existing_meta = BackupMetadataManager.load_metadata(user_dir)
        
        if existing_meta and existing_meta[0].get("Checksum") == new_checksum and existing_meta[0].get("Status") == "completed":
            return _map_metadata_to_frontend(existing_meta[0])

        # 3. Encrypt and verify integrity
        try:
            json_bytes = json.dumps(data, default=str).encode("utf-8")
            compressed = gzip.compress(json_bytes, compresslevel=9)
            encrypted = BackupEncryption.encrypt_user_payload(compressed, decryption_key)

            # Test decryption immediately
            decrypted_compressed = BackupEncryption.decrypt_user_payload(encrypted, decryption_key)
            decrypted_json_bytes = gzip.decompress(decrypted_compressed)
            verified_data = json.loads(decrypted_json_bytes.decode("utf-8"))
            if verified_data.get("user_id") != user_id:
                raise ValueError("Decrypted user_id mismatch during verification.")
        except Exception as e:
            # Handle Failure
            meta_fail = BackupMetadataManager.build_metadata_entry(
                backup_id=-1, user_id=user_id, filename=f"backup_failed_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.enc",
                size_bytes=0, app_version=APP_VERSION, db_version=DB_VERSION, enc_version=ENC_VERSION,
                checksum=new_checksum, status=f"failed: {e}", table_counts={}, latest_dates={}
            )
            BackupMetadataManager.add_backup_metadata(user_dir, meta_fail)
            raise ValueError(f"Backup verification failed: {e}")

        # 4. Save to Disk
        timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        filename = f"{date_str}/backup_{timestamp}.enc"
        
        file_path = self.local_storage.save_user_backup(user_id, filename, encrypted)

        # 5. Save to DB (for backward compatibility with old API)
        backup_record = UserBackup(
            user_id=user_id,
            filename=filename,
            file_path=file_path,
            size_bytes=len(encrypted),
            app_version=APP_VERSION,
            db_version=DB_VERSION,
            enc_version=ENC_VERSION,
            status="completed",
            table_counts=json.dumps(counts),
            checksum=new_checksum,
        )
        db.session.add(backup_record)
        db.session.commit()

        # 6. Save Metadata and Enforce Retention
        latest_dates = get_latest_dates(data)
        meta_success = BackupMetadataManager.build_metadata_entry(
            backup_id=backup_record.id, user_id=user_id, filename=filename,
            size_bytes=len(encrypted), app_version=APP_VERSION, db_version=DB_VERSION, enc_version=ENC_VERSION,
            checksum=new_checksum, status="completed", table_counts=counts, latest_dates=latest_dates
        )
        BackupMetadataManager.add_backup_metadata(user_dir, meta_success)
        self.local_storage.rotate_user_backups(user_id, keep=30)

        # 7. Upload to Google Drive (if enabled)
        self.drive_storage.upload_user_backup(user_id, file_path)

        return _map_metadata_to_frontend(meta_success)

    def list_user_backups(self, user_id: int) -> list:
        """Returns metadata for all backups of a user."""
        user_dir = self.local_storage.get_user_backup_dir(user_id)
        raw_meta = BackupMetadataManager.load_metadata(user_dir)
        return [_map_metadata_to_frontend(m) for m in raw_meta]

    def get_latest_backup(self, user_id: int) -> dict | None:
        """Returns the most recent backup metadata."""
        meta_list = self.list_user_backups(user_id)
        for m in meta_list:
            if m.get("status") == "completed":
                return m
        return None

    def restore_user_backup(self, user_id: int, backup_id: int, decryption_key: str) -> dict:
        """Decrypts a backup and restores rows to the database."""
        # Find path (either via DB or Metadata)
        backup_record = UserBackup.query.filter_by(id=backup_id, user_id=user_id).first()
        if not backup_record:
            raise LookupError(f"Backup {backup_id} not found.")

        if not os.path.exists(backup_record.file_path):
            raise FileNotFoundError("Backup file not found on server.")

        with open(backup_record.file_path, "rb") as f:
            file_bytes = f.read()

        # Decrypt
        compressed = BackupEncryption.decrypt_user_payload(file_bytes, decryption_key)
        json_bytes = gzip.decompress(compressed)
        data = json.loads(json_bytes.decode("utf-8"))

        if data.get("user_id") != user_id:
            raise PermissionError("Backup user_id mismatch — refusing restore.")

        # Restore
        restored_counts = restore_user_data(user_id, data)

        return {
            "backup_id": backup_id,
            "restored_at": datetime.datetime.utcnow().isoformat(),
            "counts": restored_counts,
        }

    def delete_user_backup(self, user_id: int, backup_id: int) -> None:
        """Deletes a backup file and its metadata/DB record."""
        backup_record = UserBackup.query.filter_by(id=backup_id, user_id=user_id).first()
        if not backup_record:
            return

        try:
            if os.path.exists(backup_record.file_path):
                os.remove(backup_record.file_path)
        except OSError as e:
            print(f"[Backup] Warning: could not delete file: {e}")

        db.session.delete(backup_record)
        db.session.commit()

        # Clean metadata
        user_dir = self.local_storage.get_user_backup_dir(user_id)
        meta_list = BackupMetadataManager.load_metadata(user_dir)
        meta_list = [m for m in meta_list if m.get("Backup ID") != backup_id]
        BackupMetadataManager.update_backup_metadata(user_dir, meta_list)
