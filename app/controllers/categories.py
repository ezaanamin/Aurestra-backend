"""Auto-split from legacy app.py — categories controller."""
from flask import Blueprint, jsonify, request, current_app
from functools import wraps
import os
import json
import re
import hashlib
import requests
import secrets
import random
import base64
import threading
from collections import defaultdict
from datetime import datetime, date, timedelta
from time import time

import jwt
import requests as http_requests
from dateutil.relativedelta import relativedelta
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc, extract, case, text
from sqlalchemy.exc import IntegrityError
from google.oauth2 import id_token
from google.auth.transport import requests
from google_auth_oauthlib.flow import Flow
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.extensions import db
from app.middleware.auth import token_required
from app.models import (
    MonthlyBalance, Transaction, Budget, AccountBalance, SavingsGoal,
    Category, CategoryBucketMapping, SMSHistory, FinancialInsight, User, DeviceToken,
    StatementAnalysis, CategorizationRule,
)
from fetchers import fetch_latest_bank_email, calculate_combined_summary
from drive_utils import (
    get_drive_service, ensure_folder_path, upload_json,
    get_gmail_service, create_message, send_gmail_message, GOOGLE_SIGNIN_OAUTH_SCOPES,
)
from fcm_utils import send_push_to_all, get_push_service_diagnostics
from sms_parser import process_bank_sms, BankAlhabibSMSParser, generate_sms_hash, generate_transaction_hash
from account_matching import match_account_for_notification
from notification_parser import ingest_notification_for_user, list_notifications_for_user
from transfer_matching import exclude_own_account_transfer_sql, is_own_account_transfer_row
from ledger_sync import apply_pending_transaction_ledger, ensure_account_balance_row, log_wallet_attribution

AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://auth.elyestra.org').rstrip('/')
AUTH_API_OWNER_EMAIL = os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')

categories_bp = Blueprint("categories", __name__)

# -------------------------
# CATEGORY ROUTES
# -------------------------
def ensure_default_shopping_category():
    try:
        shopping_cat = Category.query.filter(Category.name.ilike('shopping')).first()
        if not shopping_cat:
            shopping_cat = Category(
                name="Shopping",
                icon="cart",
                color="#F5A623",
                cat_type="spending",
                is_default=True
            )
            db.session.add(shopping_cat)
            db.session.commit()
        elif not shopping_cat.is_default:
            shopping_cat.is_default = True
            db.session.commit()
    except Exception:
        db.session.rollback()

@categories_bp.route('/api/categories', methods=['GET'])
def get_categories():
    ensure_default_shopping_category()
    categories = Category.query.all()
    return jsonify([c.to_dict() for c in categories]), 200

@categories_bp.route('/api/categories', methods=['POST'])
def add_category():
    data = request.json
    name = data.get('name')
    if not name:
        return jsonify({"error": "Name is required"}), 400
    
    # Check if exists
    if Category.query.filter_by(name=name).first():
        return jsonify({"error": "Category already exists"}), 400
    
    category = Category(
        name=name,
        icon=data.get('icon', 'cash'),
        color=data.get('color', '#64748B'),
        cat_type=data.get('cat_type', 'spending'),
        is_default=False
    )
    db.session.add(category)
    db.session.flush() # Get category.id
    
    # Bucket Classification Logic
    def classify_category(cat_name):
        seed_mapping = {
            "rent": "needs", "utilities": "needs", "groceries": "needs", "insurance": "needs", "bills": "needs",
            "dining out": "wants", "entertainment": "wants", "shopping": "wants", "coffee": "wants", "subscriptions": "wants",
            "emergency fund": "savings", "investments": "savings", "goals": "savings", "savings": "savings"
        }
        lower_name = cat_name.lower().strip()
        if lower_name in seed_mapping:
            return seed_mapping[lower_name], "seed_default"
        for key, val in seed_mapping.items():
            if key in lower_name:
                return val, "seed_default"

        # LLM fallback
        try:
            from services.llm_client import generate_llm
            prompt = f"Classify this budget category into exactly one of: Needs, Wants, Savings. Category: '{cat_name}'. Respond with ONLY the bucket name, nothing else."
            res_data = generate_llm(prompt=prompt, timeout=10)
            result = res_data.get("response", "").strip().lower()
            if "need" in result: return "needs", "ai_suggested"
            if "want" in result: return "wants", "ai_suggested"
            if "saving" in result: return "savings", "ai_suggested"
        except Exception as e:
            print(f"LLM classification failed for category {cat_name}: {e}")
        
        return "wants", "seed_default" # Fallback if everything fails
        
    bucket, source = classify_category(name)
    
    # Try to extract user_id from token if present, otherwise default to 1 (single-user dev environment fallback)
    auth_header = request.headers.get('Authorization', '')
    user_id = 1
    if auth_header.startswith('Bearer '):
        try:
            token = auth_header.split(' ')[1]
            payload = jwt.decode(token, current_app.config.get('SECRET_KEY', '0aefb44af279f5bb0ad9ecce393be138'), algorithms=['HS256'])
            user_id = payload.get('sub', 1)
        except:
            pass

    mapping = CategoryBucketMapping(
        user_id=user_id,
        category_id=category.id,
        bucket=bucket,
        source=source
    )
    db.session.add(mapping)
    db.session.commit()
    
    ret = category.to_dict()
    ret["bucket"] = bucket
    return jsonify(ret), 201

