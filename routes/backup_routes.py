# routes/backup_routes.py

from flask import Blueprint
from utils.auth import token_required, decryption_key_required
from controllers import backup_controller as ctrl

backup_bp = Blueprint('backup', __name__)

# Read — token only (metadata never contains sensitive data)
backup_bp.route('/api/backup/list',              methods=['GET'])(token_required(ctrl.list_backups))
backup_bp.route('/api/backup/latest',            methods=['GET'])(token_required(ctrl.latest_backup))

# Write — decryption key required (create/restore encrypt/decrypt data)
backup_bp.route('/api/backup/create',            methods=['POST'])(token_required(decryption_key_required(ctrl.create_backup)))
backup_bp.route('/api/backup/restore/<int:backup_id>', methods=['POST'])(token_required(decryption_key_required(ctrl.restore_backup)))

# Delete — token only (no data exposure)
backup_bp.route('/api/backup/<int:backup_id>',   methods=['DELETE'])(token_required(ctrl.delete_backup))
