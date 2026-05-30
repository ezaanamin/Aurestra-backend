# routes/account_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import account_controller as ctrl

account_bp = Blueprint('accounts', __name__)

account_bp.route('/api/accounts',              methods=['GET'])(token_required(ctrl.list_accounts))
account_bp.route('/api/accounts',              methods=['POST'])(token_required(ctrl.create_account))
account_bp.route('/api/accounts/<int:account_id>', methods=['PUT'])(token_required(ctrl.update_account))
account_bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])(token_required(ctrl.delete_account))
account_bp.route('/api/accounts/set_balance',  methods=['POST'])(token_required(ctrl.set_balance))
account_bp.route('/api/accounts/match',        methods=['POST'])(token_required(ctrl.match_account))
