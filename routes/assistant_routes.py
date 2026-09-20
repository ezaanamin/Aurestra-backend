# routes/assistant_routes.py

from flask import Blueprint
from utils.assistant_auth import assistant_token_required
from controllers import assistant_controller as ctrl

assistant_bp = Blueprint('assistant', __name__)

# Assistant endpoints — Read-only GET routes authenticated via ASSISTANT_API_TOKEN
assistant_bp.route('/api/assistant/finance-snapshot', methods=['GET'])(assistant_token_required(ctrl.get_finance_snapshot))
assistant_bp.route('/api/assistant/spending',         methods=['GET'])(assistant_token_required(ctrl.get_spending))

