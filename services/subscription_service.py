import json
from datetime import datetime
from database import db
from model import Plan, User, ChatMessage, UploadedReceipt

# Feature names
FEATURE_AI_CHAT = "ai_chat"
FEATURE_INVESTMENT_PLANNING = "investment_planning"
FEATURE_DEEP_AI_ANALYSIS = "deep_ai_analysis"
FEATURE_AI_REPORTS = "ai_reports"
FEATURE_ADVANCED_ANALYTICS = "advanced_analytics"
FEATURE_RAG_MEMORY = "rag_memory"
FEATURE_EXPORT_REPORTS = "export_reports"

# Default plan details (used for seeding and fallback if DB is not populated)
DEFAULT_PLANS = {
    "free": {
        "name": "Free Plan",
        "price": 0.0,
        "currency": "PKR",
        "description": "Basic usage plan for normal personal finance tracking.",
        "features": {
            FEATURE_AI_CHAT: True,
            FEATURE_INVESTMENT_PLANNING: False,
            FEATURE_DEEP_AI_ANALYSIS: False,
            FEATURE_AI_REPORTS: False,
            FEATURE_ADVANCED_ANALYTICS: False,
            FEATURE_RAG_MEMORY: False,
            FEATURE_EXPORT_REPORTS: False,
        },
        "limits": {
            "ai_chat_limit": 10,
            "ocr_limit": 5,
        }
    },
    "plus": {
        "name": "Aurestra Plus",
        "price": 499.0,
        "currency": "PKR",
        "description": "Standard financial intelligence, reports, sync, and unlimited AI assistant.",
        "features": {
            FEATURE_AI_CHAT: True,
            FEATURE_INVESTMENT_PLANNING: False,
            FEATURE_DEEP_AI_ANALYSIS: True,
            FEATURE_AI_REPORTS: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_RAG_MEMORY: True,
            FEATURE_EXPORT_REPORTS: True,
        },
        "limits": {
            "ai_chat_limit": -1,  # -1 means unlimited
            "ocr_limit": -1,      # -1 means unlimited
        }
    },
    "pro": {
        "name": "Aurestra Pro",
        "price": 999.0,
        "currency": "PKR",
        "description": "Unlock investment planning, financial roadmap generation, priority processing, and beta agents.",
        "features": {
            FEATURE_AI_CHAT: True,
            FEATURE_INVESTMENT_PLANNING: True,
            FEATURE_DEEP_AI_ANALYSIS: True,
            FEATURE_AI_REPORTS: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_RAG_MEMORY: True,
            FEATURE_EXPORT_REPORTS: True,
        },
        "limits": {
            "ai_chat_limit": -1,
            "ocr_limit": -1,
        }
    },
    "developer": {
        "name": "Developer Plan",
        "price": 0.0,
        "currency": "PKR",
        "description": "Internal developer testing account bypassing all limits.",
        "features": {
            FEATURE_AI_CHAT: True,
            FEATURE_INVESTMENT_PLANNING: True,
            FEATURE_DEEP_AI_ANALYSIS: True,
            FEATURE_AI_REPORTS: True,
            FEATURE_ADVANCED_ANALYTICS: True,
            FEATURE_RAG_MEMORY: True,
            FEATURE_EXPORT_REPORTS: True,
        },
        "limits": {
            "ai_chat_limit": -1,
            "ocr_limit": -1,
        }
    }
}

def seed_plans():
    """Seed default plans into the database."""
    try:
        for plan_id, info in DEFAULT_PLANS.items():
            plan = Plan.query.filter_by(plan_id=plan_id).first()
            if not plan:
                plan = Plan(
                    plan_id=plan_id,
                    name=info["name"],
                    price=info["price"],
                    currency=info["currency"],
                    description=info["description"],
                    features_json=json.dumps(info["features"]),
                    limits_json=json.dumps(info["limits"])
                )
                db.session.add(plan)
            else:
                # Update existing to be in sync
                plan.name = info["name"]
                plan.price = info["price"]
                plan.currency = info["currency"]
                plan.description = info["description"]
                plan.features_json = json.dumps(info["features"])
                plan.limits_json = json.dumps(info["limits"])
        db.session.commit()
        print("✅ Subscription plans seeded successfully.")
    except Exception as e:
        db.session.rollback()
        print(f"❌ Failed to seed subscription plans: {e}")

