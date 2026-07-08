"""Auto-split from legacy app.py — summary controller."""
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

AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://9938-119-73-101-66.ngrok-free.app').rstrip('/')
AUTH_API_OWNER_EMAIL = os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')

summary_bp = Blueprint("summary", __name__)

# -------------------------
# SUMMARY ROUTES
# -------------------------
@summary_bp.route("/api/monthly-summary", methods=["GET"])
@token_required
def get_monthly_summary_from_db(current_user):
    current_month = datetime.now().strftime("%Y-%m")
    
    with current_app.app_context():
        # 1. ALWAYS Calculate Truth from Transaction Table
        dt = datetime.now()
        
        # Dynamic Expense Calculation
        dynamic_expense = db.session.query(
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

        # Dynamic Income Calculation 
        dynamic_income_tx = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            Transaction.categorization_status != 'pending',
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0
        
        # Check budget for income override
        budget_entry = Budget.query.filter_by(user_id=current_user.id, month=current_month).first()
        final_income = dynamic_income_tx
        if budget_entry and budget_entry.total_budget > 0:
            final_income = budget_entry.total_budget
            
        final_savings = final_income - dynamic_expense

        # 2. Update/Create MonthlyBalance persistence
        summary = MonthlyBalance.query.filter_by(user_id=current_user.id, month=current_month).first()
        
        # FIX: Closing Balance should be the TOTAL CURRENT BALANCE for THIS user only
        total_current_balance = 0
        account_balances = AccountBalance.query.filter_by(user_id=current_user.id).all()
        for acc in account_balances:
            total_current_balance += acc.current_balance

        if not summary:
            # Create new
            summary = MonthlyBalance(
                user_id=current_user.id,
                source="auto-dynamic",
                month=current_month,
                opening_balance=0,
                closing_balance=total_current_balance,
                expense=dynamic_expense,
                savings=final_savings,
                fetched_at=datetime.now()
            )
            db.session.add(summary)
        else:
            # Update existing
            summary.closing_balance = total_current_balance # Update with actual balance
            summary.expense = dynamic_expense
            # summary.income = final_income # REMOVED
            summary.savings = final_savings
            summary.fetched_at = datetime.now()
            
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Failed to update summary persistence: {e}")

        # 3. Return Dynamic Data
        return jsonify({
            "month": current_month,
            "opening_balance": summary.opening_balance,
            "closing_balance": summary.closing_balance,
            "total_expense": dynamic_expense,
            "total_income": final_income,
            "total_savings": final_savings,
            "fetched_at": datetime.now().strftime("%d %b %Y %H:%M:%S")
        })

