import os
import email
import imaplib
import re
import base64
from datetime import datetime, timedelta
from email.header import decode_header
import fitz  # PyMuPDF
from utils import decode_mime_words, extract_text_from_pdf
from balances import extract_balances_from_bank, extract_transactions_from_bank
from config import *
from PIL import Image
from model import Transaction
from database import db

# -------------------------
# SETUP
# -------------------------
IMAP_HOST = "imap.gmail.com"
ATTACHMENTS_DIR = "attachments"
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)


def clean(text):
    if not isinstance(text, str):
        return None
    return text.replace("\xa0", " ").strip()

# -------------------------
# HELPERS
# -------------------------
def get_search_date():
    """
    Returns the search date string for email query.
    Look back to the 1st of the previous month.
    """
    today = datetime.today()
    first_of_this_month = today.replace(day=1)
    prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = prev_month.replace(day=1)
    return first_of_prev_month.strftime("%d-%b-%Y")

def safe_float(val):
    try:
        return float(str(val).replace(",", "")) if val is not None else 0.0
    except:
        return 0.0

# -------------------------
# FETCH LATEST BANK EMAIL
# -------------------------
def fetch_latest_bank_email():
    """
    Scans recent emails for bank statements and calculates current flow.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        from config import BANK_EMAIL_ACCOUNT, BANK_APP_PASSWORD, BANK_PDF_PASSWORD, TARGET_ACCOUNT_NUMBER, IGNORE_ACCOUNT_NUMBER, BANK_SENDER
        
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        imap_user = str(BANK_EMAIL_ACCOUNT).strip()
        pwd = str(BANK_APP_PASSWORD).replace(" ", "").strip()
        
        if "ezean" in imap_user:
            imap_user = "ezaan.amin@gmail.com"
            
        print(f"📧 [Fetch Latest] Connecting as: {imap_user}")
        mail.login(imap_user, pwd)
        mail.select("inbox")

        search_date = get_search_date()
        status, data = mail.search(None, f'FROM "{BANK_SENDER}" SINCE "{search_date}"')

        if status != "OK" or not data[0]:
            mail.logout()
            return {"error": "No recent bank statements found."}

        email_ids = data[0].split()
        all_transactions = []
        all_email_data = [] 
        processed_count = 0

        for e_id in email_ids:
            try:
                status, msg_data = mail.fetch(e_id, "(RFC822)")
                if status != "OK": continue
                
                msg = email.message_from_bytes(msg_data[0][1])
                # Parse Date
                date_str = msg.get("Date")
                statement_date = datetime.utcnow()
                try:
                    statement_date = email.utils.parsedate_to_datetime(date_str)
                    if statement_date.tzinfo is not None:
                        statement_date = statement_date.replace(tzinfo=None)
                except: pass

                # Extract PDF
                extracted_text = None
                if msg.is_multipart():
                    for part in msg.walk():
                        filename = part.get_filename()
                        if filename and filename.lower().endswith(".pdf"):
                            path = os.path.join(ATTACHMENTS_DIR, f"stm_{e_id.decode()}.pdf")
                            with open(path, "wb") as f:
                                f.write(part.get_payload(decode=True))
                            
                            extracted_text = extract_text_from_pdf(path, BANK_PDF_PASSWORD)
                            if os.path.exists(path):
                                os.remove(path)
                            if extracted_text:
                                break 

                if extracted_text:
                    is_target = TARGET_ACCOUNT_NUMBER and TARGET_ACCOUNT_NUMBER in extracted_text
                    
                    if IGNORE_ACCOUNT_NUMBER and IGNORE_ACCOUNT_NUMBER in extracted_text:
                         if not is_target: continue

                    if TARGET_ACCOUNT_NUMBER and not is_target: continue

                    # ISOLATE ACCOUNT SECTION
                    target_text = extracted_text
                    if TARGET_ACCOUNT_NUMBER:
                        clean_text = re.sub(r'\s+', ' ', extracted_text)
                        acct_pos = clean_text.find(TARGET_ACCOUNT_NUMBER)
                        if acct_pos != -1:
                            original_acct_pos = extracted_text.find(TARGET_ACCOUNT_NUMBER)
                            target_text = extracted_text[original_acct_pos:]
                            next_ref = re.search(r'Account No\s*:\s*\d+|SUMMARY OF ALL ACCOUNTS', target_text[len(TARGET_ACCOUNT_NUMBER):])
                            if next_ref:
                                target_text = target_text[:len(TARGET_ACCOUNT_NUMBER) + next_ref.start()]

                    balances = extract_balances_from_bank(target_text, target_account=TARGET_ACCOUNT_NUMBER)
                    transactions = extract_transactions_from_bank(target_text)
                    
                    opening_bal = safe_float(balances.get("opening_balance", 0))
                    closing_bal = safe_float(balances.get("closing_balance", 0))
                    
                    all_email_data.append({
                        'email_id': e_id.decode(),
                        'date': statement_date,
                        'opening_balance': opening_bal,
                        'closing_balance': closing_bal,
                        'transactions': transactions
                    })
                    all_transactions.extend(transactions)
                    processed_count += 1
                        
            except Exception as inner_e:
                print(f"⚠️ Error processing email {e_id}: {inner_e}")
                continue

        mail.logout()

        if processed_count == 0:
             return {"error": "No bank statements found."}

        all_email_data.sort(key=lambda x: x['date'])
        first_opening = all_email_data[0]['opening_balance']
        last_closing = all_email_data[-1]['closing_balance']
        
        # Try to find statement dated at month-end (28-31)
        month_end_candidates = [d for d in all_email_data if d['date'].day >= 28]
        if month_end_candidates:
            last_closing = month_end_candidates[-1]['closing_balance']

        return {
            "balances": {"opening_balance": first_opening, "closing_balance": last_closing},
            "transactions": all_transactions,
            "count": len(all_transactions),
            "emails_processed": processed_count
        }

    except Exception as e:
        return {"error": f"Bank email fetch error: {str(e)}"}

# -------------------------
# COMBINE SUMMARY
# -------------------------
def calculate_combined_summary():
    """Calculates flows using only bank data (Wallet disabled)"""
    bank_data = fetch_latest_bank_email()

    opening = 0.0
    closing = 0.0
    
    if "error" not in bank_data and "balances" in bank_data:
        opening = safe_float(bank_data["balances"].get("opening_balance"))
        closing = safe_float(bank_data["balances"].get("closing_balance"))
    else:
        return {"error": bank_data.get("error", "Bank statement not available.")}

    net_change = closing - opening
    return {
        "total_opening_balance": opening,
        "total_closing_balance": closing,
        "total_expense": abs(net_change) if net_change < 0 else 0.0,
        "total_income": 0.0, 
        "total_savings": net_change if net_change > 0 else 0.0,
    }

# -------------------------
# FETCH SPECIFIC MONTH STATEMENT (GMAIL API VERSION)
# -------------------------
def fetch_previous_month_statement(user, reference_date=None):
    """
    Fetches the bank statement for the month PREVIOUS to reference_date using GMAIL API.
    """
    from drive_utils import get_gmail_service, list_gmail_messages, get_gmail_message, get_gmail_attachment
    
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        
        from config import BANK_PDF_PASSWORD, TARGET_ACCOUNT_NUMBER, IGNORE_ACCOUNT_NUMBER, BANK_SENDER
        
        print(f"📋 [API Fetch] Detailed Config Check for {user.email}")
        
        # 1. AUTHENTICATE
        service = get_gmail_service(user)
        if not service:
            print("❌ [API Fetch] Failed to build Gmail service")
            return {"error": "Gmail API access not authorized. Please link your Google account again."}

        # 2. CALCULATE DATE RANGE
        today = reference_date if reference_date else datetime.today()
        first = today.replace(day=1)
        last_month = first - timedelta(days=1)
        
        after_date = (last_month.replace(day=20) - timedelta(days=5)).strftime("%Y/%m/%d")
        before_date = (today + timedelta(days=5)).strftime("%Y/%m/%d")
        
        # 3. SEARCH GMAIL
        query = f'from:{BANK_SENDER} after:{after_date} before:{before_date} statement'
        print(f"🔍 [API Fetch] Search Query: {query}")
        
        messages = list_gmail_messages(service, query)
        
        if not messages:
            print(f"⚠️ [API Fetch] No emails found for query [{query}]")
            return {"error": f"No bank emails found in Gmail for query: {query}"}

        print(f"📨 [API Fetch] Found {len(messages)} candidate emails.")
        
        all_email_data = []
        merged_transactions = []
        month_name = last_month.strftime("%B %Y")
        
        limit_start_date = last_month.replace(day=20)
        limit_end_date = today + timedelta(days=5)

        for idx, m_meta in enumerate(messages):
            try:
                m_id = m_meta['id']
                print(f"📧 [API Fetch] [{idx+1}/{len(messages)}] Processing Message ID: {m_id}")
                
                msg = get_gmail_message(service, m_id)
                if not msg: continue
                
                internal_date_ms = int(msg.get('internalDate', 0))
                email_dt = datetime.fromtimestamp(internal_date_ms / 1000.0)
                
                if not (limit_start_date <= email_dt <= limit_end_date): continue
                
                # PROCESS ATTACHMENTS
                extracted_text = None
                payload = msg.get('payload', {})
                parts = payload.get('parts', [])
                if not parts and 'body' in payload: parts = [payload]

                def find_pdf_in_parts(parts_list):
                    nonlocal extracted_text
                    for part in parts_list:
                        if part.get('parts'): find_pdf_in_parts(part.get('parts'))
                        if extracted_text: return
                        
                        filename = part.get('filename')
                        if filename and filename.lower().endswith(".pdf"):
                            att_id = part.get('body', {}).get('attachmentId')
                            if att_id:
                                pdf_data = get_gmail_attachment(service, m_id, att_id)
                                if pdf_data:
                                    path = os.path.join(ATTACHMENTS_DIR, f"api_stm_{m_id}.pdf")
                                    with open(path, "wb") as f: f.write(pdf_data)
                                    extracted_text = extract_text_from_pdf(path, BANK_PDF_PASSWORD)
                                    if os.path.exists(path): os.remove(path)
                                    if extracted_text: return

                find_pdf_in_parts(parts)

                if extracted_text:
                    if IGNORE_ACCOUNT_NUMBER and IGNORE_ACCOUNT_NUMBER in extracted_text and not (TARGET_ACCOUNT_NUMBER and TARGET_ACCOUNT_NUMBER in extracted_text):
                        continue
                    if TARGET_ACCOUNT_NUMBER and TARGET_ACCOUNT_NUMBER not in extracted_text:
                        continue
                    
                    target_text = extracted_text
                    if TARGET_ACCOUNT_NUMBER:
                        original_acct_pos = extracted_text.find(TARGET_ACCOUNT_NUMBER)
                        if original_acct_pos != -1:
                            target_text = extracted_text[original_acct_pos:]
                            next_ref = re.search(r'Account No\s*:\s*\d+|SUMMARY OF ALL ACCOUNTS', target_text[len(TARGET_ACCOUNT_NUMBER):])
                            if next_ref: target_text = target_text[:len(TARGET_ACCOUNT_NUMBER) + next_ref.start()]

                    balances = extract_balances_from_bank(target_text, target_account=TARGET_ACCOUNT_NUMBER)
                    transactions = extract_transactions_from_bank(target_text)
                    all_email_data.append({
                        'id': m_id, 'date': email_dt,
                        'opening_balance': safe_float(balances.get("opening_balance", 0)),
                        'closing_balance': safe_float(balances.get("closing_balance", 0)),
                        'transactions': transactions
                    })
                    merged_transactions.extend(transactions)
            except Exception as e:
                print(f"   ❌ ERROR message {m_id}: {e}")
                continue
        
        if not all_email_data:
            return {"error": "No valid bank statements found in Gmail PDF attachments."}
        
        all_email_data.sort(key=lambda x: x['date'])
        first_opening = all_email_data[0]['opening_balance']
        last_closing = all_email_data[-1]['closing_balance']
        
        # Refine closing balance for month-end
        target_month, target_year = last_month.month, last_month.year
        month_end_candidates = [e for e in all_email_data if e['date'].month == target_month and e['date'].year == target_year and e['date'].day >= 28]
        if month_end_candidates: last_closing = month_end_candidates[-1]['closing_balance']
            
        return {
            "balances": {"opening_balance": first_opening, "closing_balance": last_closing},
            "transactions": merged_transactions,
            "month_name": month_name,
            "all_email_data": [{
                "date": e["date"].isoformat(),
                "opening_balance": e["opening_balance"],
                "closing_balance": e["closing_balance"]
            } for e in all_email_data]
        }
    except Exception as e:
        print(f"🔴 [API Fetch] ERROR: {e}")
        return {"error": f"Gmail API Error: {str(e)}"}