# routes/system_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import system_controller as ctrl

system_bp = Blueprint('system', __name__)

system_bp.route('/api/health',              methods=['GET'])(ctrl.health_check)
system_bp.route('/api/health/db',           methods=['GET'])(ctrl.db_health_check)
system_bp.route('/api/debug/push-diagnostics', methods=['GET'])(ctrl.push_diagnostics)
system_bp.route('/api/debug/push-status',   methods=['GET'])(token_required(ctrl.debug_push_status))
system_bp.route('/api/backup/trigger',      methods=['POST'])(token_required(ctrl.trigger_backup))
system_bp.route('/api/insights',            methods=['GET'])(token_required(ctrl.list_api_insights))
system_bp.route('/test',                    methods=['GET'])(ctrl.test_route)
system_bp.route('/',                        methods=['GET'])(ctrl.home)
