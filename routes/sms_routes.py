# routes/sms_routes.py

from flask import Blueprint
from utils.auth import token_required
from controllers import sms_controller as ctrl

sms_bp = Blueprint('sms', __name__)

sms_bp.route('/api/sms/process',    methods=['POST'])(ctrl.process_sms)
sms_bp.route('/api/sms/test',       methods=['POST'])(ctrl.test_sms)
sms_bp.route('/api/sms/batch',      methods=['POST'])(token_required(ctrl.process_batch))
sms_bp.route('/api/sms/last-sync',  methods=['GET'])(token_required(ctrl.last_sync))
