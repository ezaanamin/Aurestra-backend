"""Auto-split from legacy app.py — reports controller."""
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

reports_bp = Blueprint("reports", __name__)

# -------------------------
# REPORTS ROUTES
# -------------------------
@reports_bp.route("/api/reports/statement", methods=["POST"])
@token_required
def get_statement_report(current_user):
    from model import StatementAnalysis # Fix NameError
    data = request.get_json() or {}
    
    # Input: month "YYYY-MM" (e.g. "2026-01")
    input_month_str = data.get("month")
    
    if not input_month_str:
        # Default to current month if not provided, or handle as "Latest"
        input_month_str = datetime.now().strftime("%Y-%m")
        
    target_month_str = input_month_str
    
    # 1. Try fetching from E-Statement Analysis Table (The new Source of Truth)
    analysis = StatementAnalysis.query.filter_by(month=target_month_str).first()
    
    if analysis:
        # print(f"✅ Found Analysis for {target_month_str} in DB")
        
        # 2. Fetch linked transactions for the table
        try:
            stmt_tx_ids = json.loads(analysis.transaction_ids) if analysis.transaction_ids else []
            from model import Transaction
            linked_txs = Transaction.query.filter(Transaction.id.in_(stmt_tx_ids)).order_by(Transaction.date.desc()).all()
        except:
            linked_txs = []
            
        tx_data = []
        for t in linked_txs:
            tx_data.append({
                "date": t.date.strftime("%d/%m/%Y"),
                "amount": t.amount,
                "description": t.notes or t.sender or "Transaction",
                "type": t.type,
                "status": "existing"
            })
            
        response_data = analysis.to_dict()
        response_data["data"] = tx_data  # Populate 'data' field for the frontend table

        # RESTORED: Backup to Drive (Encrypted)
        if current_user.google_refresh_token:
            try:
                service = get_drive_service(current_user)
                if service:
                    # Ensure "Aurestra Finance/{Month}"
                    folder_id = ensure_folder_path(service, ["Aurestra Finance", target_month_str])
                    if folder_id:
                        upload_json(service, folder_id, "statement.json", response_data)
            except Exception as e:
                print(f"⚠️ Drive Backup Failed: {e}")
        
        return jsonify(response_data)
        
    # print(f"⚠️ No Analysis found for {target_month_str} in DB.")
    
    # Check if user is asking for the *current* month
    # We can't have a final statement for the current running month.
    now_str = datetime.now().strftime("%Y-%m")
    
    if target_month_str == now_str:
        return jsonify({
            "message": f"Statement for {target_month_str} is not finalized yet. Please wait for the month to end."
        }), 404
    else:
        return jsonify({
            "message": f"Analysis for {target_month_str} isn't computed. Please go to Home and click 'Calculate'."
        }), 404
        
    # ---------------------------------------------------------
    # ---------------------------------------------------------



