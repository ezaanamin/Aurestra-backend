"""
Bank Alhabib SMS Parser
=======================
Parses Bank Alhabib SMS messages and extracts transaction details
"""

import re
import hashlib
from datetime import datetime
from model import Transaction, AccountBalance
from database import db
from sqlalchemy import func
import json
import logging

from ledger_sync import (
    apply_pending_transaction_ledger,
    ensure_account_balance_row,
    log_wallet_attribution,
    resolve_balance_slug_for_ingest,
)

logger = logging.getLogger(__name__)


class BankAlhabibSMSParser:
    """Parser specifically for Bank Alhabib SMS messages"""
    
    # Account number patterns
    ACCOUNT_PATTERNS = [
        r'A/C\s+\(?(PK\*\*BAHL\*{4}\d+)\)?',  # PK**BAHL****6801
        r'A/C\s+(\d{4}-\d{4}-\*{6}-\d{2}-\d)',  # 0460-0981-******-01-4
        r'Account\s+\((\d{4}-\d{4}-\*{6}-\d{2}-\d)\)',
    ]
    
    # Transaction patterns
    CREDIT_PATTERN = re.compile(
        r'credited\s+(?:with|by)\s+(?:Rs\.|PKR)\s*([0-9,]+\.?\d*)\s+via\s+(.+?)\s+from\s+(.+?)\s+on\s+(\d{2}[/\-]\d{2}[/\-]\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})',
        re.IGNORECASE
    )
    
    DEBIT_PATTERN = re.compile(
        r'debited\s+(?:by|for|of)\s+(?:Rs\.|PKR)\s*([0-9,]+\.?\d*)\s+(?:excluding\s+FED\.?\s*)?(?:for|on|at)?\s*(.+?)(?:\.|For|on|$)',
        re.IGNORECASE
    )
    
    # Simpler version for variations
    CREDIT_PATTERN_SIMPLE = re.compile(
        r'(?:credited\s+(?:with|by|at)|received|added)\s+(?:Rs\.|PKR)?\s*([0-9,]+\.?\d*)\s+(?:from|at)\s+([^,\s]+)',
        re.IGNORECASE
    )

    # Date-based fallback
    CREDIT_PATTERN_DATE = re.compile(
        r'credited\s+with\s+(?:Rs\.|PKR)\s*([0-9,]+\.?\d*)\s+on\s+(\d{2}[/\-]\d{2}[/\-]\d{4})',
        re.IGNORECASE
    )
    
    # Service charge pattern
    SERVICE_CHARGE_PATTERN = re.compile(
        r'debited\s+(?:by|for)\s+(?:PKR|Rs\.)\s*([0-9,]+\.?\d*)\s+excluding FED\.?\s*(?:For|on)\s+(.+?)(?:\.|For|on)',
        re.IGNORECASE
    )
    
    # Card charge pattern
    CARD_CHARGE_PATTERN = re.compile(
        r'debited\s+(?:by|for)\s+(?:PKR|Rs\.)\s*([0-9,]+\.?\d*)\s+(?:excluding FED\.?\s+)?(?:for|on)\s+(.+?),\s+your card no\. ending with\s+\*\*(\d+)',
        re.IGNORECASE
    )

    # Generic app notification: "PKR 720.00 sent to NAME …" (no "from your BAHL" required)
    SENT_TO_GENERIC_PATTERN = re.compile(
        r'(?:PKR|Rs\.?)\s*([0-9,]+\.?\d*)\s+sent\s+to\s+(.+?)(?:\s+from\s+your|\s+on\s+\d|\s+fee|\.\s*$|$)',
        re.IGNORECASE | re.DOTALL,
    )

    # Easypaisa push style: "easypaisa Rs. ..." or "easypaisa: Rs. ..." (colon common in shade/title)
    EASYPAISA_SENT_PATTERN = re.compile(
        r'easypaisa\s*:?\s*(?:rs\.?|pkr)\s*([0-9,]+\.?\d*)\s+sent\s+to\s+(.+?)\s+in\s+(.+?)(?:\.|\s+Fee)',
        re.IGNORECASE | re.DOTALL,
    )

    # BAHL mobile app (notification title + body): Fund Transfer—Debit via Raast
    # PKR 720.00 sent to AZAN AMIN RAAST ID *7444 from your BAHL A/C *6801 on 21-Jan-2026 13:33
    SENT_TO_PATTERN = re.compile(
        r'(?:PKR|Rs\.)\s*([0-9,]+\.?\d*)\s+sent\s+to\s+(.+?)\s+from\s+your\s+BAHL\s+A/C.*?on\s+(\d{1,2}-[a-z]{3}-\d{4}\s+\d{1,2}:\d{2})',
        re.IGNORECASE
    )

    # Raast Send Pattern (Debit)
    RAAST_SEND_PATTERN = re.compile(
        r'PKR\s*([0-9,]+\.?\d*)\s+sent\s+from\s+(.+?)\s+to\s+(.+?)\s+on\s+(\d{2}-\d{2}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})\s+via\s+RAAST',
        re.IGNORECASE
    )

    # POS/Debit Pattern
    POS_DEBIT_PATTERN = re.compile(
        r'used\s+for\s+PKR\s*([0-9,]+\.?\d*)\s+on\s+(\d{2}-\d{2}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})\s+on\s+(.+?)\s+via',
        re.IGNORECASE
    )

    # NEW: Raast Send Pattern (Debit)
    # PKR 100.00 sent from IBAN XXXX6801 to AZAN AMIN of IBAN XXXX7774 on 10-01-2026 at 18:41:44 via RAAST
    RAAST_SEND_PATTERN = re.compile(
        r'PKR\s*([0-9,]+\.?\d*)\s+sent\s+from\s+(.+?)\s+to\s+(.+?)\s+on\s+(\d{2}-\d{2}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})\s+via\s+RAAST',
        re.IGNORECASE
    )

    # NEW: POS/Debit Pattern (User Provided)
    # Your AL Habib A/C X6801 was used for PKR 610.00 on 08-01-2026 at 13:31:50 on GOGA NAQIBIA MURGH CHANEY via Raast P2M
    POS_DEBIT_PATTERN = re.compile(
        r'used\s+for\s+PKR\s*([0-9,]+\.?\d*)\s+on\s+(\d{2}-\d{2}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})\s+on\s+(.+?)\s+via',
        re.IGNORECASE
    )
    
    @staticmethod
    def parse_amount(amount_str):
        """Parse amount string to float"""
        try:
            return float(amount_str.replace(',', '').strip())
        except:
            return 0.0
    
    @staticmethod
    def parse_datetime(date_str, time_str="00:00:00"):
        """Parse date and time strings to datetime object (supports month names)"""
        if not date_str:
            return datetime.utcnow()
            
        try:
            # Clean strings
            date_str = date_str.strip()
            time_str = time_str.strip() if time_str else "00:00:00"
            
            # Handle formats like 21-Jan-2026 13:33
            if '-' in date_str and any(m in date_str.lower() for m in ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']):
                # Some formats might combine date and time in date_str
                full_str = f"{date_str} {time_str}" if time_str != "00:00:00" else date_str
                # Match 21-Jan-2026 13:33 or 21-Jan-2026
                for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
                    try:
                        return datetime.strptime(full_str, fmt)
                    except ValueError:
                        continue

            # Legacy numeric formats (10/12/2025 or 10-12-2025)
            date_clean = date_str.replace('-', '/')
            datetime_str = f"{date_clean} {time_str}"
            
            for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
                try:
                    return datetime.strptime(datetime_str, fmt)
                except ValueError:
                    continue
            
            logger.error(f"Could not parse datetime: {date_str} {time_str}")
            return datetime.utcnow()
        except Exception as e:
            logger.error(f"Error in parse_datetime: {e}")
            return datetime.utcnow()
    
    @staticmethod
    def extract_account_number(message):
        """Extract account number from message"""
        for pattern in BankAlhabibSMSParser.ACCOUNT_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    @classmethod
    def parse_credit_transaction(cls, message):
        """
        Parse credit (incoming) transaction
        """
        # Try complex pattern first
        match = cls.CREDIT_PATTERN.search(message)
        if match:
            amount = cls.parse_amount(match.group(1))
            payment_method = match.group(2).strip()
            sender_name = match.group(3).strip()
            date_str = match.group(4)
            time_str = match.group(5)
            
            transaction_date = cls.parse_datetime(date_str, time_str)
            account_number = cls.extract_account_number(message)
            
            return {
                'type': 'credit',
                'amount': amount,
                'sender': sender_name,
                'purpose': 'Uncategorized', # Changed from Income to allow manual categorization
                'date': transaction_date,
                'notes': f"Via {payment_method}, Account: {account_number}",
                'source': 'sms',
            }



        # Try simple pattern
        match = cls.CREDIT_PATTERN_SIMPLE.search(message)
        if match:
            amount = cls.parse_amount(match.group(1))
            sender_name = match.group(2).strip()
            
            return {
                'type': 'credit',
                'amount': amount,
                'sender': sender_name,
                'purpose': 'Uncategorized', # Changed from Income to allow manual categorization
                'date': datetime.utcnow(),
                'notes': f"Source: {sender_name}",
                'source': 'sms',
            }
        
        # Try date fallback pattern
        match = cls.CREDIT_PATTERN_DATE.search(message)
        if match:
             amount = cls.parse_amount(match.group(1))
             date_str = match.group(2)
             
             return {
                'type': 'credit',
                'amount': amount,
                'sender': 'Bank',
                'purpose': 'Uncategorized',
                'date': cls.parse_datetime(date_str, "00:00:00"),
                'notes': "Credit transaction from Bank",
                'source': 'sms',
            }

        return None
    
    @classmethod
    def parse_debit_transaction(cls, message):
        """
        Parse debit (outgoing) transaction
        
        Examples:
        - "debited by PKR 225.00 excluding FED.For SMS Alert Service Charges"
        - "debited by PKR 129.31 excluding FED for Debit Card Charges, your card no. ending with **6883"
        - "PKR 100.00 sent from IBAN XXXX6801 to..."
        """
        ep = cls.EASYPAISA_SENT_PATTERN.search(message)
        if ep:
            amount = cls.parse_amount(ep.group(1))
            recipient = ep.group(2).strip()
            bank_part = ep.group(3).strip()
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': recipient,
                'purpose': 'Uncategorized',
                'date': datetime.utcnow(),
                'notes': f"Easypaisa → {bank_part}"[:250],
                'source': 'sms',
            }

        gen_sent = cls.SENT_TO_GENERIC_PATTERN.search(message)
        if gen_sent:
            from transfer_matching import scrub_counterparty

            amount = cls.parse_amount(gen_sent.group(1))
            receiver = scrub_counterparty(gen_sent.group(2).strip())
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': receiver or gen_sent.group(2).strip(),
                'purpose': 'Uncategorized',
                'date': datetime.utcnow(),
                'notes': (message[:220] + "…") if len(message) > 220 else message,
                'source': 'sms',
            }

        # Try Raast Send Pattern (Debit)
        match = cls.RAAST_SEND_PATTERN.search(message)
        if match:
             amount = cls.parse_amount(match.group(1))
             sender_clean = match.group(2).replace('IBAN', '').strip()
             receiver = match.group(3).strip()
             date_str = match.group(4) # 10-01-2026
             time_str = match.group(5)

             # Fix date format (DD-MM-YYYY -> DD/MM/YYYY)
             date_str = date_str.replace('-', '/')
             transaction_date = cls.parse_datetime(date_str, time_str)
             
             return {
                'type': 'debit',
                'amount': amount,
                'receiver': receiver, # "AZAN AMIN of IBAN..."
                'purpose': 'Uncategorized', # Could be Transfer to Self or Other
                'date': transaction_date,
                'notes': f"Raast Transfer to {receiver}",
                'source': 'sms',
            }
        
        # Try card charge pattern first (more specific)
        card_match = cls.CARD_CHARGE_PATTERN.search(message)
        if card_match:
            amount = cls.parse_amount(card_match.group(1))
            purpose = card_match.group(2).strip()
            card_last_digits = card_match.group(3)
            
            account_number = cls.extract_account_number(message)
            
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': purpose, # Merchant name extracted from SMS
                'purpose': 'Uncategorized', # Force user categorization in app
                'date': datetime.utcnow(),
                'notes': f"Card ending: {card_last_digits}, Account: {account_number}",
                'source': 'sms',
            }

        # Try POS Debit Pattern
        match = cls.POS_DEBIT_PATTERN.search(message)
        if match:
             amount = cls.parse_amount(match.group(1))
             date_str = match.group(2).replace('-', '/')
             time_str = match.group(3)
             merchant = match.group(4).strip()
             
             transaction_date = cls.parse_datetime(date_str, time_str)

             return {
                'type': 'debit',
                'amount': amount,
                'receiver': merchant,
                'purpose': 'Uncategorized',
                'date': transaction_date,
                'notes': f"POS/Online Purchase at {merchant}",
                'source': 'sms',
            }
        
        # Try service charge pattern
        service_match = cls.SERVICE_CHARGE_PATTERN.search(message)
        if service_match:
            amount = cls.parse_amount(service_match.group(1))
            purpose = service_match.group(2).strip()
            
            account_number = cls.extract_account_number(message)
            
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': 'Bank AL Habib',
                'purpose': 'Uncategorized',
                'date': datetime.utcnow(),
                'notes': f"{purpose}, Account: {account_number}",
                'source': 'sms',
            }
        
        # 1. Try Sent To Pattern (Newest)
        sent_match = cls.SENT_TO_PATTERN.search(message)
        if sent_match:
            amount = cls.parse_amount(sent_match.group(1))
            receiver = sent_match.group(2).strip()
            date_time_str = sent_match.group(3)
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': receiver,
                'purpose': 'Uncategorized',
                'date': cls.parse_datetime(date_time_str),
                'notes': f"Sent to {receiver}",
                'source': 'sms',
            }

        # 2. Try Raast Send Pattern
        raast_match = cls.RAAST_SEND_PATTERN.search(message)
        if raast_match:
            amount = cls.parse_amount(raast_match.group(1))
            receiver = raast_match.group(3).strip()
            date_str = raast_match.group(4)
            time_str = raast_match.group(5)
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': receiver,
                'purpose': 'Uncategorized',
                'date': cls.parse_datetime(date_str, time_str),
                'notes': f"Raast Transfer to {receiver}",
                'source': 'sms',
            }

        # 2. Try POS Pattern
        pos_match = cls.POS_DEBIT_PATTERN.search(message)
        if pos_match:
            amount = cls.parse_amount(pos_match.group(1))
            merchant = pos_match.group(4).strip()
            date_str = pos_match.group(2)
            time_str = pos_match.group(3)
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': merchant,
                'purpose': 'Uncategorized',
                'date': cls.parse_datetime(date_str, time_str),
                'notes': f"POS Purchase at {merchant}",
                'source': 'sms',
            }

        # 3. Try Card Charge Pattern
        card_match = cls.CARD_CHARGE_PATTERN.search(message)
        if card_match:
            amount = cls.parse_amount(card_match.group(1))
            merchant = card_match.group(2).strip()
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': merchant,
                'purpose': 'Uncategorized',
                'date': datetime.utcnow(),
                'notes': f"Card Payment to {merchant}",
                'source': 'sms',
            }

        # 4. Try general debit pattern
        debit_match = cls.DEBIT_PATTERN.search(message)
        if debit_match:
            amount = cls.parse_amount(debit_match.group(1))
            purpose = debit_match.group(2).strip() if debit_match.group(2) else "Bank Charges"
            account_number = cls.extract_account_number(message)
            return {
                'type': 'debit',
                'amount': amount,
                'receiver': purpose,
                'purpose': 'Uncategorized',
                'date': datetime.utcnow(),
                'notes': f"Account: {account_number}",
                'source': 'sms',
            }
        
        return None
    
    @classmethod
    def is_transaction_sms(cls, message):
        """Check if SMS is a transaction (not OTP, info, etc.)"""
        # Skip OTPs, account opening messages, info messages
        skip_keywords = [
            'OTP', 'OTAC', 'One Time',
            'cheque book', 'ready',
            'subscribe', 'E-Statement',
            'account has been opened',
            'For assistance', 'monthly statement', 'login', 'password', 'verification'
        ]
        
        msg_lower = message.lower()
        for keyword in skip_keywords:
            if keyword.lower() in msg_lower:
                return False
        
        # Check if it's a transaction message
        keywords = [
            'credited', 'debited', 'sent from', 'sent to', 'used for', 'pkr', 'rs.', 'easypaisa',
            'payment', 'withdrawal', 'transfer', 'received', 'added', 'transferred', 'trx id',
            'fee:',
            'e-statement', 'estatement', 'statement', 'mini statement', 'account statement',
            'transaction history', 'fund transfer', 'balance alert', 'your bahl', 'a/c',
        ]

        return any(kw in msg_lower for kw in keywords)
    
    @classmethod
    def parse_sms(cls, message, sender='BAHL', skip_keyword_gate=False):
        """
        Main parsing method
        
        Returns:
            dict: Transaction data or None if not a transaction SMS
        """
        try:
            # Clean message
            message = message.strip()
            
            # Check if it's a transaction SMS
            if not skip_keyword_gate and not cls.is_transaction_sms(message):
                logger.info("SMS is not a transaction message, skipping")
                return None

            msg_lower = message.lower()

            # Prefer debit when the text clearly describes money leaving the account.
            # Credit patterns are tried first in the legacy path; that mis-classifies some spends as income.
            debit_first = False
            if "debited" in msg_lower:
                debit_first = True
            elif re.search(r"sent\s+to\s+.+\s+from\s+your\b", message, re.IGNORECASE | re.DOTALL):
                debit_first = True
            elif (
                "sent to" in msg_lower
                and "credited" not in msg_lower
                and "received from" not in msg_lower
                and not re.search(r"sent\s+to\s+your\b", msg_lower)
            ):
                debit_first = True

            if debit_first:
                transaction_data = cls.parse_debit_transaction(message)
                if transaction_data:
                    logger.info(
                        "Parsed DEBIT transaction (debit-first): Rs. %s",
                        transaction_data["amount"],
                    )
                    return transaction_data

            # Try to parse as credit transaction
            transaction_data = cls.parse_credit_transaction(message)
            if transaction_data:
                logger.info(f"Parsed CREDIT transaction: Rs. {transaction_data['amount']}")
                return transaction_data
            
            # Try to parse as debit transaction
            transaction_data = cls.parse_debit_transaction(message)
            if transaction_data:
                logger.info(f"Parsed DEBIT transaction: Rs. {transaction_data['amount']}")
                return transaction_data
            
            logger.warning(f"Could not parse transaction from SMS: {message[:100]}...")
            return None
            
        except Exception as e:
            logger.error(f"Error parsing SMS: {e}")
            return None


