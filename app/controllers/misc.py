"""Auto-split from legacy app.py — misc controller."""
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

misc_bp = Blueprint("misc", __name__)

@misc_bp.route('/api/set_salary', methods=['POST'])
@token_required
def set_salary(current_user):
    try:
        data = request.json
        amount = float(data.get("amount", 0))
        # Default to current month if not provided
        month_str = data.get("month", datetime.now().strftime("%Y-%m"))
        
        budget = Budget.query.filter_by(month=month_str).first()
        if not budget:
             budget = Budget(month=month_str, total_budget=amount)
             db.session.add(budget)
        else:
             budget.total_budget = amount

        # Auto-Calculate 50/30/20 Rule
        needs = amount * 0.50
        wants = amount * 0.30
        savings = amount * 0.20
        
        budget.needs = needs
        budget.wants = wants
        budget.saving = savings
        
        # User Feedback: Budget should be Spending Limit (Needs + Wants)
        # Not the total salary.
        budget.total_budget = needs + wants
            
        db.session.commit()
        return jsonify({
            "message": "Salary updated successfully. Budget set to Spending Limit (Needs + Wants).", 
            "salary": amount, 
            "month": month_str,
            "breakdown": {
                "needs": budget.needs,
                "wants": budget.wants,
                "saving": budget.saving,
                "spending_limit": budget.total_budget
            }
        })

    except Exception as e:
        print(f"❌ Error in set_salary: {e}")
        return jsonify({"error": str(e)}), 500


@misc_bp.route('/api/insights', methods=['GET'])
@token_required
def get_insights(current_user):
    """
    Returns the documentation for the exposed APIs for the AI Agent.
    """
    try:
        endpoints = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint.startswith("ai_agent."):
                func = app.view_functions[rule.endpoint]
                docstring = func.__doc__ or "No description available."
                
                # Dynamically generate real sample data by executing the underlying function
                sample_data = None
                if str(rule) == "/api/agent/exposed-endpoints":
                    sample_data = {"message": "Self-referencing docs endpoint."}
                else:
                    try:
                        # Bypass auth decorators to get the raw endpoint function
                        original_func = getattr(func, '__wrapped__', func)
                        
                        # Execute it! (It will use the current request Context's args)
                        res = original_func()
                        
                        if isinstance(res, tuple):
                            res = res[0]
                            
                        if hasattr(res, 'get_json'):
                            sample_data = res.get_json()
                        elif hasattr(res, 'json'):
                            sample_data = res.json
                    except Exception as e:
                        sample_data = {"error": f"Requires specific query parameters or payload. Detail: {str(e)}"}
                
                endpoints.append({
                    "id": str(rule),  # used as key
                    "endpoint": str(rule),
                    "methods": [m for m in rule.methods if m not in ("HEAD", "OPTIONS")],
                    "description": docstring.strip(),
                    "sample_response": sample_data
                })
                
        return jsonify(endpoints), 200

    except Exception as e:
        print(f"❌ Error fetching insights/apis: {e}")
        return jsonify({"error": str(e)}), 500


