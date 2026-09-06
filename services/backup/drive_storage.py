import os
import datetime

class BackupDriveStorage:
    """Manages uploading backups to Google Drive."""

    def __init__(self):
        # We import locally to prevent circular dependencies if needed
        from drive_utils import get_drive_service, ensure_folder_path, upload_file_from_path
        self.get_drive_service = get_drive_service
        self.ensure_folder_path = ensure_folder_path
        self.upload_file_from_path = upload_file_from_path

    def _get_admin_service(self):
        from model import User
        admin_email = os.getenv("BANK_EMAIL_ACCOUNT")
        # 1. Try configured admin/bank emails
        admin_email = os.getenv("BANK_EMAIL_ACCOUNT") or os.getenv("AUTH_API_OWNER_EMAIL") or os.getenv("SMTP_EMAIL") or "ezaan.amin@gmail.com"
        if admin_email:
            from types import SimpleNamespace
            admin_user = SimpleNamespace(email=admin_email, google_refresh_token=None)
            service = self.get_drive_service(admin_user)
            if service:
                return service

        # Fallback to a user with a google_refresh_token
        # 2. Try default central auth service without user object
        service = self.get_drive_service(None)
        if service:
            return service

        # 3. Fallback to a user with a local google_refresh_token
        admin_user = User.query.filter(User.google_refresh_token.isnot(None)).first()
        if admin_user:
            return self.get_drive_service(admin_user)
        return None

    def upload_system_backup(self, filepath: str) -> bool:
        """Uploads a system backup to 'Aurestra Backups/YYYY-MM-DD/'."""
        service = self._get_admin_service()
        if not service:
            print("⚠️  [Drive] No Google-linked user found. Skipping system backup upload.")
            return False

        try:
            date_folder = datetime.datetime.now().strftime("%Y-%m-%d")
            folder_id = self.ensure_folder_path(service, ["Aurestra Backups", date_folder])
            if not folder_id:
                print("❌ [Drive] Could not create/find Drive folder.")
                return False

            filename = os.path.basename(filepath)
            success = self.upload_file_from_path(service, folder_id, filename, filepath)
            return bool(success)
        except Exception as e:
            print(f"❌ [Drive] Error uploading system backup: {e}")
            return False

    def upload_user_backup(self, user_id: int, filepath: str) -> bool:
        """Uploads a user backup to 'aurestra_user_backup/user_{user_id}/{date}/'."""
        service = self._get_admin_service()
        if not service:
            print("⚠️  [Drive] No Google-linked user found. Skipping user backup upload.")
            return False

        try:
            date_folder = datetime.datetime.now().strftime("%Y-%m-%d")
            folder_id = self.ensure_folder_path(service, ["aurestra_user_backup", f"user_{user_id}", date_folder])
            if not folder_id:
                print("❌ [Drive] Could not create/find Drive folder for user.")
                return False

            filename = os.path.basename(filepath)
            success = self.upload_file_from_path(service, folder_id, filename, filepath)
            return bool(success)
        except Exception as e:
            print(f"❌ [Drive] Error uploading user backup: {e}")
            return False
