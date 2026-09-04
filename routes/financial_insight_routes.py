# routes/financial_insight_routes.py  —  URL registration for FINANCIAL INSIGHT endpoints
#
# Auth: User JWT via token_required.
#
# URL prefix: /api/financial-insight/...

from flask import Blueprint
from utils.auth import token_required
from controllers import financial_insight_controller as ctrl

financial_insight_bp = Blueprint("financial_insight", __name__)

# ── Latest / Recent ───────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/latest",
    methods=["GET"],
)(token_required(ctrl.latest_insight))

financial_insight_bp.route(
    "/api/financial-insight/recent",
    methods=["GET"],
)(token_required(ctrl.recent_insights))

# ── By Month ──────────────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/<month>",
    methods=["GET"],
)(token_required(ctrl.insight_by_month))

# ── By Tag ────────────────────────────────────────────────────────────────────

financial_insight_bp.route(
    "/api/financial-insight/tag/<tag>",
    methods=["GET"],
)(token_required(ctrl.insights_by_tag))
