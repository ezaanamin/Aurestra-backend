import sys
from app import app
from services.backup.backup_manager import BackupOrchestrator

with app.app_context():
    orchestrator = BackupOrchestrator(app)
    orchestrator.perform_full_backup()

if __name__ == '__main__':
    test_backup()
