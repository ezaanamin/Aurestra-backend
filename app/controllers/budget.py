"""Auto-split from legacy app.py — budget controller."""
from flask import Blueprint, jsonify, request, current_app
from functools import wraps
import os
import json
import re
import hashlib
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
    Category, SMSHistory, FinancialInsight, User, DeviceToken,
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

budget_bp = Blueprint("budget", __name__)

# -------------------------
# BUDGET ROUTES
# -------------------------
@budget_bp.route("/api/budget", methods=["POST"])
@token_required
def save_budget(current_user):
    data = request.get_json()
    month = datetime.now().strftime("%Y-%m")
    
    required_fields = ['income', 'needs', 'wants', 'saving']
    
    for field in required_fields:
        if field not in data:
            return jsonify({
                "message": f"Missing '{field}' in request body."
            }), 400
    
    try:
        total_budget_amount = float(data['income'])
        needs_amount = float(data['needs'])
        wants_amount = float(data['wants'])
        saving_amount = float(data['saving'])
    except ValueError:
        return jsonify({
            "message": "Invalid value provided. All amounts must be numbers."
        }), 400
    
    with current_app.app_context():
        try:
            existing_budget = Budget.query.filter_by(month=month).first()

            if existing_budget:
                existing_budget.total_budget = total_budget_amount
                existing_budget.needs = needs_amount
                existing_budget.wants = wants_amount
                existing_budget.saving = saving_amount
                db.session.commit()
                
                return jsonify({
                    "message": f"Budget for {month} updated successfully.",
                    "month": month,
                    "total_budget": total_budget_amount,
                    "needs": needs_amount,
                    "wants": wants_amount,
                    "saving": saving_amount
                }), 200
            else:
                new_budget = Budget(
                    month=month,
                    total_budget=total_budget_amount,
                    needs=needs_amount,
                    wants=wants_amount,
                    saving=saving_amount
                )
                db.session.add(new_budget)
                db.session.commit()
                
                return jsonify({
                    "message": f"Budget for {month} created successfully.",
                    "month": month,
                    "total_budget": total_budget_amount,
                    "needs": needs_amount,
                    "wants": wants_amount,
                    "saving": saving_amount
                }), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"message": f"Database error: {str(e)}"}), 500

@budget_bp.route("/api/budget", methods=["GET"])
@token_required
def get_budget(current_user):
    month = request.args.get('month')
    if not month:
        month = datetime.now().strftime("%Y-%m")

    with current_app.app_context():
        try:
            start_date = datetime.strptime(f"{month}-01", "%Y-%m-%d")
            if start_date.month == 12:
                end_date = start_date.replace(year=start_date.year + 1, month=1)
            else:
                end_date = start_date.replace(month=start_date.month + 1)
        except Exception:
            month = datetime.now().strftime("%Y-%m")
            start_date = datetime.strptime(f"{month}-01", "%Y-%m-%d")
            if start_date.month == 12:
                end_date = start_date.replace(year=start_date.year + 1, month=1)
            else:
                end_date = start_date.replace(month=start_date.month + 1)

        budget = Budget.query.filter_by(month=month).first()

        # Monthly-only actual expense calculations
        tx_query = Transaction.query.filter(
            Transaction.date >= start_date,
            Transaction.date < end_date,
            Transaction.is_deleted == False,
            Transaction.is_spam == False,
            exclude_own_account_transfer_sql(),
        )

        month_txs = tx_query.all()

        monthly_total_expense = sum(
            t.amount for t in month_txs if t.type == 'debit' or (not t.type and t.amount > 0)
        )
        monthly_total_income = sum(
            t.amount for t in month_txs if t.type == 'credit'
        )

        # Monthly Shopping details breakdown
        shopping_txs = [
            t for t in month_txs 
            if (t.purpose and t.purpose.strip().lower() == 'shopping') and (t.type == 'debit' or not t.type)
        ]
        shopping_items = [
            {
                "id": t.id,
                "details": t.shopping_details or t.notes or "Shopping item",
                "amount": float(t.amount),
                "date": t.date.isoformat() if t.date else None
            }
            for t in shopping_txs
        ]
        shopping_total = sum(t["amount"] for t in shopping_items)

        total_budget = budget.total_budget if budget else 0.0
        needs = budget.needs if budget else (total_budget * 0.50)
        wants = budget.wants if budget else (total_budget * 0.30)
        saving = budget.saving if budget else (total_budget * 0.20)
        spending_limit = needs + wants
        created_at_str = budget.created_at.strftime("%Y-%m-%d %H:%M:%S") if budget and budget.created_at else None

        return jsonify({
            "month": month,
            "total_budget": total_budget,
            "needs": needs,
            "wants": wants,
            "saving": saving,
            "spending_limit": spending_limit,
            "created_at": created_at_str,
            "actual": {
                "total_expense": monthly_total_expense,
                "total_income": monthly_total_income,
                "shopping_total": shopping_total,
                "shopping_items": shopping_items
            }
        }), 200

@budget_bp.route("/api/budget/history", methods=["GET"])
def get_budget_history():
    months_to_fetch = 4
    months_list = []
    today = datetime.now()

    for i in range(months_to_fetch):
        target_date = today - relativedelta(months=i)
        months_list.append(target_date.strftime("%Y-%m"))

    with current_app.app_context():
        budget_records = Budget.query.filter(Budget.month.in_(months_list)).all()
        budget_map = {b.month: b for b in budget_records}

        balance_records = MonthlyBalance.query.filter(MonthlyBalance.month.in_(months_list)).all()
        balance_map = {m.month: m for m in balance_records}
        
        history_data = []
        for month_str in months_list:
            budget_rec = budget_map.get(month_str)
            balance_rec = balance_map.get(month_str)
            
            # Dynamic Calculation for Freshness
            dt = datetime.strptime(month_str, "%Y-%m")
            fresh_expense = db.session.query(
                func.sum(case(
                    (Transaction.type == 'debit', Transaction.amount),
                    (Transaction.type == 'credit', -Transaction.amount),
                    else_=0
                ))
            ).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                Transaction.categorization_status != 'pending',
                exclude_own_account_transfer_sql(),
            ).scalar() or 0.0
            
            fresh_income = db.session.query(func.sum(Transaction.amount)).filter(
                extract('year', Transaction.date) == dt.year,
                extract('month', Transaction.date) == dt.month,
                Transaction.type == 'credit',
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True),
                Transaction.categorization_status != 'pending',
                exclude_own_account_transfer_sql(),
            ).scalar() or 0.0
            
            # Use stored balance if available, otherwise 0
            stored_expense = getattr(balance_rec, 'expense', 0.0)
            
            # Prefer fresh calculation if stored is 0 but fresh is > 0
            final_expense = fresh_expense if fresh_expense > 0 else stored_expense
            
            # Closing balance logic
            closing = getattr(balance_rec, 'closing_balance', 0.0)
            
            # Saving = Income - Expense (Simple view)
            # Or use budget.saving if strict.
            # Let's use (Income - Expense) as actual savings
            final_savings = fresh_income - final_expense

            history_data.append({
                "month": month_str,
                "budget": {
                    "total_budget": getattr(budget_rec, 'total_budget', 0.0),
                    "needs": getattr(budget_rec, 'needs', 0.0),
                    "wants": getattr(budget_rec, 'wants', 0.0),
                    "saving": getattr(budget_rec, 'saving', 0.0),
                },
                "actual": {
                    "expense": final_expense,
                    "savings": final_savings,
                    "closing_balance": closing,
                }
            })

    return jsonify(history_data), 200

