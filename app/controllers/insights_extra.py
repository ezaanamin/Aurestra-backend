"""Auto-split from legacy app.py — insights_extra controller."""
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
from utils.auth import token_required
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

insights_extra_bp = Blueprint("insights_extra", __name__)

@insights_extra_bp.route("/api/insights/generate", methods=["POST"])
@token_required
def generate_insights(current_user):
    try:
        from datetime import datetime, date, timedelta
        from model import FinancialInsight
        from financial_agent import FinancialAgent
        from fetchers import fetch_latest_bank_email
        
        data = request.get_json() or {}
        month_str = data.get("month") # YYYY-MM
        
        # 1. Sync Data first (User requirement: "open bank statement...")
        print("🔄 Manual Trigger: Syncing Bank Data...")
        try:
            fetch_latest_bank_email()
            # We can also fetch wallet/easypaisa if needed
        except Exception as e:
            print(f"⚠️ Sync failed: {e}")
            # Continue anyway, maybe transactions are already there
            
        # 2. Determine target month
        target_year = None
        target_month = None
        
        if month_str:
            dt = datetime.strptime(month_str, "%Y-%m")
            target_year = dt.year
            target_month = dt.month
        else:
            # Default to PREVIOUS month as per user description ("january looks at december")
            # But allowing "Current Month" might be useful?
            # User said: "calculate the monthly summary for THAT month."
            # If I am in Jan, and I click button, do I want Jan summary (partial) or Dec (complete)?
            # User said: "For example, if it's January, it should look at all of December..."
            # So default = Previous Month.
            today = date.today()
            first = today.replace(day=1)
            prev = first - timedelta(days=1)
            target_year = prev.year
            target_month = prev.month
            
        # 3. Use Financial Agent
        agent = FinancialAgent()
        agent.analyze_month(target_year, target_month)
        
        return jsonify({
            "message": f"Insights generated for {target_year}-{target_month:02d}",
            "month": f"{target_year}-{target_month:02d}"
        }), 200

    except Exception as e:
        print(f"❌ Generation failed: {e}")
        return jsonify({"error": str(e)}), 500

@insights_extra_bp.route("/api/reports/statement/mark-read", methods=["POST"])
@token_required
def mark_statement_as_read(current_user):
    from model import StatementAnalysis, AccountBalance
    data = request.get_json() or {}
    month_str = data.get("month")
    
    if not month_str:
        return jsonify({"error": "Month is required"}), 400
        
    stmt = StatementAnalysis.query.filter_by(month=month_str).first()
    if not stmt:
        return jsonify({"error": "Statement not found"}), 404
    
    # ADDITIVE BALANCE LOGIC (MOVED HERE)
    # Only if NOT already applied
    if not stmt.balance_applied:
        close_bal = stmt.closing_balance
        slug = getattr(stmt, "account_balance_source", None) or None
        if slug:
            account_balance = AccountBalance.query.filter_by(source=slug).first()
        else:
            account_balance = AccountBalance.query.filter_by(source="bank").first()

        if not account_balance and not slug:
            account_balance = AccountBalance(
                source="bank",
                display_name="Bank Account",
                holder_name="",
                account_kind="bank",
                match_keywords=json.dumps(["bank", "hbl", "statement"]),
                accent_color="#A855F7",
                sort_order=0,
                current_balance=close_bal,
                last_updated=datetime.utcnow(),
                is_manual=False,
            )
            db.session.add(account_balance)
            print(f"💰 [mark-read] created default bank row with balance {close_bal}")
        elif not account_balance:
            return jsonify({
                "error": f"No AccountBalance row for source `{slug}`. Check account_balance_source on this statement."
            }), 400

        old_balance = account_balance.current_balance
        account_balance.current_balance = close_bal
        account_balance.last_updated = datetime.utcnow()
        account_balance.is_manual = False
        print(f"💰 [mark-read] SET Balance on `{account_balance.source}`: {old_balance} -> {close_bal}")

        stmt.balance_applied = True

    if not stmt.reviewed_at:
        stmt.reviewed_at = datetime.utcnow()
        print(f"✅ Marked statement {month_str} as READ")
    
    db.session.commit()
    return jsonify({
        "message": "Statement marked as read and balance updated", 
        "balance_applied": stmt.balance_applied,
        "reviewed_at": stmt.reviewed_at.isoformat()
    })


@insights_extra_bp.route("/api/transactions", methods=["POST"])
@token_required
def create_transaction(current_user):
    try:
        data = request.get_json()

        amount = abs(float(data.get("amount", 0)))
        if amount == 0:
            return jsonify({"error": "Amount must be positive"}), 400

        t_type = (data.get("type") or "debit").strip().lower()
        if t_type not in ("debit", "credit"):
            return jsonify({"error": "type must be debit or credit"}), 400

        purpose = data.get("category") or data.get("purpose") or "Uncategorized"
        notes = data.get("notes", "")
        shopping_details = (data.get("shopping_details") or "").strip()
        date_str = data.get("date")

        if str(purpose).strip().lower() == "shopping" and not shopping_details:
            return jsonify({"error": "shopping_details is required for Shopping transactions."}), 400

        slug = (
            (data.get("account_balance_source") or data.get("wallet_slug") or data.get("balance_account_slug") or "")
            .strip()
            .lower()
        )
        if not slug:
            return jsonify(
                {"error": "account_balance_source is required (wallet slug, e.g. bank, easypaisa, cash)"}
            ), 400

        tx_date = datetime.utcnow()
        if date_str:
            try:
                tx_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except Exception:
                pass

        user_id = getattr(current_user, 'id', None)
        if user_id is None and isinstance(current_user, dict):
            user_id = current_user.get('id')
        if user_id is None:
            first_u = User.query.first()
            if first_u:
                user_id = first_u.id

        new_tx = Transaction(
            user_id=user_id,
            source="manual",
            date=tx_date,
            amount=amount,
            type=t_type,
            purpose=purpose,
            sender="Manual Entry",
            receiver="Me" if t_type == "credit" else "Merchant",
            notes=notes,
            shopping_details=shopping_details if str(purpose).strip().lower() == "shopping" else None,
            categorization_status="confirmed",
            account_balance_source=slug,
            balance_applied=False,
        )

        db.session.add(new_tx)
        db.session.flush()

        ensure_account_balance_row(slug, user_id=user_id)
        try:
            log_wallet_attribution(
                "MANUAL_TXN_CREATED",
                transaction_id=new_tx.id,
                resolved_wallet_slug=slug,
                txn_type=t_type,
                amount=amount,
                purpose=purpose,
                note="Manual entry: ledger apply next (credit +, debit − on selected wallet)",
            )
        except Exception:
            pass

        apply_pending_transaction_ledger(new_tx, respect_manual_lock=False)

        db.session.commit()

        accounts = [
            acc.to_dict()
            for acc in AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
        ]

        return jsonify(
            {
                "message": "Transaction added successfully",
                "transaction": new_tx.to_dict(),
                "accounts": accounts,
            }
        ), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Transaction creation error: {e}")
        return jsonify({"error": str(e)}), 500

