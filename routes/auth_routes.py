# routes/auth_routes.py  —  URL registration only

from flask import Blueprint
from utils.auth import token_required
from controllers import auth_controller as ctrl

auth_bp = Blueprint('auth', __name__)

auth_bp.route('/api/google/login',  methods=['POST'])(ctrl.google_login)
auth_bp.route('/api/auth/verify',   methods=['POST'])(ctrl.auth_verify)
auth_bp.route('/api/auth/status',   methods=['GET'])(token_required(ctrl.auth_status))
auth_bp.route('/api/profile',       methods=['GET'])(token_required(ctrl.get_profile))
auth_bp.route('/api/profile',       methods=['POST'])(token_required(ctrl.update_profile))
