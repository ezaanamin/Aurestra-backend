"""Auto-split from legacy app.py — sms controller."""
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

sms_bp = Blueprint("sms", __name__)

# -------------------------
# SMS ROUTES (NEW) 🎉
# -------------------------

@sms_bp.route("/api/sms/process", methods=["POST"])
def process_sms():
    """Process bank SMS and create transaction"""
    try:
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_process', 20, 60):
        #     return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({"error": "SMS message required"}), 400
        
        message = data.get('message')
        sender = data.get('sender', 'BAHL')
        
        if not message or len(message.strip()) < 10:
            return jsonify({"error": "Invalid SMS message"}), 400
        
        # Consistent hash generation if metadata is provided
        external_sms_hash = None
        device_sms_id = data.get('_id') or data.get('id')
        date_val = data.get('date')
        
        if device_sms_id and date_val:
            device_timestamp = None
            if isinstance(date_val, int):
                device_timestamp = datetime.fromtimestamp(date_val / 1000.0)
            elif isinstance(date_val, str):
                try:
                    device_timestamp = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                except:
                    device_timestamp = datetime.utcnow()
            else:
                device_timestamp = datetime.utcnow()
                
            hash_input = f"{device_sms_id}|{sender}|{message}|{device_timestamp.isoformat()}"
            external_sms_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

        # result is now (transaction, is_new)
        transaction, is_new = process_bank_sms(message, sender, external_sms_hash=external_sms_hash)
        
        if not transaction:
            from sms_parser import BankAlhabibSMSParser
            if not BankAlhabibSMSParser.is_transaction_sms(message):
                return jsonify({
                    "status": "skipped",
                    "message": "SMS is not a transaction (OTP, info, etc.)"
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "message": "Could not parse transaction from SMS"
                }), 400
        
        status_msg = "recorded" if is_new else "already exists"
        print(f"✅ SMS transaction {status_msg}: {transaction.id}")
        try:
            log_wallet_attribution(
                "SMS_API_AFTER_PROCESS",
                transaction_id=transaction.id,
                is_new=is_new,
                resolved_wallet_slug=getattr(transaction, "account_balance_source", None),
                txn_type=transaction.type,
                amount=transaction.amount,
                purpose=getattr(transaction, "purpose", None),
                txn_sender=getattr(transaction, "sender", None),
                txn_receiver=getattr(transaction, "receiver", None),
            )
        except Exception as _e:
            print(f"⚠️ wallet attribution log: {_e}")
        
        from model import AccountBalance
        accounts = [acc.to_dict() for acc in AccountBalance.query.all()]
        
        if is_new and not is_own_account_transfer_row(transaction):
            try:
                if transaction.type == 'debit':
                    merchant = transaction.receiver or "Merchant"
                    title = "💸 New Spending Detected"
                    body = f"You just spent PKR {transaction.amount:,.0f} at {merchant}. Tap to categorize it now!"
                    send_push_to_all(title, body)
                elif transaction.type == 'credit':
                    sender = transaction.sender or "Source"
                    title = "💰 Money Received!"
                    body = f"PKR {transaction.amount:,.0f} has been credited to your account from {sender}. Tap to see details."
                    send_push_to_all(title, body)
            except Exception as e:
                print(f"⚠️ Push notification failed: {e}")

        
        return jsonify({
            "status": "success",
            "message": "Transaction created from SMS",
            "transaction": transaction.to_dict(),
            "accounts": accounts
        }), 201
        
    except Exception as e:
        print(f"❌ Error processing SMS: {e}")
        return jsonify({"error": "Failed to process SMS"}), 500

