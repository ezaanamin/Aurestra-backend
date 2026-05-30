# routes/savings_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import savings_controller as ctrl

savings_bp = Blueprint('savings', __name__)

savings_bp.route('/api/savings-goals',                        methods=['GET'])(token_required(ctrl.list_goals))
savings_bp.route('/api/savings-goals',                        methods=['POST'])(token_required(ctrl.create_goal))
savings_bp.route('/api/savings-goals/<int:id>',               methods=['PUT'])(token_required(ctrl.update_goal))
savings_bp.route('/api/savings-goals/<int:id>',               methods=['DELETE'])(token_required(ctrl.delete_goal))
savings_bp.route('/api/savings-goals/<int:id>/contribute',    methods=['POST'])(token_required(ctrl.contribute))
