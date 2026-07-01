# routes/budget_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import budget_controller as ctrl

budget_bp = Blueprint('budget', __name__)

budget_bp.route('/api/budget',          methods=['GET'])(token_required(ctrl.get_budget))
budget_bp.route('/api/budget',          methods=['POST'])(token_required(ctrl.save_budget))
budget_bp.route('/api/budget/history',  methods=['GET'])(token_required(ctrl.get_budget_history))
budget_bp.route('/api/set_salary',      methods=['POST'])(token_required(ctrl.set_salary))
budget_bp.route('/api/monthly-summary', methods=['GET'])(token_required(ctrl.get_monthly_summary))
