"""Auto-split from legacy app.py — analytics controller."""
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

analytics_bp = Blueprint("analytics", __name__)

# -------------------------
# TRANSACTION ROUTES
# -------------------------
@analytics_bp.route('/api/analytics/trend', methods=['GET'])
def get_analytics_trend():
    period = request.args.get('period', default='month')
    
    data_points = []
    
    if period == 'week':
        # Last 7 days
        for i in range(6, -1, -1):
            target_date = datetime.now().date() - timedelta(days=i)
            total = (
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    Transaction.type == 'debit',
                    func.date(Transaction.date) == target_date,
                    Transaction.is_deleted.isnot(True),
                    Transaction.is_spam.isnot(True),
                    Transaction.categorization_status != 'pending',
                    exclude_own_account_transfer_sql(),
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": target_date.strftime("%a"), # Mon, Tue...
                "value": float(total)
            })
            
    elif period == 'month':
        # Last 6 months (default behavior for trend)
        for i in range(5, -1, -1):
            target_date = datetime.now() - relativedelta(months=i)
            month_str = target_date.strftime("%Y-%m")
            
            total = (
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    Transaction.type == 'debit',
                    extract('year', Transaction.date) == target_date.year,
                    extract('month', Transaction.date) == target_date.month,
                    Transaction.is_deleted.isnot(True),
                    Transaction.is_spam.isnot(True),
                    Transaction.categorization_status != 'pending',
                    exclude_own_account_transfer_sql(),
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": target_date.strftime("%b"), # Jan, Feb...
                "value": float(total)
            })
            
    elif period == 'year':
        # Current year months
        for i in range(1, 13):
            year = datetime.now().year
            total = (
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    Transaction.type == 'debit',
                    extract('year', Transaction.date) == year,
                    extract('month', Transaction.date) == i,
                    Transaction.is_deleted.isnot(True),
                    Transaction.is_spam.isnot(True),
                    Transaction.categorization_status != 'pending',
                    exclude_own_account_transfer_sql(),
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": date(year, i, 1).strftime("%b"),
                "value": float(total)
            })
            
    elif period == 'all':
        # Last 5 years
        for i in range(4, -1, -1):
            year = datetime.now().year - i
            total = (
                db.session.query(func.sum(Transaction.amount))
                .filter(
                    Transaction.type == 'debit',
                    extract('year', Transaction.date) == year,
                    Transaction.is_deleted.isnot(True),
                    Transaction.is_spam.isnot(True),
                    Transaction.categorization_status != 'pending',
                    exclude_own_account_transfer_sql(),
                )
                .scalar() or 0.0
            )
            data_points.append({
                "label": str(year),
                "value": float(total)
            })
            
    return jsonify(data_points), 200

def _get_sum_for_range(start, end, txn_type):
    query = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == txn_type,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    )
    if start: query = query.filter(Transaction.date >= start)
    if end: query = query.filter(Transaction.date <= end)
    return float(query.scalar() or 0.0)

@analytics_bp.route('/api/analytics/summary', methods=['GET'])
@token_required
def get_analytics_summary(current_user):
    period = request.args.get('period', default='month')
    now = datetime.now()

    if period == 'week':
        curr_start = (now - timedelta(days=7)).date()
        curr_end = now.date()
        prev_start = (now - timedelta(days=14)).date()
        prev_end = (now - timedelta(days=7)).date()
    elif period == 'month':
        curr_start = date(now.year, now.month, 1)
        curr_end = now.date()
        prev_start = (now - relativedelta(months=1)).replace(day=1).date()
        prev_end = curr_start - timedelta(days=1)
    elif period == 'year':
        curr_start = date(now.year, 1, 1)
        curr_end = now.date()
        prev_start = date(now.year - 1, 1, 1)
        prev_end = date(now.year - 1, 12, 31)
    else: # all
        curr_start = None
        curr_end = None
        prev_start = None
        prev_end = None

    curr_exp = _get_sum_for_range(curr_start, curr_end, 'debit')
    prev_exp = _get_sum_for_range(prev_start, prev_end, 'debit')
    curr_inc = _get_sum_for_range(curr_start, curr_end, 'credit')
    prev_inc = _get_sum_for_range(prev_start, prev_end, 'credit')

    return jsonify({
        "currentExpense": curr_exp,
        "prevExpense": prev_exp,
        "currentIncome": curr_inc,
        "prevIncome": prev_inc
    }), 200

@analytics_bp.route('/api/latest-transactions', methods=['GET'])
def latest_transactions():
    try:
        limit = request.args.get('limit', default=4, type=int)
        transactions = (
            db.session.query(Transaction)
            .filter(
                Transaction.is_deleted.isnot(True),
                Transaction.is_spam.isnot(True)
            )
            .order_by(desc(Transaction.date))
            .limit(limit)
            .all()
        )

        result = [txn.to_dict() for txn in transactions]

        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@analytics_bp.route('/api/transactions/top-categories', methods=['GET'])
def top_spending_categories():
    period = request.args.get('period', default='month')

    query = db.session.query(
        Transaction.purpose.label("category"),
        func.sum(Transaction.amount).label("total_spent")
    ).filter(
        Transaction.type == 'debit',  # ONLY debits, never credits
        Transaction.purpose.isnot(None),
        Transaction.purpose != 'Uncategorized',
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
        Transaction.categorization_status != 'pending',
        exclude_own_account_transfer_sql(),
    )

    if period == 'week':
        start_date = datetime.now() - timedelta(days=7)
        query = query.filter(Transaction.date >= start_date)
    elif period == 'month':
        dt = datetime.now()
        query = query.filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month
        )
    elif period == 'year':
        query = query.filter(
            extract('year', Transaction.date) == datetime.now().year
        )

    categories = (
        query.group_by(Transaction.purpose)
        .having(func.sum(Transaction.amount) > 0)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(10)
        .all()
    )

    result = [
        {"category": cat.category, "total_spent": cat.total_spent}
        for cat in categories
    ]
    return jsonify(result), 200

