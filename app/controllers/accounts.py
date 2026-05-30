"""Auto-split from legacy app.py — accounts controller."""
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

accounts_bp = Blueprint("accounts", __name__)

@accounts_bp.route("/api/bank/latest")
@token_required
def bank_latest(current_user):
    data = fetch_latest_bank_email()
    return jsonify(data)

# Static Config for Uploads
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
current_app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@accounts_bp.route('/static/uploads/<filename>')
def serve_upload(filename):
    from flask import send_from_directory
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

@accounts_bp.route("/api/upload/avatar", methods=["POST"])
@token_required
def upload_avatar(current_user):
    if 'avatar' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['avatar']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        timestamp = int(time())
        filename = secure_filename(f"user_{current_user.id}_{timestamp}_{file.filename}")
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Build URL (Relative for now)
        avatar_url = f"/static/uploads/{filename}"
        
        current_user.avatar_url = avatar_url
        db.session.commit()
        
        return jsonify({'message': 'File uploaded', 'avatar_url': avatar_url}), 200

@accounts_bp.route("/api/wallet/latest")
@token_required
def wallet_latest(current_user):
    data = fetch_latest_wallet_email()
    return jsonify(data)


@accounts_bp.route("/api/accounts/set_balance", methods=["POST"])
@token_required
def set_manual_balance(current_user):
    """
    Manually set the account balance.
    This sets is_manual=True, preventing older statements from overwriting it.
    Pass account_id (preferred) or source slug.
    """
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        account_id = data.get("account_id")
        if account_id is not None:
            balance = AccountBalance.query.get(int(account_id))
            if not balance:
                return jsonify({"error": "Account not found"}), 404
            source = balance.source
        else:
            source = data.get('source', 'bank')
            balance = AccountBalance.query.filter_by(source=source).first()

        if not balance:
            dn = (source or "bank").replace("_", " ").title()
            balance = AccountBalance(
                source=source or "bank",
                display_name=dn,
                holder_name="",
                account_kind="bank",
                match_keywords=json.dumps([source or "bank"]),
                accent_color="#6366F1",
                sort_order=(db.session.query(func.max(AccountBalance.sort_order)).scalar() or 0) + 1,
                current_balance=amount,
            )
            db.session.add(balance)
        
        # Update Balance and set Manual Flag
        balance.current_balance = amount
        balance.is_manual = True
        balance.last_updated = datetime.now()
        
        db.session.commit()
        
        # FIX: Fetch ALL accounts to return complete updated state
        all_accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
        
        return jsonify({
            "message": "Balance updated manually",
            "account": balance.to_dict(),
            "accounts": [acc.to_dict() for acc in all_accounts]  # Return all
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def ensure_default_cash_wallet():
    """Ensure the reserved physical-cash wallet exists (source=cash). Idempotent."""
    if AccountBalance.query.filter_by(source="cash").first():
        return
    acc = AccountBalance(
        source="cash",
        display_name="Cash",
        holder_name="",
        account_kind="cash",
        match_keywords=json.dumps(["cash"]),
        accent_color="#22C55E",
        sort_order=-1000,
        current_balance=0.0,
        last_updated=datetime.now(),
        is_manual=False,
    )
    db.session.add(acc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()


@accounts_bp.route("/api/accounts", methods=["GET", "POST"])
@token_required
def get_accounts(current_user):
    if request.method == "POST":
        try:
            data = request.get_json() or {}
            display_name = (data.get("display_name") or "").strip()
            if not display_name:
                return jsonify({"error": "display_name is required"}), 400
            holder_name = (data.get("holder_name") or data.get("account_holder_name") or "").strip()
            account_kind = (data.get("account_kind") or "bank").strip()
            if account_kind not in ("bank", "mobile_wallet", "cash", "digital_bank"):
                account_kind = "bank"
            if account_kind == "cash":
                return jsonify({"error": "Cash is included automatically. Edit it from home."}), 400
            raw_slug = (data.get("slug") or "").strip().lower()
            base = raw_slug or re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")[:48]
            if not base:
                base = "wallet"
            slug = base
            n = 2
            while AccountBalance.query.filter_by(source=slug).first():
                slug = f"{base}_{n}"
                n += 1
            if slug == "cash":
                return jsonify({"error": "That name is reserved for built-in Cash."}), 400
            kws = data.get("match_keywords")
            if isinstance(kws, str):
                kws = [x.strip() for x in kws.split(",") if x.strip()]
            elif isinstance(kws, list):
                kws = [str(x).strip() for x in kws if str(x).strip()]
            else:
                kws = [display_name]
            nums = data.get("statement_account_numbers")
            stmt_nums = None
            if isinstance(nums, list) and nums:
                stmt_nums = json.dumps([str(x).strip() for x in nums if str(x).strip()])
            elif isinstance(nums, str) and nums.strip():
                stmt_nums = nums.strip()
            accent = (data.get("accent_color") or "#6366F1").strip()
            initial = float(data.get("initial_balance", 0) or 0)
            max_ord = db.session.query(func.max(AccountBalance.sort_order)).scalar()
            max_ord = int(max_ord) if max_ord is not None else 0
            acc = AccountBalance(
                source=slug,
                display_name=display_name,
                holder_name=holder_name,
                account_kind=account_kind,
                match_keywords=json.dumps(kws),
                statement_account_numbers=stmt_nums,
                accent_color=accent,
                sort_order=max_ord + 1,
                current_balance=initial,
                last_updated=datetime.now(),
                is_manual=bool(initial),
            )
            db.session.add(acc)
            db.session.commit()
            return jsonify({"message": "Account created", "account": acc.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500

    with current_app.app_context():
        ensure_default_cash_wallet()
        # Statement email sync must NOT run on every GET: the app calls GET /api/accounts after
        # categorize (fetchUserAccounts), and this block was overwriting bank.current_balance from
        # stale email "closing_balance", undoing per-wallet ledger updates while transaction-based
        # income/expense still changed — opt in with ?sync_statement=1 or ?sync_bank_email=1.
        sync_statement = (
            request.args.get("sync_statement", type=int) == 1
            or request.args.get("sync_bank_email", type=int) == 1
        )
        if sync_statement:
            # 1. Start with fresh statement balance if possible
            try:
                bank_data = fetch_latest_bank_email()
                if "balances" in bank_data:
                    closing_bal = bank_data["balances"].get("closing_balance", 0.0)
                    email_date = bank_data.get("date") # Assuming fetch_latest_bank_email returns a datetime object
                    
                    bank_acc = AccountBalance.query.filter_by(source="bank").first()
                    if not bank_acc:
                        bank_acc = (
                            AccountBalance.query.filter_by(account_kind="bank")
                            .order_by(AccountBalance.sort_order, AccountBalance.id)
                            .first()
                        )
                    if bank_acc:
                        # CHECK MANUAL OVERRIDE & TIMESTAMP Logic
                        should_update = True
                        
                        if bank_acc.is_manual:
                            # LOCKED: Do not update if manual
                            should_update = False
                        
                        # Also check TIMESTAMP: Only update if email is NEWER than last_updated
                        # This prevents stale transaction emails from overwriting fresh statement data
                        if email_date and bank_acc.last_updated:
                            if email_date <= bank_acc.last_updated:
                                print(f"⏭️  Stale email balance ({email_date}) ignored. Current balance is newer ({bank_acc.last_updated})")
                                should_update = False
                        
                        if should_update:
                            print(f"🔄 Updating Account Balance from Email: {closing_bal}")
                            bank_acc.current_balance = closing_bal
                            bank_acc.last_updated = datetime.now()
                            bank_acc.is_manual = False  # Email update removes manual lock
                        
                        # SAVE TRANSACTIONS (Always process transactions, just don't overwrite balance if manual)
                        extracted_txs = bank_data.get("transactions", [])
                        new_tx_count = 0
                        for tx in extracted_txs:
                            try:
                                tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
                            except:
                                tx_date = datetime.now()

                            # New Deduplication Logic:
                            # 1. Search for ANY transaction with same Amount & Type within +/- 2 days.
                            candidates = Transaction.query.filter(
                                Transaction.amount == tx["amount"],
                                Transaction.type == tx["type"],
                                Transaction.date >= tx_date - timedelta(days=2),
                                Transaction.date <= tx_date + timedelta(days=2)
                            ).all()
                            
                            # 🎯 ROBUST DEDUPLICATION using Hashing
                            tx_hash = Transaction.generate_deterministic_hash({
                                "date": tx_date,
                                "amount": tx["amount"],
                                "type": tx["type"],
                                "description": tx["description"]
                            })
                            
                            # Check existance by HASH (idempotent)
                            exists = Transaction.query.filter_by(transaction_hash=tx_hash).first()
                            
                            if not exists:
                                # 🛡️ FALLBACK: Check for 'Similar' transaction (e.g. from SMS)
                                # Checking +/- 2 days to account for statement vs SMS date differences
                                exists = Transaction.query.filter(
                                    Transaction.amount == tx["amount"],
                                    Transaction.type == tx["type"],
                                    Transaction.date >= tx_date - timedelta(days=2),
                                    Transaction.date <= tx_date + timedelta(days=2)
                                ).first()
                                if exists:
                                    print(f"🔗 Similar transaction found (SMS overlap?): {tx_date.date()} | {tx['amount']}")
                                    # Update existing transaction with the statement hash if missing
                                    if not exists.transaction_hash:
                                        exists.transaction_hash = tx_hash

                            if not exists:
                                desc_txt = (tx.get("description") or "")[:250]
                                try:
                                    new_tx = Transaction(
                                        source="bank",
                                        date=tx_date,
                                        amount=tx["amount"],
                                        type=tx["type"],
                                        purpose="Uncategorized",
                                        sender="Bank Statement",
                                        receiver="Me",
                                        notes=desc_txt,
                                        transaction_hash=tx_hash,
                                    )
                                    # Nested transaction so a duplicate hash does not abort the whole GET /accounts session.
                                    with db.session.begin_nested():
                                        db.session.add(new_tx)
                                        db.session.flush()
                                    new_tx_count += 1
                                    print(f"✅ Added new transaction: {tx_hash[:10]}...")
                                except IntegrityError:
                                    print(f"⏭️  Skipped duplicate transaction hash: {tx_hash[:10]}...")
                                    continue
                        
                        if new_tx_count > 0:
                            try:
                                 send_push_to_all(
                                     title="New Bank Transactions",
                                     body=f"Found {new_tx_count} new transaction(s) from your bank statement."
                                 )
                            except Exception as e:
                                 print(f"⚠️ Push failed in get_accounts: {e}")

                        db.session.commit()
            except Exception as sync_err:
                print(f"⚠️ get_accounts bank email sync skipped: {sync_err}")
                db.session.rollback()

        # 2. Fetch all accounts (ordered for dashboard)
        accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()

        response_data = []
        for acc in accounts:
            acc_dict = acc.to_dict()
            acc_dict["statement_base"] = acc.current_balance
            acc_dict["live_adjustment"] = 0.0
            acc_dict["savings_reduction"] = 0.0
            response_data.append(acc_dict)

        return jsonify(response_data)


@accounts_bp.route("/api/accounts/<int:account_id>", methods=["PUT", "DELETE"])
@token_required
def manage_single_account(current_user, account_id):
    acc = AccountBalance.query.get(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    if request.method == "DELETE":
        if acc.source == "cash":
            return jsonify({"error": "The Cash wallet cannot be deleted."}), 400
        try:
            db.session.delete(acc)
            db.session.commit()
            return jsonify({"message": "Deleted"}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 500
    if acc.source == "cash":
        return jsonify({"error": "Cash only supports balance updates from the app."}), 400
    try:
        data = request.get_json() or {}
        if "display_name" in data:
            acc.display_name = (data.get("display_name") or acc.display_name).strip()
        if "holder_name" in data or "account_holder_name" in data:
            hn = data.get("holder_name") if "holder_name" in data else data.get("account_holder_name")
            acc.holder_name = (hn or "").strip()
        if "account_kind" in data:
            ak = (data.get("account_kind") or "").strip()
            if ak == "cash" and acc.source != "cash":
                return jsonify({"error": "Use the built-in Cash wallet for physical cash."}), 400
            if ak in ("bank", "mobile_wallet", "cash", "digital_bank"):
                acc.account_kind = ak
        if "match_keywords" in data:
            kws = data.get("match_keywords")
            if isinstance(kws, str):
                kws = [x.strip() for x in kws.split(",") if x.strip()]
            if isinstance(kws, list):
                acc.match_keywords = json.dumps([str(x).strip() for x in kws if str(x).strip()])
        if "statement_account_numbers" in data:
            nums = data.get("statement_account_numbers")
            if nums is None or nums == []:
                acc.statement_account_numbers = None
            elif isinstance(nums, list):
                acc.statement_account_numbers = json.dumps(
                    [str(x).strip() for x in nums if str(x).strip()]
                )
            elif isinstance(nums, str) and nums.strip():
                acc.statement_account_numbers = nums.strip()
        if "accent_color" in data:
            acc.accent_color = (data.get("accent_color") or acc.accent_color).strip()
        if "sort_order" in data:
            acc.sort_order = int(data.get("sort_order") or 0)
        acc.last_updated = datetime.now()
        db.session.commit()
        return jsonify({"account": acc.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/match", methods=["POST"])
@token_required
def match_notification_account(current_user):
    """Pick which wallet/account a push notification likely belongs to (keyword + title matching)."""
    data = request.get_json() or {}
    accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
    m = match_account_for_notification(
        accounts,
        data.get("title") or "",
        data.get("text") or "",
        data.get("packageName") or data.get("package_name") or "",
    )
    if not m:
        return jsonify({"match": None}), 200
    return jsonify({"match": m.to_dict()}), 200


@accounts_bp.route("/api/savings-goals", methods=["GET", "POST"])
@token_required
def manage_savings_goals(current_user):
    if request.method == "GET":
        goals = SavingsGoal.query.all()
        return jsonify([g.to_dict() for g in goals])
    
    if request.method == "POST":
        data = request.get_json()
        name = data.get("name")
        target = float(data.get("target_amount", 0))
        current = float(data.get("current_amount", 0))
        emoji = data.get("emoji", "💰")
        deadline_str = data.get("deadline") # YYYY-MM-DD
        
        deadline_date = None
        if deadline_str:
            try:
                deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            except:
                pass
        
        new_goal = SavingsGoal(
            name=name,
            target_amount=target,
            current_amount=current,
            emoji=emoji,
            deadline=deadline_date
        )
        db.session.add(new_goal)
        db.session.commit()
        
        return jsonify(new_goal.to_dict()), 201

@accounts_bp.route("/api/savings-goals/<int:id>", methods=["PUT"])
@token_required
def update_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        data = request.get_json()
        if "name" in data:
            goal.name = data["name"]
        if "target_amount" in data:
            goal.target_amount = float(data["target_amount"])
        if "current_amount" in data:
            goal.current_amount = float(data["current_amount"])
        if "emoji" in data:
            goal.emoji = data["emoji"]
        if "deadline" in data:
            try:
                goal.deadline = datetime.strptime(data["deadline"], "%Y-%m-%d").date()
            except:
                pass
                
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@accounts_bp.route("/api/savings-goals/<int:id>", methods=["DELETE"])
@token_required
def delete_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        # REFUND LOGIC: Check if money was allocated
        if goal.current_amount > 0:
            refund_amount = goal.current_amount
            
            # Find primary bank account to refund to
            bank_acc = AccountBalance.query.filter_by(source='bank').first()
            if not bank_acc:
                bank_acc = AccountBalance.query.first()
                
            if bank_acc:
                bank_acc.current_balance += refund_amount
                bank_acc.last_updated = datetime.utcnow()
                
                # Create Refund Transaction
                refund_tx = Transaction(
                    source='bank',
                    date=datetime.utcnow(),
                    amount=refund_amount,
                    type='credit',
                    purpose='Savings Refund',
                    sender='Savings Goal',
                    receiver='Me',
                    notes=f"Refund from deleted goal: {goal.name}"
                )
                db.session.add(refund_tx)
            
        db.session.delete(goal)
        db.session.commit()
        return jsonify({"message": "Goal deleted and funds refunded"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@accounts_bp.route("/api/savings-goals/<int:id>/contribute", methods=["POST"])
@token_required
def contribute_to_savings_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
            
        data = request.get_json()
        amount = float(data.get("amount", 0))
        
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than zero"}), 400
            
        # TRANSACTION Logic: Deduct from Bank Account
        # TRANSACTION Logic: Deduct from Bank Account
        # Try to find a 'bank' account first, otherwise pick the first available one (e.g. Cash)
        bank_acc = AccountBalance.query.filter_by(source='bank').first()
        if not bank_acc:
             bank_acc = AccountBalance.query.first()
             
        if not bank_acc:
            return jsonify({"error": "No account found to fund savings. Please create an account first."}), 400
            
        # Optional: Check Strict Balance?
        # if bank_acc.current_balance < amount:
        #    return jsonify({"error": "Insufficient funds in bank account"}), 400
             
        # 1. Deduct from Balance
        bank_acc.current_balance -= amount
        bank_acc.last_updated = datetime.utcnow()
        
        # 2. Create Debit Transaction
        contrib_tx = Transaction(
            source='bank',
            date=datetime.utcnow(),
            amount=amount,
            type='debit',
            purpose='Savings',
            sender='Me',
            receiver='Savings Goal',
            notes=f"Contribution to: {goal.name}"
        )
        db.session.add(contrib_tx)
        
        # 3. Update Goal
        goal.current_amount += amount
        
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
