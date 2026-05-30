# routes/notification_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import notification_controller as ctrl

notification_bp = Blueprint('notifications', __name__)

notification_bp.route('/api/notifications/ingest', methods=['POST'])(token_required(ctrl.ingest))
notification_bp.route('/api/notifications',        methods=['GET'])(token_required(ctrl.list_notifications))
notification_bp.route('/api/register-device',      methods=['POST'])(ctrl.register_device)
notification_bp.route('/api/send-test',            methods=['POST'])(ctrl.send_test)
