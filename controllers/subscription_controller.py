from flask import request, jsonify
from datetime import datetime, timedelta
from database import db
from model import Plan, User
from services.subscription_service import get_user_subscription_info, DEFAULT_PLANS

def get_plans(current_user):
    """
    GET /api/subscription/plans
    Returns list of all available plans.
    """
    try:
        plans = Plan.query.all()
        # If plans table is empty for some reason, return default plans
        if not plans:
            return jsonify([
                {
                    "plan_id": plan_id,
                    "name": info["name"],
                    "price": info["price"],
                    "currency": info["currency"],
                    "description": info["description"],
                    "features": info["features"],
                    "limits": info["limits"]
                }
                for plan_id, info in DEFAULT_PLANS.items()
            ]), 200
            
        return jsonify([p.to_dict() for p in plans]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_status(current_user):
    """
    GET /api/subscription/status
    Returns the subscription status and remaining limits.
    """
    try:
        info = get_user_subscription_info(current_user)
        return jsonify(info), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def subscribe(current_user):
    """
    POST /api/subscription/subscribe
    Simulates buying/upgrading to a plan.
    Request body:
    {
        "plan_id": "plus"  # "free", "plus", "pro", "developer"
    }
    """
    data = request.get_json() or {}
    plan_id = data.get("plan_id", "").lower().strip()
    
    if plan_id not in DEFAULT_PLANS:
        return jsonify({"error": f"Invalid plan_id. Allowed: {list(DEFAULT_PLANS.keys())}"}), 400
        
    try:
        current_user.current_plan_id = plan_id
        current_user.subscription_status = "active"
        current_user.subscription_started_at = datetime.utcnow()
        
        # Set subscription expiration to 30 days from now (except for free/developer plans)
        if plan_id in ["free", "developer"]:
            current_user.subscription_expires_at = None
        else:
            current_user.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
            
        db.session.commit()
        
        # Return updated subscription info
        info = get_user_subscription_info(current_user)
        return jsonify({
            "message": f"Successfully subscribed to {plan_id.upper()} plan.",
            "subscription": info
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
