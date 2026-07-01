# routes/auth_routes.py  —  URL registration only

from flask import Blueprint
from utils.auth import token_required
from controllers import auth_controller as ctrl

auth_bp = Blueprint('auth', __name__)

# ── Email / Password Auth ──────────────────────────────────────────────────────
auth_bp.route('/api/auth/register',            methods=['POST'])(ctrl.email_register)
auth_bp.route('/api/auth/login',               methods=['POST'])(ctrl.email_login)
auth_bp.route('/api/auth/verify-email',        methods=['POST'])(ctrl.verify_email)
auth_bp.route('/api/auth/resend-verification', methods=['POST'])(ctrl.resend_verification)
auth_bp.route('/api/auth/forgot-password',     methods=['POST'])(ctrl.forgot_password)
auth_bp.route('/api/auth/reset-password',      methods=['POST'])(ctrl.do_reset_password)

# ── Google OAuth ───────────────────────────────────────────────────────────────
auth_bp.route('/api/google/login',  methods=['POST'])(ctrl.google_login)
auth_bp.route('/api/auth/verify',   methods=['POST'])(ctrl.auth_verify)   # legacy

# ── Authenticated ──────────────────────────────────────────────────────────────
auth_bp.route('/api/auth/status', methods=['GET'])(token_required(ctrl.auth_status))
auth_bp.route('/api/profile',     methods=['GET'])(token_required(ctrl.get_profile))
auth_bp.route('/api/profile',     methods=['POST'])(token_required(ctrl.update_profile))
