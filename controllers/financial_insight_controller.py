# controllers/financial_insight_controller.py
#
# Returns financial summaries / insights (FinancialInsight model) for the
# AI agent. Consumed via the same agent-JWT auth as live_state_controller —
# no current_user is injected, these are agent-identity aware.

from flask import jsonify, request
from models.model import FinancialInsight


def _insight_not_found(month: str | None = None):
    msg = f"No insight found for {month}." if month else "No insights found."
    return jsonify({"error": msg}), 404


def latest_insight():
    """
    GET /api/financial-insight/latest

    Returns the most recently generated financial insight/summary.
    """
    insight = FinancialInsight.query.order_by(
        FinancialInsight.created_at.desc()
    ).first()

    if not insight:
        return _insight_not_found()

    return jsonify(insight.to_dict()), 200


def insight_by_month(month):
    """
    GET /api/financial-insight/<month>   (month format: YYYY-MM)

    Returns the financial insight/summary for a specific month.
    If multiple rows exist for the same month, the most recent is returned.
    """
    insight = (
        FinancialInsight.query.filter_by(month=month)
        .order_by(FinancialInsight.created_at.desc())
        .first()
    )

    if not insight:
        return _insight_not_found(month)

    return jsonify(insight.to_dict()), 200


def recent_insights():
    """
    GET /api/financial-insight/recent?limit=6

    Returns the most recent N insights (default 6), newest first.
    Useful for trend summaries / RAG context windows.
    """
    try:
        limit = int(request.args.get("limit", 6))
    except (TypeError, ValueError):
        limit = 6
    limit = max(1, min(limit, 50))  # sane bounds

    insights = (
        FinancialInsight.query.order_by(FinancialInsight.created_at.desc())
        .limit(limit)
        .all()
    )

    return jsonify([i.to_dict() for i in insights]), 200


def insights_by_tag(tag):
    """
    GET /api/financial-insight/tag/<tag>

    Returns insights matching a given retrieval tag
    (e.g. "savings_trend", "high_expenses").
    """
    insights = (
        FinancialInsight.query.filter(FinancialInsight.tags.like(f"%{tag}%"))
        .order_by(FinancialInsight.created_at.desc())
        .all()
    )

    if not insights:
        return jsonify({"error": f"No insights found for tag '{tag}'."}), 404

    return jsonify([i.to_dict() for i in insights]), 200