def generate_sms_hash(message_data):
    """SHA256 of: sender|message|date"""
    sender = str(message_data.get('sender', 'UNKNOWN')).upper()
    message = str(message_data.get('message', '')).strip()
    date = str(message_data.get('date', ''))
    
    hash_string = f"{sender}|{message}|{date}"
    return hashlib.sha256(hash_string.encode()).hexdigest()


def generate_transaction_hash(transaction_data):
    """SHA256 of: date|amount|type|source"""
    if hasattr(transaction_data['date'], 'isoformat'):
        date_iso = transaction_data['date'].isoformat()
    else:
        date_iso = str(transaction_data['date'])
        
    amount_str = f"{float(transaction_data['amount']):.2f}"
    tx_type = str(transaction_data['type'])
    source = str(transaction_data.get('source', 'sms'))
    
    hash_string = f"{date_iso}|{amount_str}|{tx_type}|{source}"
    return hashlib.sha256(hash_string.encode()).hexdigest()


# PKR / Rs amounts in bank alerts (aligned with Android BankNotificationParser).
MONEY_AMOUNT_RE = re.compile(
    r"(?:PKR|Rs\.?|Rs)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:PKR|Rs\.?)\b",
    re.IGNORECASE,
)

_CREDIT_SIGNALS = re.compile(
    r"\bcredited\b|received\s+from|received\s+in|you(?:'ve)?\s+received|\bdeposit(?:ed)?\b|payment\s+received|"
    r"money\s+received|credited\s+to\s+your|deposited\s+to\s+your|\bincoming\b|incoming\s+funds|"
    r"\bibft\b|\bi\.?f\.?t\.?\b|\bfunds\s+received\b|\bcredit\s+alert\b|\bsalary\b|\bpayroll\b|\bwages\b|"
    r"\bamount\s+received\b|\bpayment\s+by\b",
    re.IGNORECASE,
)

