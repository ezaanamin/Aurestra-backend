"""Auto-split from legacy app.py — health controller."""
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

health_bp = Blueprint("health", __name__)

# -------------------------
# HEALTH CHECK ROUTES
# -------------------------
@health_bp.route('/api/health', methods=['GET'])
def health_check():
    """Simple backend liveness probe"""
    return jsonify({
        "status": "online",
        "message": "Backend is running",
        "timestamp": datetime.now().isoformat()
    }), 200

@health_bp.route('/api/health/db', methods=['GET'])
def db_health_check():
    """Deep database connectivity and schema check"""
    try:
        from sqlalchemy import inspect
        
        # 1. Check Connection
        db.session.execute(func.now()).scalar()
        
        # 2. Check Tables
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        
        required_tables = ['users', 'transactions', 'categories', 'budgets']
        missing_tables = [t for t in required_tables if t not in existing_tables]
        
        status = "healthy" if not missing_tables else "degraded"
        
        return jsonify({
            "status": status,
            "database": "connected",
            "tables_found": len(existing_tables),
            "missing_required_tables": missing_tables,
            "all_tables": existing_tables
        }), 200 if status == "healthy" else 503
        
    except Exception as e:
        print(f"❌ DB Health Check Failed: {e}")
        return jsonify({
            "status": "offline",
            "error": str(e)
        }), 500

