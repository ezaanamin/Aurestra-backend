# routes/live_state_routes.py  —  URL registration for LIVE_STATE endpoints
#
# Auth: Fixed Agent JWT (AGENT_FIXED_TOKEN) via agent_auth_required.
# This blueprint is consumed by the AI agent, not end-user clients.
# No current_user is injected — controllers are agent-identity aware.
#
# URL prefix: /api/live-state/...

from flask import Blueprint
from ai_agent_api import agent_auth_required
from controllers import live_state_controller as ctrl

live_state_bp = Blueprint("live_state", __name__)

# ── Balance ───────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/balance",
    methods=["GET"],
)(agent_auth_required(ctrl.current_balance))

live_state_bp.route(
    "/api/live-state/balance/available",
    methods=["GET"],
)(agent_auth_required(ctrl.available_balance))

# ── Transactions ──────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/transactions/last",
    methods=["GET"],
)(agent_auth_required(ctrl.last_transaction))

live_state_bp.route(
    "/api/live-state/transactions/pending",
    methods=["GET"],
)(agent_auth_required(ctrl.pending_transactions))

live_state_bp.route(
    "/api/live-state/transactions/today",
    methods=["GET"],
)(agent_auth_required(ctrl.todays_transactions))

# ── Income ────────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/salary-status",
    methods=["GET"],
)(agent_auth_required(ctrl.salary_status))

# ── Spending ──────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/spending/today",
    methods=["GET"],
)(agent_auth_required(ctrl.spending_today))

# ── Recurring / Bills ─────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/subscriptions/this-week",
    methods=["GET"],
)(agent_auth_required(ctrl.subscriptions_this_week))

live_state_bp.route(
    "/api/live-state/bills/<bill_name>/status",
    methods=["GET"],
)(agent_auth_required(ctrl.bill_status))

# ── Credit / Overdraft ────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/credit-status",
    methods=["GET"],
)(agent_auth_required(ctrl.credit_status))

# ── Status ───────────────────────────────────────────────────────────────────

live_state_bp.route(
    "/api/live-state/balance/total",
    methods=["GET"],
)(agent_auth_required(ctrl.total_balance))

live_state_bp.route(
    "/api/live-state/status/online",
    methods=["GET"],
)(agent_auth_required(ctrl.online_status))