_DEBIT_SIGNALS = re.compile(
    r"\bdebited\b|sent\s+to\b.+?from\s+your\s+(?:bahl|a/c|account|iban)|"
    r"(?:pkr|rs\.?)\s*[\d,]+(?:\.\d+)?\s+sent\s+to\b|fund\s+transfer.{0,120}\bdebit\b|"
    r"easypaisa\s+rs.+?sent\s+to\b|\bpurchase\b|\bwithdraw|\braast\b.{0,80}\bsent\b|"
    r"\bpaid\s+to\b|\bmoney\s+sent\b|\byou\s+sent\b|\bpayment\s+to\b|\btxn\s+debited\b|"
    r"\bpurchase\s+at\b|\bpos\b|\batm\s+withdraw",
    re.IGNORECASE,
)


def extract_money_amount_from_text(text: str | None) -> float | None:
    """First plausible PKR/Rs amount in text (notification bodies often list the txn amount first)."""
    if not text or not str(text).strip():
        return None
    for m in MONEY_AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if v > 0:
            return v
    return None


def text_contains_money_amount(text: str | None) -> bool:
    return extract_money_amount_from_text(text) is not None


def infer_transaction_kind_from_text(text: str | None) -> str | None:
    """Return 'credit' or 'debit' using the same cues as the Android parser (+ extras)."""
    if not text or not str(text).strip():
        return None
    s = str(text).lower()
    c_hit = _CREDIT_SIGNALS.search(s)
    d_hit = _DEBIT_SIGNALS.search(s)
    if d_hit and not c_hit:
        return "debit"
    if c_hit and not d_hit:
        return "credit"
    if d_hit and c_hit:
        if "debited" in s or re.search(r"\bsent\s+to\b", s):
            return "debit"
        if "credited" in s or "received from" in s or "received in" in s:
            return "credit"
        return "debit"
    return None


