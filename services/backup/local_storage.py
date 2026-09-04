import os
import shutil

class BackupLocalStorage:
    """Manages the local filesystem paths and retention policies for backups."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.user_backups_dir = os.path.join(self.base_dir, "backups")
        self.system_backups_dir = os.path.join(self.base_dir, "system_backups")
        self.temp_dir = os.path.join(self.base_dir, "temp_backups")

        os.makedirs(self.user_backups_dir, exist_ok=True)
        os.makedirs(self.system_backups_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

    # ── User Backup Paths ────────────────────────────────────────────────────
    def get_user_backup_dir(self, user_id: int) -> str:
        """Get the isolated folder for a specific user."""
        user_dir = os.path.join(self.user_backups_dir, f"user_{user_id}")
        os.makedirs(user_dir, exist_ok=True)
        return user_dir

    def save_user_backup(self, user_id: int, filename: str, file_bytes: bytes) -> str:
        """Save a user backup file and return its path."""
        user_dir = self.get_user_backup_dir(user_id)
        file_path = os.path.join(user_dir, filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(file_bytes)
        return file_path

    # ── System Backup Paths ──────────────────────────────────────────────────
    def save_system_backup(self, src_path: str, filename: str) -> str:
        """Copy a completed system backup to the system_backups directory."""
        dest_file = os.path.join(self.system_backups_dir, filename)
        shutil.copy2(src_path, dest_file)
        return dest_file

    def get_temp_dir(self) -> str:
        return self.temp_dir

    def cleanup_temp_dir(self):
        """Wipe and recreate the temp_backups directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)

    # ── Retention Policy ─────────────────────────────────────────────────────
    def rotate_user_backups(self, user_id: int, keep: int = 30) -> None:
        """Skip rotation/deletion to keep all records as requested by user."""
        print(f"ℹ️  [Storage] Keeping all records for user {user_id}. Rotation skipped.")
        return

    def rotate_system_backups(self, keep: int = None) -> None:
        """Delete oldest system backups, retaining at least `keep` (default 14 or BACKUP_RETENTION_COUNT env)."""
        if keep is None:
            try:
                keep = int(os.getenv("BACKUP_RETENTION_COUNT", "14"))
            except (ValueError, TypeError):
                keep = 14
        # Safety constraint: Never keep fewer than 1 backup
        keep = max(1, keep)
        self._rotate_directory(self.system_backups_dir, keep, ext=".enc")

    def _rotate_directory(self, directory: str, keep: int, ext: str) -> None:
        try:
            files = sorted(
                [f for f in os.listdir(directory) if f.endswith(ext)],
                reverse=True,
            )
            for old in files[keep:]:
                path = os.path.join(directory, old)
                os.remove(path)
                print(f"🗑️  [Storage] Rotated out: {path}")
        except Exception as e:
            print(f"⚠️  [Storage] Rotation warning for {directory}: {e}")
