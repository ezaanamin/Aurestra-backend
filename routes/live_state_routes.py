# routes/live_state_routes.py  —  URL registration for LIVE_STATE endpoints
#
# Follows the existing pattern:
#   - Blueprint is the ONLY thing in a routes/ file
#   - All URLs are registered here via .route()
#   - Auth is applied at the point of registration with token_required()
#   - Controllers handle HTTP; services handle logic
#
# URL prefix: /api/live-state/...

from flask import Blueprint
from utils.auth import token_required
from controllers import live_state_controller as ctrl

live_state_bp = Blueprint("live_state", __name__)

# ── Balance ───────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/balance",
    methods=["GET"],
)(token_required(ctrl.current_balance))

live_state_bp.route(
    "/api/live-state/balance/available",
    methods=["GET"],
)(token_required(ctrl.available_balance))

# ── Transactions ──────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/transactions/last",
    methods=["GET"],
)(token_required(ctrl.last_transaction))

live_state_bp.route(
    "/api/live-state/transactions/pending",
    methods=["GET"],
)(token_required(ctrl.pending_transactions))

live_state_bp.route(
    "/api/live-state/transactions/today",
    methods=["GET"],
)(token_required(ctrl.todays_transactions))

# ── Income ────────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/salary-status",
    methods=["GET"],
)(token_required(ctrl.salary_status))

# ── Spending ──────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/spending/today",
    methods=["GET"],
)(token_required(ctrl.spending_today))

# ── Recurring / Bills ─────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/subscriptions/this-week",
    methods=["GET"],
)(token_required(ctrl.subscriptions_this_week))

live_state_bp.route(
    "/api/live-state/bills/<bill_name>/status",
    methods=["GET"],
)(token_required(ctrl.bill_status))

# ── Credit / Overdraft ────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/credit-status",
    methods=["GET"],
)(token_required(ctrl.credit_status))
