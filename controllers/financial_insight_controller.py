# controllers/financial_insight_controller.py  (Phase 3: user-scoped)
#
# Financial insights are now user-scoped. Agent-level endpoints that need
# cross-user data must use a separate agent-auth path.

from flask import jsonify, request
from model import FinancialInsight
from services import rag_service


def latest_insight(current_user):
    """GET /api/financial-insight/latest"""
    insight = (
        FinancialInsight.query
        .filter_by(user_id=current_user.id)
        .order_by(FinancialInsight.created_at.desc())
        .first()
    )
    if not insight:
        return jsonify({"error": "No insight found."}), 404
    return jsonify(insight.to_dict()), 200


def insight_by_month(current_user, month):
    """GET /api/financial-insight/<month>"""
    insight = rag_service.get_insight_by_month(current_user.id, month)
    if not insight:
        return jsonify({"error": f"No insight found for {month}."}), 404
    return jsonify(insight.to_dict()), 200


def recent_insights(current_user):
    """GET /api/financial-insight/recent?limit=6"""
    try:
        limit = int(request.args.get("limit", 6))
    except (TypeError, ValueError):
        limit = 6
    limit    = max(1, min(limit, 50))
    insights = rag_service.get_insights_for_user(current_user.id, limit)
    return jsonify([i.to_dict() for i in insights]), 200


def insights_by_tag(current_user, tag):
    """GET /api/financial-insight/tag/<tag>"""
    insights = (
        FinancialInsight.query
        .filter(
            FinancialInsight.user_id == current_user.id,
            FinancialInsight.tags.like(f"%{tag}%"),
        )
        .order_by(FinancialInsight.created_at.desc())
        .all()
    )
    if not insights:
        return jsonify({"error": f"No insights found for tag '{tag}'."}), 404
    return jsonify([i.to_dict() for i in insights]), 200