def build_minimal_notification_transaction_data(
    message: str,
    *,
    source: str,
    notification_parse_hint: dict | None,
) -> dict | None:
    """
    Last-resort parse for device notifications when regex SMS parsers miss (still updates balances).
    """
    hint = notification_parse_hint if isinstance(notification_parse_hint, dict) else None
    amt = None
    if hint:
        try:
            amt = float(hint.get("amount"))
        except (TypeError, ValueError):
            amt = None
    if amt is None or amt <= 0:
        amt = extract_money_amount_from_text(message)
    if amt is None or amt <= 0:
        return None

    ttype = None
    if hint:
        ttype = (hint.get("type") or hint.get("transaction_type") or "").strip().lower()
    if ttype not in ("debit", "credit"):
        ttype = infer_transaction_kind_from_text(message)
    if ttype not in ("debit", "credit"):
        return None

    cp = None
    if hint:
        cp = (hint.get("counterparty") or "").strip() or None
    dt = _hint_datetime_from_notification(
        hint.get("timestamp_iso") if hint else None,
        hint.get("post_time_ms") if hint else None,
    )
    snippet = (message or "").strip()
    if len(snippet) > 220:
        snippet = snippet[:217] + "..."

    if ttype == "debit":
        return {
            "type": "debit",
            "amount": amt,
            "receiver": cp or "Transfer",
            "sender": "App notification",
            "purpose": "Uncategorized",
            "date": dt,
            "notes": snippet or "Recorded from bank/wallet notification",
            "source": source,
        }

    return {
        "type": "credit",
        "amount": amt,
        "sender": cp or "Transfer",
        "receiver": None,
        "purpose": "Uncategorized",
        "date": dt,
        "notes": snippet or "Recorded from bank/wallet notification",
        "source": source,
    }


