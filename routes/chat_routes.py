# routes/chat_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import chat_controller as ctrl

chat_bp = Blueprint('chat', __name__)

chat_bp.route('/api/chat', methods=['POST'])(token_required(ctrl.chat_session))
chat_bp.route('/api/chat/history', methods=['GET'])(token_required(ctrl.get_chat_history))
chat_bp.route('/api/chat/history', methods=['DELETE'])(token_required(ctrl.clear_chat_history))

