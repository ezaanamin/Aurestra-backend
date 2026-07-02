# routes/category_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import category_controller as ctrl

category_bp = Blueprint('categories', __name__)

category_bp.route('/api/categories',                          methods=['GET'])(token_required(ctrl.list_categories))
category_bp.route('/api/categories',                          methods=['POST'])(token_required(ctrl.add_category))
category_bp.route('/api/categories/<int:id>',                 methods=['PUT'])(token_required(ctrl.update_category))
category_bp.route('/api/categories/<int:id>',                 methods=['DELETE'])(token_required(ctrl.delete_category))
category_bp.route('/api/categories/suggest',                  methods=['GET'])(token_required(ctrl.suggest_category))
category_bp.route('/api/categorization-rules',                methods=['GET'])(token_required(ctrl.list_rules))
category_bp.route('/api/categorization-rules',                methods=['POST'])(token_required(ctrl.create_rule))
category_bp.route('/api/categorization-rules/<int:id>',       methods=['DELETE'])(token_required(ctrl.delete_rule))