@sms_bp.route("/api/sms/test", methods=["POST"])
def test_sms_parsing():
    """Test SMS parsing without saving"""
    try:
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_test', 10, 60):
        #     return jsonify({"error": "Rate limit exceeded"}), 429
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({"error": "SMS message required"}), 400
        
        message = data.get('message')
        sender = data.get('sender', 'BAHL')
        
        transaction_data = BankAlhabibSMSParser.parse_sms(message, sender)
        
        if not transaction_data:
            if not BankAlhabibSMSParser.is_transaction_sms(message):
                return jsonify({
                    "status": "skipped",
                    "message": "Not a transaction SMS"
                }), 200
            else:
                return jsonify({
                    "status": "failed",
                    "message": "Could not parse SMS"
                }), 400
        
        response = {
            "status": "success",
            "message": "SMS parsed successfully",
            "parsed_data": {
                "type": transaction_data['type'],
                "amount": transaction_data['amount'],
                "purpose": transaction_data['purpose'],
                "date": transaction_data['date'].isoformat(),
                "notes": transaction_data['notes'],
            }
        }
        
        if transaction_data['type'] == 'credit':
            response['parsed_data']['sender'] = transaction_data['sender']
        else:
            response['parsed_data']['receiver'] = transaction_data['receiver']
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@sms_bp.route("/api/sms/batch", methods=["POST"])
@token_required
def process_batch_sms(current_user):
    """
    Process multiple SMS messages with Backend-Authoritative Deduplication.
    1. Insert raw messages into 'sms_messages' (deduplicated by unique sms_hash).
    2. Process only 'pending' messages to create transactions.
    """
    from model import SMSHistory, Transaction
    from sms_parser import generate_sms_hash, process_bank_sms
    
    try:
        # Rate limiting
        # Rate limiting disabled - function not implemented
        # if not rate_limit_check('sms_batch', 10, 60):
        #     return jsonify({"error": "Rate limit exceeded."}), 429
        
        data = request.get_json()
        messages = data.get('messages', [])
        
        if not messages:
            return jsonify({"error": "Messages array required"}), 400
        
        if len(messages) > 1000: # Increased limit for raw sync
             return jsonify({"error": "Maximum 1000 messages per batch"}), 400
             
        stats = {
            'received': len(messages),
            'inserted': 0,
            'processed': 0,
            'transactions_created': 0,
            'duplicates_ignored': 0,
            'errors': 0
        }
        
        # 1. INSERT RAW MESSAGES (Database-Level Deduplication)
        new_sms_ids = []
        
        if messages:
            print(f"📥 [Batch] Received {len(messages)} messages")
            print(f"📥 [Sample] First Item: {messages[0]}")
        
        for msg_data in messages:
            try:
                # Extract fields
                body = msg_data.get('body') or msg_data.get('message')
                sender = msg_data.get('address') or msg_data.get('sender')
                device_sms_id = str(msg_data.get('_id', '')) or str(msg_data.get('id', ''))  # Android SMS ID
                
                # Handle timestamp: Frontend sends milliseconds
                date_val = msg_data.get('date')
                device_timestamp = None
                
                if isinstance(date_val, int):
                    device_timestamp = datetime.fromtimestamp(date_val / 1000.0)
                elif isinstance(date_val, str):
                    try:
                        device_timestamp = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                    except:
                        device_timestamp = datetime.utcnow()
                else:
                    device_timestamp = datetime.utcnow()
                    
                if not body or not sender:
                    print(f"⚠️ Skipping SMS: Missing body or sender")
                    continue

                # 🎯 DETERMINISTIC HASH GENERATION
                # hash_input = device_sms_id + sender + body + device_timestamp
                hash_input = f"{device_sms_id}|{sender}|{body}|{device_timestamp.isoformat()}"
                sms_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
                
                print(f"🔐 Hash: {sms_hash[:16]}... (ID: {device_sms_id}, Sender: {sender})")
                
                # 🛡️ DATABASE-LEVEL DEDUPLICATION
                # Try to insert - UNIQUE constraint on sms_hash will prevent duplicates
                new_msg = SMSHistory(
                    device_sms_id=device_sms_id,
                    sender=sender,
                    body=body,
                    device_timestamp=device_timestamp,
                    sms_hash=sms_hash,
                    status='pending'
                )
                
                db.session.add(new_msg)
                
                try:
                    db.session.flush()  # Force constraint check NOW
                    new_sms_ids.append(new_msg.id)
                    stats['inserted'] += 1
                    print(f"✅ Inserted SMS ID: {new_msg.id}")
                    
                except Exception as flush_error:
                    db.session.rollback()  # Rollback this specific insert
                    
                    # Check if it's a duplicate (UNIQUE constraint violation)
                    if 'Duplicate entry' in str(flush_error) or 'UNIQUE constraint' in str(flush_error):
                        stats['duplicates_ignored'] += 1
                        print(f"⏭️  Duplicate ignored: {sms_hash[:16]}...")
                    else:
                        stats['errors'] += 1
                        print(f"❌ Insert error: {flush_error}")
                
            except Exception as e:
                print(f"❌ Error processing SMS: {e}")
                stats['errors'] += 1
        
        db.session.commit()  # Commit all successful inserts
        
        # 2. PROCESS PENDING MESSAGES
        pending_messages = SMSHistory.query.filter(SMSHistory.id.in_(new_sms_ids)).all()
        
        affected_months = set()
        created_transactions = [] # List to hold details for UI
        
        for sms in pending_messages:
            try:
                # Use parser
                transaction, is_new = process_bank_sms(sms.body, sms.sender, external_sms_hash=sms.sms_hash)
                
                if transaction:
                    if is_new:
                        stats['transactions_created'] += 1
                        month_key = transaction.date.strftime('%Y-%m')
                        affected_months.add(month_key)
                        
                        # Add to list for UI
                        created_transactions.append({
                            'id': transaction.id,
                            'type': transaction.type,
                            'amount': transaction.amount,
                            'date': transaction.date.isoformat(),
                            'purpose': transaction.purpose,
                            'is_new': True
                        })
                    
                    sms.status = 'processed'
                else:
                    sms.status = 'ignored'
                    
                stats['processed'] += 1
                
            except Exception as e:
                print(f"❌ Error processing SMS {sms.id}: {e}")
                sms.status = 'error'
                stats['errors'] += 1
        
        db.session.commit()
        
        # 3. Update Summaries
        for month_key in affected_months:
             # (Simplified summary update trigger)
            pass

        stats['transactions'] = created_transactions # Add to response
        
        print(f"✅ Batch Complete: {stats}")
        return jsonify(stats), 200

    except Exception as e:
        db.session.rollback()
        print(f"🔥 Batch Fatal Error: {e}")
        return jsonify({"error": str(e)}), 500