def notification_hint_is_usable(hint: dict | None) -> bool:
    if not hint or not isinstance(hint, dict):
        return False
    try:
        if float(hint.get("amount")) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    ttype = (hint.get("type") or hint.get("transaction_type") or "").strip().lower()
    return ttype in ("debit", "credit")


def _hint_datetime_from_notification(timestamp_iso: object, post_time_ms: object) -> datetime:
    if timestamp_iso:
        s = str(timestamp_iso).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    if post_time_ms is not None:
        try:
            ms = float(post_time_ms)
            return datetime.utcfromtimestamp(ms / 1000.0)
        except (TypeError, ValueError, OSError):
            pass
    return datetime.utcnow()


def transaction_data_from_notification_hint(
    message: str,
    hint: dict,
    *,
    source: str,
) -> dict | None:
    try:
        amt = float(hint.get("amount"))
    except (TypeError, ValueError):
        return None
    if amt <= 0:
        return None

    ttype = (hint.get("type") or hint.get("transaction_type") or "").strip().lower()
    if ttype not in ("debit", "credit"):
        ttype = infer_transaction_kind_from_text(message)
    if ttype not in ("debit", "credit"):
        return None

    cp = (hint.get("counterparty") or "").strip() or None
    dt = _hint_datetime_from_notification(hint.get("timestamp_iso"), hint.get("post_time_ms"))
    snippet = (message or "").strip()
    if len(snippet) > 220:
        snippet = snippet[:217] + "..."

    if ttype == "debit":
        return {
            "type": "debit",
            "amount": amt,
            "receiver": cp or "Transfer",
            "sender": "App notification",
            "purpose": "Uncategorized",
            "date": dt,
            "notes": snippet or "Recorded from bank/wallet notification",
            "source": source,
        }

    return {
        "type": "credit",
        "amount": amt,
        "sender": cp or "Transfer",
        "receiver": None,
        "purpose": "Uncategorized",
        "date": dt,
        "notes": snippet or "Recorded from bank/wallet notification",
        "source": source,
    }