@categories_bp.route('/api/categories/<int:id>', methods=['DELETE'])
def delete_category(id):
    category = Category.query.get(id)
    if not category:
        return jsonify({"error": "Category not found"}), 404
    
    if category.is_default or category.name.strip().lower() == "shopping":
        return jsonify({"error": "Cannot delete default/protected category 'Shopping'"}), 400
        
    db.session.delete(category)
    db.session.commit()
    return jsonify({"message": "Category deleted"}), 200

@categories_bp.route('/api/categories/<int:id>', methods=['PUT'])
def update_category(id):
    try:
        data = request.json
        category = Category.query.get(id)
        if not category:
            return jsonify({"error": "Category not found"}), 404
            
        if "name" in data:
            category.name = data["name"]
        if "icon" in data:
            category.icon = data["icon"]
        if "color" in data:
            category.color = data["color"]
        if "cat_type" in data:
            category.cat_type = data["cat_type"]
            
        db.session.commit()
        return jsonify(category.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@categories_bp.route('/api/transactions/<int:id>', methods=['PUT'])
def update_transaction(id):
    try:
        data = request.get_json(silent=True) or {}
        txn = Transaction.query.get(id)
        if not txn:
            return jsonify({"error": "Transaction not found"}), 404

        prev_status = txn.categorization_status
        slug_hint = (
            (data.get("account_balance_source") or data.get("balance_account_slug") or "")
            .strip()
            .lower()
        )

        # Ledger must run *before* overwriting purpose — self-transfer detection uses purpose.
        will_finalize = ("category_id" in data) or ("purpose" in data)
        if prev_status == "pending" and will_finalize:
            if not getattr(txn, "balance_applied", False):
                apply_pending_transaction_ledger(
                    txn, balance_slug_override=slug_hint or None
                )

        new_purpose = data.get("purpose") or data.get("category") or txn.purpose
        shopping_details = data.get("shopping_details")
        if shopping_details is not None:
            shopping_details_val = str(shopping_details).strip()
        else:
            shopping_details_val = (txn.shopping_details or "").strip()

        if str(new_purpose).strip().lower() == "shopping":
            if not shopping_details_val:
                return jsonify({"error": "shopping_details is required for Shopping transactions."}), 400
            txn.shopping_details = shopping_details_val

        if "category_id" in data:
            txn.category_id = data["category_id"]
            category = Category.query.get(data["category_id"])
            if category:
                txn.purpose = category.name  # Backward compat
            txn.categorization_status = 'manual'
        
        if "purpose" in data:
            txn.purpose = data["purpose"]
            # Try to find matching category by name for backward compat
            category = Category.query.filter_by(name=data["purpose"]).first()
            if category:
                txn.category_id = category.id
            txn.categorization_status = 'manual'
            
        if "notes" in data:
            txn.notes = data["notes"]

        db.session.commit()
        return jsonify({
            "message": "Transaction updated", 
            "transaction": txn.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# =======================================================
# TRANSACTION CATEGORIZATION ENDPOINTS (NEW)
# =======================================================



@categories_bp.route("/api/transactions/bulk-categorize", methods=["POST"])
@token_required
def bulk_categorize_transactions(current_user):
    """Bulk update categories for multiple transactions"""
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        category_id = data.get('category_id')
        
        if not transaction_ids or not category_id:
            return jsonify({"error": "Missing required fields"}), 400
        
        category = Category.query.get(category_id)
        if not category:
            return jsonify({"error": "Category not found"}), 404

        slug_hint = ""
        if isinstance(data, dict):
            slug_hint = (
                (data.get("account_balance_source") or data.get("balance_account_slug") or "")
                .strip()
                .lower()
            )

        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                prev_status = tx.categorization_status
                # Ledger while purpose still marks self-transfer (e.g. Easypaisa → bank).
                if prev_status == 'pending':
                    if not getattr(tx, "balance_applied", False):
                        apply_pending_transaction_ledger(
                            tx, balance_slug_override=slug_hint or None
                        )
                tx.category_id = category_id
                tx.purpose = category.name
                tx.categorization_status = 'manual'
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Updated {updated_count} transactions"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@categories_bp.route("/api/transactions/bulk-delete", methods=["POST"])
@token_required
def bulk_delete_transactions(current_user):
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        if not transaction_ids:
            return jsonify({"error": "No transactions selected"}), 400
        
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.is_deleted = True
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Deleted {updated_count} transactions"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@categories_bp.route("/api/transactions/bulk-spam", methods=["POST"])
@token_required
def bulk_mark_spam_transactions(current_user):
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        if not transaction_ids:
            return jsonify({"error": "No transactions selected"}), 400
        
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.is_spam = True
                updated_count += 1
        
        db.session.commit()
        return jsonify({"message": f"Marked {updated_count} transactions as spam"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@categories_bp.route("/api/categories/suggest", methods=["GET"])
@token_required
def suggest_category(current_user):
    """Get suggested category for a merchant"""
    try:
        from model import CategorizationRule
        
        merchant = request.args.get('merchant', '').lower().strip()
        
        if not merchant:
            return jsonify({"suggestion": None}), 200
        
        # Check user rules first
        rule = CategorizationRule.query.filter(
            CategorizationRule.user_id == current_user.id,
            CategorizationRule.merchant_pattern.ilike(f"%{merchant}%")
        ).first()
        
        if rule:
            return jsonify({
                "suggestion": rule.category.to_dict(),
                "source": "user_rule",
                "confidence": "high"
            }), 200
        
        # Check common patterns
        common_patterns = {
            "mcdonald": "Food & Snacks", "burger": "Food & Snacks", "kfc": "Food & Snacks",
            "uber": "Ride / Transport", "careem": "Ride / Transport",
            "netflix": "Entertainment", "spotify": "Entertainment",
            "amazon": "Shopping", "daraz": "Shopping",
            "gym": "Gym & Fitness", "fitness": "Gym & Fitness",
            "hospital": "Healthcare", "pharmacy": "Healthcare",
            "electricity": "Bills & Utilities", "gas": "Bills & Utilities",
        }
        
        for pattern, cat_name in common_patterns.items():
            if pattern in merchant:
                category = Category.query.filter_by(name=cat_name).first()
                if category:
                    return jsonify({
                        "suggestion": category.to_dict(),
                        "source": "common_pattern",
                        "confidence": "medium"
                    }), 200
        
        return jsonify({"suggestion": None}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@categories_bp.route("/api/categories/monthly", methods=["GET"])
@token_required
def get_monthly_category_totals(current_user):
    """
    Get aggregated category totals for selected month sorted highest to lowest.
    Query Parameter: month=YYYY-MM
    """
    try:
        month_str = request.args.get('month')
        if not month_str:
            return jsonify({"message": "Month is required (YYYY-MM)"}), 400
            
        start_date = datetime.strptime(f"{month_str}-01", "%Y-%m-%d")
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1)
            
        totals = db.session.query(
            Transaction.purpose.label('category'),
            func.sum(case(
                (Transaction.type == 'debit', Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0
            )).label('total')
        ).filter(
            Transaction.date >= start_date,
            Transaction.date < end_date,
            Transaction.is_deleted == False,
            Transaction.purpose.isnot(None),
            Transaction.purpose.ilike('Uncategorized') == False,
            exclude_own_account_transfer_sql(),
        ).group_by(Transaction.purpose).all()
        
        sorted_totals = sorted(
            [{"category": t.category, "total": float(t.total or 0)} for t in totals],
            key=lambda x: x["total"],
            reverse=True
        )
        
        return jsonify(sorted_totals), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@categories_bp.route("/api/categorization-rules", methods=["GET", "POST"])
@token_required
def manage_categorization_rules(current_user):
    """Get or create categorization rules"""
    from model import CategorizationRule
    
    if request.method == "GET":
        try:
            rules = CategorizationRule.query.filter_by(
                user_id=current_user.id
            ).order_by(CategorizationRule.created_at.desc()).all()
            
            return jsonify({
                "count": len(rules),
                "rules": [r.to_dict() for r in rules]
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    else:  # POST
        try:
            data = request.get_json()
            merchant_pattern = data.get('merchant_pattern', '').strip()
            category_id = data.get('category_id')
            
            if not merchant_pattern or not category_id:
                return jsonify({"error": "Missing required fields"}), 400
            
            category = Category.query.get(category_id)
            if not category:
                return jsonify({"error": "Category not found"}), 404
            
            # Check if rule already exists
            existing_rule = CategorizationRule.query.filter_by(
                user_id=current_user.id,
                merchant_pattern=merchant_pattern
            ).first()
            
            if existing_rule:
                existing_rule.category_id = category_id
                db.session.commit()
                return jsonify({
                    "message": "Rule updated",
                    "rule": existing_rule.to_dict()
                }), 200
            
            rule = CategorizationRule(
                user_id=current_user.id,
                merchant_pattern=merchant_pattern,
                category_id=category_id
            )
            db.session.add(rule)
            db.session.commit()
            
            return jsonify({
                "message": "Rule created",
                "rule": rule.to_dict()
            }), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500


@categories_bp.route("/api/categorization-rules/<int:id>", methods=["DELETE"])
@token_required
def delete_categorization_rule(current_user, id):
    """Delete a categorization rule"""
    try:
        from model import CategorizationRule
        
        rule = CategorizationRule.query.filter_by(
            id=id,
            user_id=current_user.id
        ).first()
        
        if not rule:
            return jsonify({"error": "Rule not found"}), 404
        
        db.session.delete(rule)
        db.session.commit()
        
        return jsonify({"message": "Rule deleted"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

