import sys
from app import app
from backup_manager import BackupManager

def test_backup():
    print("Testing backup manager...")
    bm = BackupManager()
    bm.init_app(app)
    with app.app_context():
        results = bm.perform_backup()
        print("Backup results:", results)

if __name__ == '__main__':
    test_backup()
