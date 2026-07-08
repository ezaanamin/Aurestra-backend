"""Auto-split from legacy app.py — expenses controller."""
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

expenses_bp = Blueprint("expenses", __name__)

# -------------------------
# EXPENSE ROUTES
# -------------------------

def calculate_month_expenses(year, month, user_id=None):
    """
    Running-balance expense calculation (date-ordered), scoped to a single user.
    """
    q = Transaction.query.filter(
        extract('year',  Transaction.date) == year,
        extract('month', Transaction.date) == month,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    )
    if user_id is not None:
        q = q.filter(Transaction.user_id == user_id)
    transactions = q.order_by(Transaction.date.asc()).all()

    running = 0.0
    for txn in transactions:
        if is_own_account_transfer_row(txn):
            continue
        if txn.type == 'debit':
            running += txn.amount
        elif txn.type == 'credit':
            running = max(0.0, running - txn.amount)

    return running




@expenses_bp.route("/api/expenses/total", methods=["GET"])
@token_required
def get_total_expenses(current_user):
    try:
        dt = datetime.now()
        year, month = dt.year, dt.month
        month_str = dt.strftime("%Y-%m")

        # --- Calculate from scratch for THIS user ---
        total_expenses = calculate_month_expenses(year, month, user_id=current_user.id)

        # Also expose raw totals for debugging / other screens
        total_debits = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year',  Transaction.date) == year,
            extract('month', Transaction.date) == month,
            Transaction.type        == 'debit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            Transaction.user_id     == current_user.id,
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        total_credits = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year',  Transaction.date) == year,
            extract('month', Transaction.date) == month,
            Transaction.type        == 'credit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            Transaction.user_id     == current_user.id,
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        # --- Persist to Budget.total_expenses for THIS user ---
        budget_entry = Budget.query.filter_by(user_id=current_user.id, month=month_str).first()
        if budget_entry:
            budget_entry.total_expenses = total_expenses
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"⚠️  Could not persist total_expenses to Budget: {e}")

        print(f"💰 [{month_str}] debits={total_debits:.2f}  credits={total_credits:.2f}  total_expenses={total_expenses:.2f}")

        return jsonify({
            "month":          month_str,
            "total_expense":  total_expenses,   # net (debits − credits, min 0)
            "total_debits":   total_debits,
            "total_credits":  total_credits,
        }), 200

    except Exception as e:
        print(f"❌ Failed to get total expenses: {e}")
        return jsonify({"error": str(e)}), 500



