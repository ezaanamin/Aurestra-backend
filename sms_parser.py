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
import logging

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

    # Sent To Pattern (New Format)
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
        keywords = ['credited', 'debited', 'sent from', 'used for', 'pkr', 'rs.', 'payment', 'withdrawal', 'transfer', 'received', 'added', 'transferred']
        
        return any(kw in msg_lower for kw in keywords)
    
    @classmethod
    def parse_sms(cls, message, sender='BAHL'):
        """
        Main parsing method
        
        Returns:
            dict: Transaction data or None if not a transaction SMS
        """
        try:
            # Clean message
            message = message.strip()
            
            # Check if it's a transaction SMS
            if not cls.is_transaction_sms(message):
                logger.info("SMS is not a transaction message, skipping")
                return None
            
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


def process_bank_sms(message, sender='BAHL', external_sms_hash=None):
    """
    Process a bank SMS and save as transaction with robust deduplication
    
    Args:
        message (str): SMS message text
        sender (str): SMS sender (default: 'BAHL')
    
    Returns:
        tuple: (Transaction, is_new) where is_new is True if created, False if duplicate
    """
    try:
        # Check if it's a transaction SMS
        if not BankAlhabibSMSParser.is_transaction_sms(message):
            logger.info("SMS is not a transaction message, skipping")
            return None, False
        
        # Parse SMS
        transaction_data = BankAlhabibSMSParser.parse_sms(message, sender)
        
        if not transaction_data:
            logger.warning(f"Could not parse transaction from SMS: {message[:100]}")
            return None, False
        
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
            
        # Check 3: By date + amount + type (fallback for transactions without hash)
        tx_date = transaction_data['date']
        similar_tx = Transaction.query.filter(
            db.func.date(Transaction.date) == tx_date.date(),
            Transaction.amount == transaction_data['amount'],
            Transaction.type == transaction_data['type']
        ).first()
        
        if similar_tx:
            logger.info(f"Similar transaction found: Date={tx_date.date()}, Amount={transaction_data['amount']}")
            
            # Update hashes on existing transaction
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
        transaction_data['source'] = 'bank_sms'
        transaction_data['categorization_status'] = 'pending'
        
        # Create transaction
        transaction = Transaction(**transaction_data)
        db.session.add(transaction)
        db.session.flush()  # Get the ID
        
        # ============================================
        # UPDATE ACCOUNT BALANCE
        # ============================================
        
        # Determine which account to update
        target_source = 'bank' if sender in ['BAHL', 'BankALHabib', 'AL-Habib', '8810', '8812'] else 'sms'
        
        balance = AccountBalance.query.filter_by(source=target_source).first()
        if not balance:
            balance = AccountBalance(source=target_source, current_balance=0.0)
            db.session.add(balance)
        
        # Update balance (Only if is_new=True, which is implied since we are here)
        if transaction.type == 'credit':
            balance.current_balance += transaction.amount
            logger.info(f"Credit: +{transaction.amount} → {balance.current_balance}")
        else:
            balance.current_balance -= transaction.amount
            logger.info(f"Debit: -{transaction.amount} → {balance.current_balance}")
        
        balance.last_updated = datetime.now()
        
        # Commit everything
        db.session.commit()
        
        logger.info(f"✅ Created transaction {transaction.id}: {transaction.type} Rs.{transaction.amount}")
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