def process_bank_sms(
    message,
    sender='BAHL',
    external_sms_hash=None,
    transaction_source_override=None,
    balance_source_override=None,
    notification_parse_hint=None,
    notification_relaxed_gate=False,
):
    """
    Process a bank SMS and save as transaction with robust deduplication
    
    Args:
        message (str): SMS message text
        sender (str): SMS sender (default: 'BAHL')
    
    Returns:
        tuple: (Transaction, is_new) where is_new is True if created, False if duplicate
    """
    try:
        usable_native_hint = notification_hint_is_usable(notification_parse_hint)
        is_notification_source = (transaction_source_override or "").strip().lower() == "notification"
        skip_kw = bool(is_notification_source and notification_relaxed_gate)
        msg_ok = bool((message or "").strip())
        if not msg_ok:
            return None, False
        # Notifications often fail keyword gate / regex parse — allow ingest-side relaxed parsing.
        if (
            not BankAlhabibSMSParser.is_transaction_sms(message)
            and not usable_native_hint
            and not skip_kw
        ):
            logger.info("SMS is not a transaction message, skipping")
            return None, False

        # Notifications: prefer native debit/credit hint so we don't mis-classify (regex tries credit first).
        transaction_data = None
        if is_notification_source and usable_native_hint:
            src = transaction_source_override or "sms"
            transaction_data = transaction_data_from_notification_hint(
                message,
                notification_parse_hint,
                source=src,
            )
            if transaction_data:
                logger.info(
                    "Notification (native hint): %s PKR %.2f",
                    transaction_data["type"],
                    transaction_data["amount"],
                )

        if not transaction_data:
            transaction_data = BankAlhabibSMSParser.parse_sms(
                message, sender, skip_keyword_gate=skip_kw
            )

        if not transaction_data and notification_parse_hint:
            src = transaction_source_override or "sms"
            transaction_data = transaction_data_from_notification_hint(
                message,
                notification_parse_hint,
                source=src,
            )
            if transaction_data:
                logger.info(
                    "Used notification native parse fallback: %s PKR %.2f",
                    transaction_data["type"],
                    transaction_data["amount"],
                )

        if not transaction_data and is_notification_source and notification_relaxed_gate:
            transaction_data = build_minimal_notification_transaction_data(
                message,
                source=transaction_source_override or "notification",
                notification_parse_hint=notification_parse_hint,
            )
            if transaction_data:
                logger.info(
                    "Notification minimal fallback: %s PKR %.2f",
                    transaction_data["type"],
                    transaction_data["amount"],
                )

        if not transaction_data:
            logger.warning(f"Could not parse transaction from SMS: {message[:100]}")
            return None, False
        
        if transaction_source_override:
            transaction_data['source'] = transaction_source_override

        # Generate hashes
        sms_hash = external_sms_hash or generate_sms_hash({
            'sender': sender,
            'message': message,
            'date': transaction_data['date'].isoformat()
        })
        
        transaction_hash = generate_transaction_hash(transaction_data)
    # 1. Check SMS Hash (Exact Match)
        print(f"🔍 [SMS DEBUG]\n   Msg: {message[:30]}...\n   Date: {transaction_data['date']}\n   Generated Hash: {sms_hash}")
        
        existing_by_sms = Transaction.query.filter_by(sms_hash=sms_hash).first()
        if existing_by_sms:
            print(f"   ❌ DUPLICATE by SMS SMS Hash! (ID: {existing_by_sms.id})")
            return existing_by_sms, False

        # 2. Check Transaction Hash (Content Match)
        tx_hash = transaction_hash # Already generated above
        print(f"   Transaction Hash: {tx_hash}")

        existing_by_tx = Transaction.query.filter_by(transaction_hash=tx_hash).first()
        if existing_by_tx:
            print(f"   ❌ DUPLICATE by Transaction Hash! (ID: {existing_by_tx.id})")
            # Update metadata if missing
            if not existing_by_tx.sms_hash:
                print("   ✏️ Updating missing SMS hash on existing record.")
                existing_by_tx.sms_hash = sms_hash
                db.session.commit()
            return existing_by_tx, False
            
        # Check 3: Same calendar day + amount + type — legacy SMS fallback only.
        # Notifications always carry external_sms_hash; without it this matcher is too aggressive
        # (e.g. two Rs.10 debits same day → wrong row, Uncategorized stays empty).
        if not external_sms_hash and not is_notification_source:
            tx_date = transaction_data['date']
            similar_tx = Transaction.query.filter(
                db.func.date(Transaction.date) == tx_date.date(),
                Transaction.amount == transaction_data['amount'],
                Transaction.type == transaction_data['type']
            ).first()

            if similar_tx:
                logger.info(
                    "Similar transaction found: Date=%s, Amount=%s",
                    tx_date.date(),
                    transaction_data['amount'],
                )

                if not similar_tx.sms_hash:
                    similar_tx.sms_hash = sms_hash
                if not similar_tx.transaction_hash:
                    similar_tx.transaction_hash = transaction_hash

                db.session.commit()
                return similar_tx, False

        # Check 4: Legacy transaction_id check (for backward compatibility)
        message_hash = hashlib.md5(message.encode()).hexdigest()[:16]
        legacy_id = f"sms_{message_hash}"
        
        existing_by_legacy = Transaction.query.filter_by(transaction_id=legacy_id).first()
        if existing_by_legacy:
            logger.info(f"Duplicate found by legacy ID: {legacy_id}")
            
            # Update with new hashes
            if not existing_by_legacy.sms_hash:
                existing_by_legacy.sms_hash = sms_hash
            if not existing_by_legacy.transaction_hash:
                existing_by_legacy.transaction_hash = transaction_hash
            
            db.session.commit()
            return existing_by_legacy, False
        
        # ============================================
        # CREATE NEW TRANSACTION
        # ============================================
        
        # Add hashes to transaction data
        transaction_data['sms_hash'] = sms_hash
        transaction_data['transaction_hash'] = transaction_hash
        transaction_data['transaction_id'] = legacy_id  # Keep for backward compatibility
        transaction_data['categorization_status'] = 'pending'
        
        target_source = resolve_balance_slug_for_ingest(
            sender, message, balance_source_override, transaction_data
        )
        # Persist on INSERT so balance_applied is reliably 0 (post-flush assign can miss on some SA/SQLite paths).
        transaction_data["account_balance_source"] = target_source
        transaction_data["balance_applied"] = False

        # Create transaction (wallet balance updates when user categorizes — ledger_sync)
        transaction = Transaction(**transaction_data)
        db.session.add(transaction)
        db.session.flush()  # Get the ID

        accounts_all = AccountBalance.query.all()
        try:
            from transfer_matching import maybe_mark_own_account_transfer

            maybe_mark_own_account_transfer(
                transaction,
                accounts_all,
                balance_slug=target_source,
            )
        except Exception as tm_err:
            logger.warning("transfer_matching skipped: %s", tm_err)

        transaction.categorization_status = "pending"
        transaction.category_id = None
        transaction.account_balance_source = target_source
        transaction.balance_applied = False
        ensure_account_balance_row(target_source)
        try:
            apply_pending_transaction_ledger(transaction, respect_manual_lock=False)
        except Exception as led_err:
            logger.warning("ingest ledger apply skipped: %s", led_err)

        db.session.commit()
        
        logger.info(f"✅ Created transaction {transaction.id}: {transaction.type} Rs.{transaction.amount}")
        try:
            from transfer_matching import is_own_account_transfer_row

            log_wallet_attribution(
                "INGEST_TXN_COMMITTED",
                transaction_id=transaction.id,
                resolved_wallet_slug=target_source,
                txn_type=transaction.type,
                amount=transaction.amount,
                purpose=getattr(transaction, "purpose", None),
                self_transfer=is_own_account_transfer_row(transaction),
                txn_sender=getattr(transaction, "sender", None),
                txn_receiver=getattr(transaction, "receiver", None),
                sms_or_notif_source=str(transaction_data.get("source") or ""),
            )
        except Exception as log_err:
            logger.warning("wallet attribution log skipped: %s", log_err)
        return transaction, True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error processing SMS transaction: {e}")
        import traceback
        traceback.print_exc()
        return None, False


