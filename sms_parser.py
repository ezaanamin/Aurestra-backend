"""
Bank Alhabib SMS Parser
=======================
Parses Bank Alhabib SMS messages and extracts transaction details
"""

import re
from datetime import datetime
from model import Transaction
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


def process_bank_sms(message, sender='BAHL'):
    """
    Process a bank SMS and save as transaction
    
    Args:
        message (str): SMS message text
        sender (str): SMS sender (default: 'BAHL')
    
    Returns:
        Transaction: Created transaction or None
    """
    try:
        # Parse SMS
        transaction_data = BankAlhabibSMSParser.parse_sms(message, sender)
        
        if not transaction_data:
            return None, False
        
        # Create robust hash for deduplication (Sender + Date + Amount + Type)
        import hashlib
        
        # Format date consistently to ISO 8601 for hashing
        date_iso = transaction_data['date'].isoformat()
        amount_str = f"{transaction_data['amount']:.2f}"
        
        # Hash Source string: "BAHL|2026-01-20T10:00:00|500.00|debit"
        hash_payload = f"{sender}|{date_iso}|{amount_str}|{transaction_data['type']}"
        transaction_hash = hashlib.sha256(hash_payload.encode()).hexdigest()
        
        transaction_data['transaction_hash'] = transaction_hash
        # Use a more descriptive source for Bank Alhabib SMS
        transaction_data['source'] = 'bank_sms' if sender in ['BAHL', 'BankALHabib', 'AL-Habib', '8810', '8812'] else 'sms'
        
        # Legacy ID for backward compatibility: MD5 of message body
        message_hash = hashlib.md5(message.encode()).hexdigest()[:16]
        transaction_data['transaction_id'] = f"sms_{message_hash}"
        
        # 1. Check by Hash (Strongest Check)
        existing_hash = Transaction.query.filter_by(transaction_hash=transaction_hash).first()
        if existing_hash:
            logger.info(f"Duplicate Transaction Hash found: {transaction_hash}")
            return existing_hash, False

        # 2. Check by Legacy ID (Secondary Check)
        existing_id = Transaction.query.filter_by(
            transaction_id=transaction_data['transaction_id']
        ).first()
        
        if existing_id:
            logger.info(f"SMS transaction already exists (Legacy ID): {message_hash}")
            # Update hash if missing?
            if not existing_id.transaction_hash:
                existing_id.transaction_hash = transaction_hash
                db.session.commit()
            return existing_id, False
        
        # Create new transaction
        transaction = Transaction(**transaction_data)
        db.session.add(transaction)
        
        # Update Account Balance
        from model import AccountBalance
        # NOTE: SMS transactions for Bank Alhabib should update the 'bank' balance
        target_source = 'bank' if sender in ['BAHL', 'BankALHabib', 'AL-Habib', '8810', '8812'] else 'sms'
        balance = AccountBalance.query.filter_by(source=target_source).first()
        if not balance:
            balance = AccountBalance(source=target_source, current_balance=0.0)
            db.session.add(balance)
            
        if transaction.type == 'credit':
            balance.current_balance += transaction.amount
        else:
            balance.current_balance -= transaction.amount
            
        db.session.commit()
        
        logger.info(f"SMS transaction created: {transaction.id} - {transaction.type} Rs. {transaction.amount}")
        return transaction, True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error processing SMS transaction: {e}")
        return None, False


