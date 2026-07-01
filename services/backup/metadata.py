import os
import json
from typing import List, Dict

class BackupMetadataManager:
    """Manages reading and writing metadata.json for user backups."""
    
    @staticmethod
    def get_metadata_path(user_backup_dir: str) -> str:
        return os.path.join(user_backup_dir, "metadata.json")

    @staticmethod
    def load_metadata(user_backup_dir: str) -> List[Dict]:
        """Load all metadata for a user's backups from metadata.json."""
        metadata_path = BackupMetadataManager.get_metadata_path(user_backup_dir)
        if not os.path.exists(metadata_path):
            return []
        try:
            with open(metadata_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Metadata] Error loading {metadata_path}: {e}")
            return []

    @staticmethod
    def save_metadata(user_backup_dir: str, metadata: List[Dict]) -> None:
        """Save metadata list to metadata.json."""
        os.makedirs(user_backup_dir, exist_ok=True)
        metadata_path = BackupMetadataManager.get_metadata_path(user_backup_dir)
        try:
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
        except Exception as e:
            print(f"[Metadata] Error saving {metadata_path}: {e}")

    @staticmethod
    def add_backup_metadata(user_backup_dir: str, new_backup: Dict) -> None:
        """Append a new backup metadata entry and save."""
        metadata = BackupMetadataManager.load_metadata(user_backup_dir)
        # Ensure we don't duplicate by Backup ID or Checksum if needed
        # Just append for now
        metadata.append(new_backup)
        # Sort by Created At descending
        metadata.sort(key=lambda x: x.get("Created At", ""), reverse=True)
        BackupMetadataManager.save_metadata(user_backup_dir, metadata)
        
    @staticmethod
    def update_backup_metadata(user_backup_dir: str, metadata_list: List[Dict]) -> None:
        BackupMetadataManager.save_metadata(user_backup_dir, metadata_list)

    @staticmethod
    def build_metadata_entry(
        backup_id: int, 
        user_id: int, 
        filename: str,
        size_bytes: int, 
        app_version: str, 
        db_version: int, 
        enc_version: str, 
        checksum: str, 
        status: str, 
        backup_type: str = "encrypted_json"
    ) -> Dict:
        """Helper to build a standardized metadata entry."""
        import datetime
        return {
            "Backup ID": backup_id,
            "User ID": user_id,
            "Filename": filename,
            "Created At": datetime.datetime.utcnow().isoformat(),
            "Application Version": app_version,
            "Database Version": db_version,
            "Backup Size": size_bytes,
            "Encryption Version": enc_version,
            "Checksum": checksum,
            "Status": status,
            "Backup Type": backup_type
        }