# ============================================
# UTILITY FUNCTIONS
# ============================================

def check_duplicate_transaction(amount, date, tx_type):
    """
    Check if a similar transaction already exists
    """
    return Transaction.query.filter(
        db.func.date(Transaction.date) == date.date(),
        Transaction.amount == amount,
        Transaction.type == tx_type
    ).first()


def get_transactions_by_hash(sms_hash=None, transaction_hash=None):
    """
    Retrieve transactions by hash
    """
    query = Transaction.query
    
    if sms_hash:
        query = query.filter_by(sms_hash=sms_hash)
    
    if transaction_hash:
        query = query.filter_by(transaction_hash=transaction_hash)
    
    return query.all()


def find_duplicate_transactions():
    """
    Find all duplicate transactions in the database
    """
    from sqlalchemy import func
    
    # Find duplicates by transaction_hash
    duplicates = db.session.query(
        Transaction.transaction_hash,
        func.count(Transaction.id).label('count')
    ).filter(
        Transaction.transaction_hash != None
    ).group_by(
        Transaction.transaction_hash
    ).having(
        func.count(Transaction.id) > 1
    ).all()
    
    duplicate_details = []
    for dup_hash, count in duplicates:
        transactions = Transaction.query.filter_by(transaction_hash=dup_hash).all()
        duplicate_details.append({
            'hash': dup_hash[:16],
            'count': count,
            'transactions': [
                {
                    'id': tx.id,
                    'amount': tx.amount,
                    'date': tx.date.isoformat(),
                    'type': tx.type,
                    'source': tx.source
                }
                for tx in transactions
            ]
        })
    
    return {
        'total_duplicate_groups': len(duplicates),
        'total_duplicate_transactions': sum(dup[1] - 1 for dup in duplicates),
        'details': duplicate_details
    }