@misc_bp.route("/api/reports/statement/calculate", methods=["POST"])
@token_required
def calculate_statement(current_user):
    from fetchers import fetch_previous_month_statement
    from model import User, Transaction, MonthlyBalance, Budget, Category, DeviceToken, FinancialInsight, StatementAnalysis
    from financial_agent import FinancialAgent
    import json
    data = request.get_json() or {}
    month_param = data.get("month")  # Expected format YYYY-MM
    print("🚀 Triggering Statement Calculation with month:", month_param)
    try:
        # If month provided, adjust date logic
        reference_date = None
        
        if month_param:
            try:
                target_dt = datetime.strptime(month_param, "%Y-%m")
                target_year = target_dt.year
                target_month = target_dt.month
                month_str = month_param
                
                # To fetch statement for target_month (Nov), we need to pretend we are in target_month + 1 (Dec)
                # fetch_previous_month_statement looks for PREVIOUS month relative to input.
                # So we pass a date in the *next* month.
                next_month = target_dt + relativedelta(months=1)
                reference_date = next_month.replace(day=15) # Pick mid-month to be safe
                
            except Exception as e:
                return jsonify({"error": f"Invalid month format: {e}"}), 400
        else:
            # Default to previous month
            today = datetime.now()
            first_of_this = today.replace(day=1)
            prev_month_dt = first_of_this - timedelta(days=1)
            target_year = prev_month_dt.year
            target_month = prev_month_dt.month
            month_str = prev_month_dt.strftime("%Y-%m")
            reference_date = today # Use today so it fetches previous month

        # ---------------------------------------------------------
        # OPTIMIZED FLOW: Check if READ in Database
        # ---------------------------------------------------------
        force_refresh = request.args.get('force', 'false').lower() == 'true'
        existing_analysis = StatementAnalysis.query.filter_by(month=month_str).first()
        is_read = existing_analysis and existing_analysis.reviewed_at
        
        if is_read and not force_refresh:
            print(f"⚡ Loading READ Statement for {month_str} from Database (Cached)...")
            
            # Fetch EXACT transactions linked to this statement
            try:
                stmt_tx_ids = json.loads(existing_analysis.transaction_ids) if existing_analysis.transaction_ids else []
                cached_txs = Transaction.query.filter(Transaction.id.in_(stmt_tx_ids)).order_by(Transaction.date.desc()).all()
            except:
                cached_txs = []
            
            tx_list_response = []
            for t in cached_txs:
                tx_list_response.append({
                    "date": t.date.strftime("%d/%m/%Y"),
                    "amount": t.amount,
                    "description": t.notes or t.sender or "Transaction",
                    "type": t.type,
                    "status": "existing"
                })
                
            return jsonify({
                "message": "Statement loaded from cache (Read).",
                "cached": True,
                "month": month_str,
                "balances": {
                    "opening": existing_analysis.opening_balance,
                    "closing": existing_analysis.closing_balance
                },
                "data": tx_list_response,
                "stats": {
                    "added": 0,
                    "skipped": len(cached_txs),
                    "total": len(cached_txs)
                },
                "processing_status": existing_analysis.processing_status,
                "read_status": "read",
                "balance_matches": True
            }), 200

        # ELSE: UNREAD or NOT EXISTS - Proceed to Email Fetching
        print(f"📧 Fetching UNREAD or NEW statement for {month_str} from Gmail API...")

        # Get manual selections if any
        user_selected_balance = data.get("user_selected_balance")
        is_confirmed = data.get("confirmed", False)

        # Fetch statement PDF/Data (Fresh with GMAIL API)
        result = fetch_previous_month_statement(current_user, reference_date=reference_date)
        if "error" in result:
             print(f"⚠️ Statement Fetch Error: {result.get('error')}")
             return jsonify({
                 "error": result.get("error"),
                 "message": "Could not find bank statement in Gmail via API.",
                 "month": month_str
             }), 400
            
        # 🛡️ MANUAL SELECTION CHECK
        # If we have a balance table and user hasn't confirmed yet, return it for selection
        if not is_confirmed and ("all_email_data" in result):
            print(f"🕵️  Manual Selection Required for {month_str}")
            return jsonify({
                "message": "Multiple balances found or manual verification required.",
                "requires_selection": True,
                "month": month_str,
                "all_email_data": result.get("all_email_data", []),
                "suggested_balances": result.get("balances", {})
            }), 200

        extracted_txs = result.get("transactions", [])
        balances = result.get("balances", {})
        
        # Override with user selection if provided
        if is_confirmed and user_selected_balance is not None:
            print(f"🎯 Using USER SELECTED Closing Balance: {user_selected_balance}")
            balances["closing_balance"] = float(user_selected_balance)
            
        added_count = 0
        skipped_count = 0
        added_tx_data = []
        stmt_transaction_ids = []
        for tx in extracted_txs:
            try:
                tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
            except:
                continue
            
            clean_desc = re.sub(r'^\d{2}\/\d{2}\/\d{4}\s*', '', tx["description"]).strip()
            
            # 🎯 DETEERMINISTIC HASHING for deduplication
            tx_hash = Transaction.generate_deterministic_hash({
                "date": tx_date,
                "amount": tx["amount"],
                "type": tx["type"],
                "description": tx["description"]
            })
            
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
                    # Update the existing transaction with the statement's better description/hash if missing
                    if not exists.transaction_hash:
                        exists.transaction_hash = tx_hash
                    # If it was from SMS, it might have source='bank_sms'.
                    # We keep that but link it to the statement by hash.
            
            if exists:
                print(f"⚠️ Duplicate Found: {tx_hash[:10]}...")
                status = "skipped"
                skipped_count += 1
                stmt_transaction_ids.append(exists.id)
            else:
                try:
                    new_tx = Transaction(
                        source="bank_statement",
                        date=tx_date,
                        amount=tx["amount"],
                        type=tx["type"],
                        purpose="Uncategorized",
                        sender="Bank Statement",
                        receiver="Me",
                        notes=clean_desc[:250],
                        transaction_hash=tx_hash
                    )
                    db.session.add(new_tx)
                    db.session.flush() # Get ID
                    status = "added"
                    added_count += 1
                    stmt_transaction_ids.append(new_tx.id)
                except Exception as e:
                    db.session.rollback()
                    print(f"⏭️  Parallel duplicate prevented: {tx_hash[:10]}...")
                    # Find the one that was just inserted by the other thread
                    existing_race = Transaction.query.filter_by(transaction_hash=tx_hash).first()
                    if existing_race:
                        stmt_transaction_ids.append(existing_race.id)
                    status = "skipped"
                    skipped_count += 1
            
            added_tx_data.append({
                "date": tx["date"],
                "amount": tx["amount"],
                "description": clean_desc,
                "status": status
            })
        # Update monthly balance
        open_bal = balances.get("opening_balance", 0)
        close_bal = balances.get("closing_balance", 0)
        mb = MonthlyBalance.query.filter_by(month=month_str).first()
        if not mb:
            mb = MonthlyBalance(month=month_str, opening_balance=open_bal, closing_balance=close_bal, source="bank_statement")
            db.session.add(mb)
        else:
            mb.opening_balance = open_bal
            mb.closing_balance = close_bal
        db.session.commit()

        # ---------------------------------------------------------
        # 1. Populate/Update StatementAnalysis Table
        # ---------------------------------------------------------
        # FIX: Calculate totals based on the ACTUAL transactions in the statement PDF
        # instead of a strict calendar month query. This handles billing cycles that span months.
        
        calc_income = 0.0
        calc_expense = 0.0
        income_bd = {}
        expense_bd = {}
        
        # extracted_txs contains all transactions found in the PDF
        print(f"📊 Starting Analysis Calculation with {len(extracted_txs)} transactions...")
        
        for tx in extracted_txs:
            try:
                # 1. Parse Amount
                raw_amt = str(tx.get("amount", "0"))
                # Remove currency symbols and commas
                clean_amt_str = raw_amt.replace("Rs", "").replace(",", "").strip()
                amt = float(clean_amt_str)
                
                # 2. Parse Type
                t_type = str(tx.get("type", "")).lower().strip()
                desc = tx.get("description", "Uncategorized")
                
                # Debug log for each tx
                print(f"   >> Processing: {desc[:20]}... | Type: {t_type} | Amt: {amt}")

                # 3. Categorize & Sum
                # Handle cases: 'debit', 'dr', 'credit', 'cr'
                # Also check if amount itself is negative (some parsers return -39000 for debit)
                
                is_credit = t_type in ['credit', 'cr', 'deposit']
                is_debit = t_type in ['debit', 'dr', 'withdrawal']
                
                # Fallback: Inference from amount sign if type is ambiguous
                if not is_credit and not is_debit:
                    if amt < 0:
                        is_debit = True
                        amt = abs(amt) # Treat expense as positive magnitude for summation
                    else:
                        # Default assumption? Standard statements usually separate columns.
                        # If truly unknown, maybe skip or assume debit?
                        pass 
                
                # Adjust amount sign for calculation
                # We want total_income (positive) and total_expense (positive magnitude)
                
                cat = "Uncategorized"  # Placeholder
                
                if is_credit:
                    calc_income += abs(amt)
                    income_bd[cat] = income_bd.get(cat, 0) + abs(amt)
                elif is_debit:
                    # Ensure we add positive magnitude to expenses
                    calc_expense += abs(amt) 
                    expense_bd[cat] = expense_bd.get(cat, 0) + abs(amt)
                else:
                    print(f"   ⚠️ Skipping ambiguous transaction: {t_type} | {amt}")
                    
            except Exception as e:
                print(f"⚠️ Error summing transaction for analysis: {e}")
                continue
                
        print(f"📊 Final Calc -> Income: {calc_income}, Expense: {calc_expense}")
                
        calc_surplus = calc_income - calc_expense
        calc_status = "Surplus" if calc_surplus >= 0 else "Deficit"
        
        breakdown_obj = {"income": income_bd, "expenses": expense_bd}
        
        # Update StatementAnalysis
        stmt_analysis = StatementAnalysis.query.filter_by(month=month_str).first()
        if not stmt_analysis:
            stmt_analysis = StatementAnalysis(month=month_str)
            db.session.add(stmt_analysis)
            
        stmt_analysis.opening_balance = open_bal
        stmt_analysis.closing_balance = close_bal
        stmt_analysis.total_income = calc_income
        stmt_analysis.total_expense = calc_expense
        stmt_analysis.net_result = calc_surplus
        stmt_analysis.status = calc_status
        stmt_analysis.breakdown_json = json.dumps(breakdown_obj)
        stmt_analysis.analysis_date = datetime.utcnow()
        stmt_analysis.transaction_ids = json.dumps(stmt_transaction_ids)
        
        if not stmt_analysis.statement_id:
            stmt_analysis.statement_id = month_str
        
        # ----------------------------------------------------------------------
        # OPTIMIZED BALANCE APPLICATION (ON CALCULATION IF UNREAD)
        # Map statement PDF → one AccountBalance row; only update that wallet.
        # ----------------------------------------------------------------------
        balance_update_message = ""
        wallet_match_info = {
            "reason": None,
            "detail": None,
            "account_balance_source": stmt_analysis.account_balance_source,
        }

        if not stmt_analysis.reviewed_at:
            from config import TARGET_ACCOUNT_NUMBER
            from statement_wallet_match import resolve_account_balance_for_statement

            ab_row, match_reason, match_detail = resolve_account_balance_for_statement(
                result.get("statement_detected_account_numbers"),
                result.get("statement_matching_text"),
                TARGET_ACCOUNT_NUMBER,
            )
            wallet_match_info["reason"] = match_reason
            wallet_match_info["detail"] = match_detail
            print(f"[statement] wallet resolve: {match_reason} — {match_detail}")

            if ab_row is None:
                balance_update_message = (
                    "Transactions saved; closing balance not written to any wallet until mapping is resolved. "
                    + match_detail
                )
                stmt_analysis.processing_status = "partial"
                prev = (stmt_analysis.processing_notes or "").strip()
                note = f"Balance mapping: {match_reason} — {match_detail}"
                stmt_analysis.processing_notes = (prev + "\n" + note).strip() if prev else note
            else:
                stmt_analysis.account_balance_source = ab_row.source
                wallet_match_info["account_balance_source"] = ab_row.source
                stmt_analysis.processing_status = "success"

                old_balance = ab_row.current_balance
                ab_row.current_balance = close_bal
                ab_row.last_updated = datetime.now()
                ab_row.is_manual = False

                balance_update_message = (
                    f"Balance SET on `{ab_row.source}` ({ab_row.display_name or ab_row.source}) "
                    f"from {old_balance:,.2f} to {close_bal:,.2f}"
                )
                print(f"✅ {balance_update_message}")

                stmt_analysis.balance_applied = True
                stmt_analysis.reviewed_at = datetime.utcnow()
                print(f"✅ UNREAD Statement {month_str} processed & balance applied.")
        else:
            balance_update_message = "Statement already read - no balance update."
            wallet_match_info["reason"] = "already_reviewed"
            wallet_match_info["detail"] = month_str

        db.session.commit()
        db.session.refresh(stmt_analysis)
        stmt_dict = stmt_analysis.to_dict()
        print(f"✅ Updated StatementAnalysis for {month_str}")

        # --- TRIGGER AI ANALYSIS ---
        response = {
            "message": "Statement processed",
            "month": month_str,
            "added_transactions": added_count,
            "skipped_transactions": skipped_count,
            "data": added_tx_data,
            "balances": {"opening": open_bal, "closing": close_bal},
            "balance_update": balance_update_message,
            "processing_status": stmt_analysis.processing_status,
            "read_status": "read" if stmt_analysis.reviewed_at else "unread",
            "balance_matches": stmt_dict.get("balance_matches", True),
            "account_balance_source": stmt_analysis.account_balance_source,
            "statement_wallet_match": wallet_match_info,
        }
        # ---------------------------------------------------------
        # DRIVE BACKUP TRIGGER
        # ---------------------------------------------------------
        if current_user.google_refresh_token:
             from drive_utils import get_drive_service, ensure_folder_path, upload_json
             try:
                 print(f"☁️ Backing up {month_str} to Drive...")
                 service = get_drive_service(current_user)
                 if service:
                     backup_payload = {
                         "month": month_str,
                         "transactions": extracted_txs,
                         "balances": balances,
                         "summary": {
                            "opening": open_bal,
                            "closing": close_bal,
                            "expense": mb.expense if mb.expense else 0.0,
                            "savings": mb.savings if mb.savings else 0.0
                         }
                     }
                     folder_id = ensure_folder_path(service, ["Aurestra Finance", month_str])
                     if folder_id:
                         upload_json(service, folder_id, "statement.json", backup_payload)
             except Exception as drive_err:
                 print(f"⚠️ Drive Backup Failed: {drive_err}")

        return jsonify(response)
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@misc_bp.route("/api/sms/last-sync", methods=["GET"])
@token_required
def get_last_sms_sync(current_user):
    try:
        # Find the VERY LATEST transaction created via SMS
        latest_tx = Transaction.query.filter(
            Transaction.source.in_(['bank_sms', 'sms'])
        ).order_by(Transaction.created_at.desc()).first()
        
        if latest_tx:
            return jsonify({
                "last_sync_time": latest_tx.created_at.isoformat(),
                "source": "database"
            })
        else:
            # DB is empty. Return null so frontend can decide the default (e.g. Jan 31st)
            return jsonify({
                "last_sync_time": None,
                "source": "empty_db"
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@misc_bp.route("/api/notifications/ingest", methods=["POST"])
@token_required
def api_notifications_ingest(current_user):
    try:
        data = request.get_json(force=True, silent=True) or {}
        result = ingest_notification_for_user(current_user.id, data)
        code = 201 if result.get("created") else 200
        return jsonify(result), code
    except Exception as e:
        print(f"❌ notifications ingest: {e}")
        return jsonify({"error": str(e), "status": "error"}), 500


@misc_bp.route("/api/notifications", methods=["GET"])
@token_required
def api_notifications_list(current_user):
    try:
        limit = request.args.get("limit", 100, type=int)
        items = list_notifications_for_user(current_user.id, limit=limit)
        return jsonify({"notifications": items}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@misc_bp.route("/api/sms/process", methods=["POST"])
@token_required
def api_process_sms(current_user):
    from sms_parser import process_bank_sms
    
    try:
        data = request.get_json() or {}
        message = data.get("message")
        sender = data.get("sender", "BAHL")
        
        if not message:
            return jsonify({"error": "Message content is required"}), 400
            
        print(f"📩 Processing SMS from {sender}: {message[:50]}...")
        
        # Process the SMS
        # result is (transaction, is_new)
        tx, is_new = process_bank_sms(message, sender)
        
        if not tx:
             # Check if it was ignored or failed
             from sms_parser import BankAlhabibSMSParser
             if not BankAlhabibSMSParser.is_transaction_sms(message):
                 return jsonify({
                     "status": "ignored", 
                     "message": "SMS is not a transaction"
                 }), 200
             else:
                 return jsonify({
                     "status": "failed",
                     "message": "Could not parse SMS"
                 }), 400
             
        # Verification: Check if it actually saved
        tx_data = {
            "id": tx.id,
            "amount": tx.amount,
            "type": tx.type,
            "description": tx.notes,
            "date": tx.date.strftime("%Y-%m-%d %H:%M:%S"),
            "is_new": is_new
        }
        return jsonify({
            "status": "success",
            "message": "Transaction recorded" if is_new else "Transaction already exists",
            "transaction": tx_data
        }), 201
            
        return jsonify(result), 200

    except Exception as e:
        print(f"❌ SMS API Error: {e}")
        return jsonify({"error": str(e)}), 500

@misc_bp.route("/api/calculate-summary", methods=["POST"])
def calculate_summary_endpoint():
    try:
        data = request.get_json() or {}
        month_str = data.get("month", datetime.now().strftime("%Y-%m"))
        
        dt = datetime.strptime(month_str, "%Y-%m")
        
        # Live Calculation
        total_income = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit',
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0
        
        total_expense = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year', Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'debit',
            Transaction.is_deleted.isnot(True),
            Transaction.is_spam.isnot(True),
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0
        
        total_savings = total_income - total_expense
        total_income = round(float(total_income), 2)
        total_expense = round(abs(float(total_expense)), 2)
        total_savings = round(total_income - total_expense, 2)
        
        # Update MonthlyBalance in DB for persistence
        summary = MonthlyBalance.query.filter_by(month=month_str).first()
        if not summary:
            summary = MonthlyBalance(
                month=month_str, 
                opening_balance=0, 
                closing_balance=0,
                source="combined"  # Explicitly set source
            )
            db.session.add(summary)
        
        # Update fields
        summary.expense = total_expense
        summary.savings = total_savings
        summary.closing_balance = summary.opening_balance + total_income - total_expense
        summary.closing_balance = (summary.opening_balance or 0) + total_savings
        # Ensure source is set if updating existing
        if not summary.source:
             summary.source = "combined"
             
        db.session.commit()

        # --- TRIGGER AI ANALYSIS ---
        try:
            # Need finding target_year/month if not defined?
            # Assuming dt.year/dt.month work
            print(f"🤖 Triggering Financial Agent for {dt.year}-{dt.month:02d}...")
            agent = FinancialAgent()
            agent.analyze_month(dt.year, dt.month)
        except Exception as e:
            print(f"❌ Financial Agent Error: {e}")
            # Don't fail the whole request if analysis fails, just log it.

        # Backup to Google Drive (if enabled)
        # Note: 'extracted_txs', 'balances', etc are not available here easily unless recalculated
        # But user wants summary updated.
        
        response_payload = {
            "message": f"Statement processed for {month_str}",
            "data": {
                "month": month_str,
                "summary": {
                    "expense": summary.expense,
                    "savings": summary.savings
                }
            }
        }
        return jsonify(response_payload), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



