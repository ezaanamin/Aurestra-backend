"""Auto-split from legacy app.py — transactions controller."""
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

transactions_bp = Blueprint("transactions", __name__)

# -------------------------
# TRANSACTION MANAGEMENT (DELETION, SPAM, CATEGORIZATION)
# -------------------------

@transactions_bp.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
@token_required
def delete_transaction(current_user, txn_id):
    """Permanently marks a transaction as deleted (user-scoped)"""
    try:
        transaction = Transaction.query.filter_by(id=txn_id, user_id=current_user.id).first()
        if not transaction:
            return jsonify({"error": "Transaction not found"}), 404
            
        transaction.is_deleted = True
        transaction.categorization_status = 'deleted'
        db.session.commit()
        
        return jsonify({"success": True, "message": "Transaction deleted permanently"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@transactions_bp.route('/api/transactions/<int:txn_id>/spam', methods=['POST'])
@token_required
def mark_as_spam(current_user, txn_id):
    """Marks a transaction as spam (user-scoped)"""
    try:
        transaction = Transaction.query.filter_by(id=txn_id, user_id=current_user.id).first()
        if not transaction:
            return jsonify({"error": "Transaction not found"}), 404
            
        transaction.is_spam = True
        transaction.categorization_status = 'spam'
        db.session.commit()
        
        return jsonify({"success": True, "message": "Transaction marked as spam"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@transactions_bp.route('/api/transactions/uncategorized', methods=['GET'])
@token_required
def get_uncategorized_transactions(current_user):
    """Fetch transactions that are not yet categorized and not deleted/spam"""
    try:
        transactions = Transaction.query.filter(
            Transaction.user_id == current_user.id,
            Transaction.categorization_status == 'pending',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True)
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify({
            "count": len(transactions),
            "transactions": [txn.to_dict() for txn in transactions]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@transactions_bp.route('/api/transactions/spam', methods=['GET'])
@token_required
def get_spam_transactions(current_user):
    """Fetch transactions marked as spam"""
    try:
        transactions = Transaction.query.filter(
            Transaction.user_id == current_user.id,
            Transaction.is_spam == True,
            Transaction.is_deleted.isnot(True)
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify([txn.to_dict() for txn in transactions]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@transactions_bp.route('/api/transactions/categorized', methods=['GET'])
@token_required
def get_categorized_transactions(current_user):
    """Fetch transactions that are categorized and not deleted/spam"""
    try:
        transactions = Transaction.query.filter(
            Transaction.user_id == current_user.id,
            Transaction.categorization_status != 'pending',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True)
        ).order_by(desc(Transaction.date)).all()
        
        return jsonify([txn.to_dict() for txn in transactions]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

