# routes/export_routes.py — Secure export blueprint registration

from flask import Blueprint
from utils.auth import token_required
from controllers import export_controller as ctrl

export_bp = Blueprint('export', __name__)

export_bp.route('/api/export/transactions', methods=['POST'])(token_required(ctrl.export_transactions))
