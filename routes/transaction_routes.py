# routes/transaction_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import transaction_controller as ctrl

transaction_bp = Blueprint('transactions', __name__)

# Read
transaction_bp.route('/api/latest-transactions',           methods=['GET'])(ctrl.get_latest)
transaction_bp.route('/api/expenses/total',                methods=['GET'])(token_required(ctrl.get_total_expenses))
transaction_bp.route('/api/analytics/trend',              methods=['GET'])(ctrl.get_analytics_trend)
transaction_bp.route('/api/transactions/top-categories',  methods=['GET'])(ctrl.get_top_categories)
transaction_bp.route('/api/categories/monthly',           methods=['GET'])(token_required(ctrl.get_monthly_category_totals))
transaction_bp.route('/api/transactions/uncategorized',   methods=['GET'])(token_required(ctrl.get_uncategorized))
transaction_bp.route('/api/transactions/spam',            methods=['GET'])(token_required(ctrl.get_spam))
transaction_bp.route('/api/transactions/categorized',     methods=['GET'])(token_required(ctrl.get_categorized))

# Write
transaction_bp.route('/api/transactions',                 methods=['POST'])(token_required(ctrl.create_transaction))
transaction_bp.route('/api/transactions/<int:id>',        methods=['PUT'])(ctrl.update_transaction)
transaction_bp.route('/api/transactions/<int:txn_id>',    methods=['DELETE'])(token_required(ctrl.delete_transaction))
transaction_bp.route('/api/transactions/<int:txn_id>/spam', methods=['POST'])(token_required(ctrl.mark_spam))

# Bulk
transaction_bp.route('/api/transactions/bulk-categorize', methods=['POST'])(token_required(ctrl.bulk_categorize))
transaction_bp.route('/api/transactions/bulk-delete',     methods=['POST'])(token_required(ctrl.bulk_delete))
transaction_bp.route('/api/transactions/bulk-spam',       methods=['POST'])(token_required(ctrl.bulk_spam))
