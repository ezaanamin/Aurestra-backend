import os
import shutil
from datetime import datetime
from database import app, db
from model import User
from drive_utils import (
    get_gmail_service, 
    list_gmail_messages, 
    delete_gmail_messages,
    get_drive_service,
    ensure_folder_path,
    list_drive_files_in_folder,
    delete_drive_file,
    GMAIL_MODIFY_SCOPES
)

def perform_daily_cleanup():
    """
    1. Local cleanup of temporary staging files.
    NOTE: All Google Drive and Gmail automated deletions have been REMOVED as per user request.
    """
    print(f"🧹 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Local Daily Cleanup...")

    # --- Local Staging Cleanup ---
    base_path = os.path.dirname(os.path.abspath(__file__))
    
    # ONLY clear temp_backups (Staging area for the backup process)
    # This is safe as it only contains files currently being processed.
    temp_dir = os.path.join(base_path, 'temp_backups')
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
            print("✅ [Cleanup] Local temp_backups staging area cleared.")
        except Exception as e:
            print(f"❌ [Cleanup] Failed to clear temp_backups: {e}")

    # --- END ---
    # Automated deletion from Google Drive, Gmail, or 'backups/' has been disabled.
    
    print(f"🏁 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Daily Cleanup Finished.")

if __name__ == "__main__":
    perform_daily_cleanup()
