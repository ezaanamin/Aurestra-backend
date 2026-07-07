from flask import Blueprint
from utils.auth import token_required
from controllers import subscription_controller as ctrl

subscription_bp = Blueprint('subscription', __name__)

subscription_bp.route('/api/subscription/plans', methods=['GET'])(token_required(ctrl.get_plans))
subscription_bp.route('/api/subscription/status', methods=['GET'])(token_required(ctrl.get_status))
subscription_bp.route('/api/subscription/subscribe', methods=['POST'])(token_required(ctrl.subscribe))
