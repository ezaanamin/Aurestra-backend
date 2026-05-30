# routes/report_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import report_controller as ctrl

report_bp = Blueprint('reports', __name__)

report_bp.route('/api/reports/statement',          methods=['POST'])(token_required(ctrl.get_statement))
report_bp.route('/api/reports/statement/calculate', methods=['POST'])(token_required(ctrl.calculate_statement))
report_bp.route('/api/reports/statement/mark-read', methods=['POST'])(token_required(ctrl.mark_read))
report_bp.route('/api/insights/generate',          methods=['POST'])(token_required(ctrl.generate_insights))
report_bp.route('/api/calculate-summary',          methods=['POST'])(ctrl.calculate_summary)
