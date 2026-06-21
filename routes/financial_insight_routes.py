# routes/financial_insight_routes.py  —  URL registration for FINANCIAL INSIGHT endpoints
#
# Auth: Fixed Agent JWT (AGENT_FIXED_TOKEN) via agent_auth_required.
# This blueprint is consumed by the AI agent, not end-user clients.
# No current_user is injected — controllers are agent-identity aware.
#
# URL prefix: /api/financial-insight/...

from flask import Blueprint
from ai_agent_api import agent_auth_required
from controllers import financial_insight_controller as ctrl

financial_insight_bp = Blueprint("financial_insight", __name__)

# ── Latest / Recent ───────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/latest",
    methods=["GET"],
)(agent_auth_required(ctrl.latest_insight))

financial_insight_bp.route(
    "/api/financial-insight/recent",
    methods=["GET"],
)(agent_auth_required(ctrl.recent_insights))

# ── By Month ──────────────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/<month>",
    methods=["GET"],
)(agent_auth_required(ctrl.insight_by_month))

# ── By Tag ────────────────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/tag/<tag>",
    methods=["GET"],
)(agent_auth_required(ctrl.insights_by_tag))