def get_user_plan(user: User) -> Plan:
    """Returns the Plan model associated with the user, defaulting to Free."""
    plan_id = getattr(user, "current_plan_id", "free") or "free"
    # Ensure status is active / not expired
    expires_at = getattr(user, "subscription_expires_at", None)
    status = getattr(user, "subscription_status", "active") or "active"
    
    if status == "expired" or (expires_at and expires_at < datetime.utcnow()):
        plan_id = "free"
        
    plan = Plan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        # Fallback if DB not seeded
        info = DEFAULT_PLANS.get(plan_id, DEFAULT_PLANS["free"])
        plan = Plan(
            plan_id=plan_id,
            name=info["name"],
            price=info["price"],
            currency=info["currency"],
            description=info["description"],
            features_json=json.dumps(info["features"]),
            limits_json=json.dumps(info["limits"])
        )
    return plan

def can_access(user: User, feature: str) -> bool:
    """Central permission checker. If developer, always True. If free/plus/pro, checks JSON permissions."""
    if not user:
        return False
        
    plan_id = getattr(user, "current_plan_id", "free") or "free"
    # Developer bypass
    if plan_id == "developer":
        return True
        
    plan = get_user_plan(user)
    try:
        features = json.loads(plan.features_json or "{}")
    except:
        features = {}
        
    # Return permission
    return bool(features.get(feature, False))

def get_current_month_usage(user_id: int):
    """Calculates user's usage of AI chat messages and OCR scans in the current calendar month."""
    now = datetime.utcnow()
    # Start of current month in UTC
    month_start = datetime(now.year, now.month, 1)
    
    # Query AI messages count (messages sent by the user)
    ai_messages = ChatMessage.query.filter(
        ChatMessage.user_id == user_id,
        ChatMessage.role == "user",
        ChatMessage.created_at >= month_start
    ).count()
    
    # Query OCR scans count (receipts uploaded)
    ocr_scans = UploadedReceipt.query.filter(
        UploadedReceipt.user_id == user_id,
        UploadedReceipt.created_at >= month_start
    ).count()
    
    return {
        "ai_chat": ai_messages,
        "ocr_scans": ocr_scans
    }

def get_user_subscription_info(user: User) -> dict:
    """Returns the subscription info dictionary to be embedded in User.to_dict()."""
    plan = get_user_plan(user)
    plan_dict = plan.to_dict()
    
    # Calculate limits remaining
    usage = get_current_month_usage(user.id)
    
    ai_chat_limit = plan_dict["limits"].get("ai_chat_limit", 10)
    ocr_limit = plan_dict["limits"].get("ocr_limit", 5)
    
    plan_id = getattr(user, "current_plan_id", "free") or "free"
    # Developer overrides limits to -1 (unlimited)
    if plan_id == "developer":
        ai_chat_limit = -1
        ocr_limit = -1
        
    limits_remaining = {
        "ai_chat": {
            "limit": ai_chat_limit,
            "used": usage["ai_chat"],
            "remaining": max(0, ai_chat_limit - usage["ai_chat"]) if ai_chat_limit >= 0 else -1
        },
        "ocr": {
            "limit": ocr_limit,
            "used": usage["ocr_scans"],
            "remaining": max(0, ocr_limit - usage["ocr_scans"]) if ocr_limit >= 0 else -1
        }
    }
    
    expires_at = getattr(user, "subscription_expires_at", None)
    started_at = getattr(user, "subscription_started_at", None)
    status = getattr(user, "subscription_status", "active") or "active"
    
    return {
        "current_plan": {
            "plan_id": plan.plan_id,
            "name": plan.name,
            "status": status,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "started_at": started_at.isoformat() if started_at else None,
        },
        "features_allowed": plan_dict["features"],
        "limits_remaining": limits_remaining
    }

def has_remaining_limit(user: User, limit_type: str) -> bool:
    """Helper to check if user has remaining limits for ai_chat or ocr."""
    if not user:
        return False
        
    plan_id = getattr(user, "current_plan_id", "free") or "free"
    if plan_id == "developer":
        return True
        
    plan = get_user_plan(user)
    try:
        limits = json.loads(plan.limits_json or "{}")
    except:
        limits = {}
        
    usage = get_current_month_usage(user.id)
    if limit_type == "ai_chat":
        limit = limits.get("ai_chat_limit", 10)
        return limit < 0 or usage["ai_chat"] < limit
    elif limit_type == "ocr":
        limit = limits.get("ocr_limit", 5)
        return limit < 0 or usage["ocr_scans"] < limit
        
    return False
