from database import db
from datetime import datetime
from sqlalchemy.orm import validates
from sqlalchemy.types import TypeDecorator, String, Text
from flask import has_app_context, g
from utils.crypto_helpers import encrypt_field, decrypt_field

class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not has_app_context():
            return value
        key = getattr(g, 'encryption_key', None)
        if key and value is not None:
            return encrypt_field(value, key)
        return value

    def process_result_value(self, value, dialect):
        if not has_app_context():
            return value
        key = getattr(g, 'encryption_key', None)
        if value is not None:
            return decrypt_field(value, key)
        return value

class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not has_app_context():
            return value
        key = getattr(g, 'encryption_key', None)
        if key and value is not None:
            return encrypt_field(value, key)
        return value

    def process_result_value(self, value, dialect):
        if not has_app_context():
            return value
        key = getattr(g, 'encryption_key', None)
        if value is not None:
            return decrypt_field(value, key)
        return value

class MonthlyBalance(db.Model):
    """
    Stores the monthly summary of balances for bank + wallet combined.
    Includes calculated fields for expense and net savings. Budget data 
    is managed separately in the Budget model.
    """
    __tablename__ = "monthly_balances"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    source = db.Column(db.String(20), nullable=False)  # e.g., "combined"
    month = db.Column(db.String(7), nullable=False)    # Format: "YYYY-MM"
    
    __table_args__ = (db.UniqueConstraint('user_id', 'month', name='uq_monthly_balance_user_month'),)
    
    # Balance Information
    opening_balance = db.Column(db.Float, nullable=False)
    closing_balance = db.Column(db.Float, nullable=False)
    
    # Financial Performance Metrics
    expense = db.Column(db.Float, nullable=True)       # Total expense (money out, always non-negative)
    savings = db.Column(db.Float, nullable=True)       # Net savings/income (money in, always non-negative)
    
    # Metadata
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<MonthlyBalance {self.month} | "
            f"Open: {self.opening_balance:.2f} | "
            f"Close: {self.closing_balance:.2f} | "
            f"Expense: {self.expense:.2f} | "
            f"Savings: {self.savings:.2f}>"
        )



class Transaction(db.Model):
    """
    Stores individual transactions from bank or wallet emails.
    """
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    source = db.Column(db.String(20), nullable=False)
    date = db.Column(db.DateTime, nullable=False)

    # Category (prints, gym, food, research paper etc.)
    purpose = db.Column(EncryptedString(255), nullable=True)

    # Amount received
    amount = db.Column(db.Float, nullable=False)

    # Sender / Who actually sent the money
    sender = db.Column(EncryptedString(255), nullable=True)

    # NEW — Receiver (Hiba Dawood, Haris Masood, Umair etc.)
    receiver = db.Column(EncryptedString(255), nullable=True)

    # NEW — unique easypaisa transaction ID for duplicate protection
    transaction_id = db.Column(db.String(50), nullable=True)
    
    # NEW — robust hash for deduplication
    transaction_hash = db.Column(db.String(64), nullable=True)
    
    # NEW — SMS hash for deduplication (added via migration)
    sms_hash = db.Column(db.String(64), nullable=True, index=True)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'transaction_id', name='uq_transaction_user_id'),
        db.UniqueConstraint('user_id', 'transaction_hash', name='uq_transaction_user_hash'),
    )

    # Optional extra details (store name, bank name etc.)
    notes = db.Column(EncryptedString(255), nullable=True)

    # NEW — type: credit or debit
    type = db.Column(db.String(10), nullable=False)
    
    # NEW — Categorization tracking
    categorization_status = db.Column(db.String(20), default='pending')  # 'pending', 'categorized', 'auto', 'manual'
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)  # Proper FK relationship

    # Wallet slug for balance (e.g. bank, easypaisa); ledger applied when user categorizes if balance_applied is False
    account_balance_source = db.Column(db.String(64), nullable=True)
    balance_applied = db.Column(db.Boolean, nullable=False, default=True)

    # NEW - linking to an uploaded receipt
    receipt_id = db.Column(db.Integer, db.ForeignKey('uploaded_receipts.id'), nullable=True)

    is_deleted = db.Column(db.Boolean, default=False)
    is_spam = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @staticmethod
    def generate_deterministic_hash(data):
        """
        Generates a robust hash for deduplication based on transaction details.
        """
        import hashlib, re
        # Fields to include in hash: date, amount, type, and identity (notes or ID)
        date_val = data.get('date')
        if isinstance(date_val, datetime):
            date_part = date_val.strftime('%Y-%m-%d')
        else:
            date_part = str(date_val)
            
        amount_part = f"{float(data.get('amount', 0)):.2f}"
        type_part = str(data.get('type', '')).lower()
        
        # Identity part: Prefer transaction_id, then notes
        identity = data.get('transaction_id') or data.get('notes') or data.get('description') or ""
        identity_part = re.sub(r'[^a-zA-Z0-9]', '', str(identity)).lower()[:50]
        
        raw_string = f"{date_part}|{amount_part}|{type_part}|{identity_part}"
        return hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "date": self.date.isoformat() if self.date else None,
            "purpose": self.purpose,
            "amount": self.amount,
            "sender": self.sender,
            "receiver": self.receiver,
            "transaction_id": self.transaction_id,
            "transaction_hash": self.transaction_hash,
            "sms_hash": self.sms_hash,
            "notes": self.notes,
            "type": self.type,
            "categorization_status": self.categorization_status,
            "category_id": self.category_id,
            "account_balance_source": self.account_balance_source,
            "balance_applied": self.balance_applied,
            "receipt_id": self.receipt_id,
            "is_deleted": self.is_deleted,
            "is_spam": self.is_spam,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


    def __repr__(self):
        return f"<Transaction {self.source} | {self.amount} | {self.date} | {self.sender} → {self.receiver}>"

class SMSHistory(db.Model):
    __tablename__ = 'sms_history'

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    device_sms_id = db.Column(db.String(100))  # Unique SMS ID from device
    sender = db.Column(db.String(50))
    body = db.Column(db.Text)
    device_timestamp = db.Column(db.DateTime)
    sms_hash = db.Column(db.String(64), nullable=False)  # Deterministic hash
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'sms_hash', name='uq_sms_history_user_hash'),)

    def to_dict(self):
        return {
            'id': self.id,
            'device_sms_id': self.device_sms_id,
            'sender': self.sender,
            'body': self.body,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class UploadedReceipt(db.Model):
    __tablename__ = 'uploaded_receipts'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(512), nullable=False)
    mime_type = db.Column(db.String(50))
    
    ocr_status = db.Column(db.String(20), default='pending') # pending, completed, failed
    ocr_raw_text = db.Column(EncryptedText, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'filename': self.filename,
            'ocr_status': self.ocr_status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class DeviceNotification(db.Model):
    """Android notification captured by the device listener (all apps, user-scoped)."""

    __tablename__ = "device_notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    notification_key = db.Column(db.String(512), nullable=True)
    dedupe_hash = db.Column(db.String(64), nullable=False)
    package_name = db.Column(db.String(255), nullable=True)
    title = db.Column(db.Text, nullable=True)
    body = db.Column(db.Text, nullable=True)
    combined_message = db.Column(db.Text, nullable=True)
    post_time_ms = db.Column(db.BigInteger, nullable=True)
    messaging_style_json = db.Column(db.Text, nullable=True)
    client_parsed_json = db.Column(db.Text, nullable=True)
    is_transactional = db.Column(db.Boolean, default=False)
    parse_attempted = db.Column(db.Boolean, default=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "dedupe_hash", name="uq_device_notif_user_dedupe"),)

    def to_dict(self):
        import json

        msg_lines = []
        if self.messaging_style_json:
            try:
                msg_lines = json.loads(self.messaging_style_json)
            except (json.JSONDecodeError, TypeError):
                msg_lines = []

        cp = None
        if self.client_parsed_json:
            try:
                cp = json.loads(self.client_parsed_json)
            except (json.JSONDecodeError, TypeError):
                cp = None

        return {
            "id": self.id,
            "notification_key": self.notification_key,
            "dedupe_hash": self.dedupe_hash,
            "package_name": self.package_name,
            "title": self.title,
            "body": self.body,
            "combined_message": self.combined_message,
            "post_time_ms": self.post_time_ms,
            "messaging_lines": msg_lines if isinstance(msg_lines, list) else [],
            "client_parsed": cp,
            "is_transactional": bool(self.is_transactional),
            "transaction_id": self.transaction_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Budget(db.Model):

    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    month = db.Column(db.String(7), nullable=False)  # Format: "YYYY-MM"
    total_budget = db.Column(db.Float, nullable=False)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'month', name='uq_budget_user_month'),)

    # Spending breakdown
    needs = db.Column(db.Float, nullable=False, default=0.0)
    wants = db.Column(db.Float, nullable=False, default=0.0)
    saving = db.Column(db.Float, nullable=False, default=0.0)

    # Calculated actual expenses for this month (updated on every /api/expenses/total call)
    total_expenses = db.Column(db.Float, nullable=False, default=0.0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Budget {self.month} | Total: {self.total_budget} | Expenses: {self.total_expenses}>"


class AccountBalance(db.Model):
    """
    Stores current balances for each account/wallet (multiple rows allowed).
    `source` is a stable slug used as Transaction.source and in APIs (unique).
    """
    __tablename__ = "account_balances"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Stable machine id / slug (matches Transaction.source), e.g. bank, jazzcash, hbl_main
    source = db.Column(db.String(64), nullable=False)

    display_name = db.Column(db.String(120), nullable=False, default="")
    
    __table_args__ = (db.UniqueConstraint('user_id', 'source', name='uq_account_balance_user_source'),)
    # Legal / preferred account-holder name as printed by the bank (not the institution label).
    holder_name = db.Column(db.String(160), nullable=False, default="")
    # bank | mobile_wallet | cash | digital_bank
    account_kind = db.Column(db.String(40), nullable=False, default="bank")
    # JSON array of strings, matched case-insensitively against notification title/body
    match_keywords = db.Column(db.Text, nullable=False, default="[]")
    accent_color = db.Column(db.String(24), nullable=False, default="#6366F1")
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    current_balance = db.Column(db.Float, nullable=False, default=0.0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @validates('current_balance')
    def _clamp_balance(self, key, value):
        """Never allow a negative balance to be stored — floor at 0.0."""
        return max(0.0, float(value or 0.0))
    is_manual = db.Column(db.Boolean, default=False)
    # JSON array of digit strings (full or partial account numbers) to match e-statement PDF text
    statement_account_numbers = db.Column(db.Text, nullable=True)

    KIND_LABELS = {
        "bank": "BANK ACCOUNT",
        "mobile_wallet": "MOBILE WALLET",
        "cash": "PHYSICAL CASH",
        "digital_bank": "DIGITAL BANK",
    }

    def __repr__(self):
        return f"<AccountBalance {self.source} | {self.display_name} | {self.current_balance:.2f}>"

    def kind_label(self) -> str:
        return self.KIND_LABELS.get((self.account_kind or "").strip(), "ACCOUNT")

    def keywords_list(self):
        import json

        try:
            data = json.loads(self.match_keywords or "[]")
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def statement_account_numbers_list(self):
        import json

        raw = self.statement_account_numbers
        if not raw or not str(raw).strip():
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "display_name": self.display_name or self.source,
            "holder_name": (self.holder_name or "").strip(),
            "account_kind": self.account_kind or "bank",
            "kind_label": self.kind_label(),
            "match_keywords": self.keywords_list(),
            "accent_color": self.accent_color or "#6366F1",
            "sort_order": self.sort_order or 0,
            "balance": max(0.0, float(self.current_balance or 0.0)),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "is_manual": bool(self.is_manual),
            "statement_account_numbers": self.statement_account_numbers_list(),
        }


class SavingsGoal(db.Model):
    """
    Stores user savings goals (e.g., 'New Laptop', 'Emergency Fund').
    """
    __tablename__ = "savings_goals"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0)
    emoji = db.Column(db.String(20), default="💰")
    deadline = db.Column(db.Date, nullable=True)
    
    # Calculate remaining amount dynamically
    @property
    def remaining(self):
        return max(self.target_amount - self.current_amount, 0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "target_amount": self.target_amount,
            "current_amount": self.current_amount,
            "emoji": self.emoji,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "remaining": self.remaining
        }

class Category(db.Model):
    """
    Stores transaction categories.
    """
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    # NOTE: unique=True on 'name' intentionally kept until Phase 3 constraint update
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50), nullable=False, default="cash")
    
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='uq_category_user_name'),)
    color = db.Column(db.String(20), nullable=False, default="#64748B")
    cat_type = db.Column(db.String(20), nullable=False, default="spending") # 'spending', 'income', 'both'
    is_default = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "color": self.color,
            "cat_type": self.cat_type,
            "is_default": self.is_default
        }


class CategorizationRule(db.Model):
    """
    Stores user-defined rules for automatic categorization.
    Example: 'Netflix' always goes to 'Entertainment'
    """
    __tablename__ = "categorization_rules"
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Pattern matching (case-insensitive)
    merchant_pattern = db.Column(db.String(255), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hit_count = db.Column(db.Integer, default=0)  # Track how many times rule was applied
    
    # Relationships
    category = db.relationship('Category')
    user = db.relationship('User', backref='categorization_rules')
    
    def to_dict(self):
        return {
            "id": self.id,
            "merchant_pattern": self.merchant_pattern,
            "category": self.category.to_dict() if self.category else None,
            "hit_count": self.hit_count,
            "created_at": self.created_at.isoformat()
        }

    def __repr__(self):
        return f"<CategorizationRule '{self.merchant_pattern}' -> {self.category.name if self.category else 'None'}>"


class User(db.Model):
    """
    Stores user authentication and profile details.
    Supports both email/password and Google OAuth sign-in.
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)

    # ── Profile ───────────────────────────────────────────────
    full_name = db.Column(db.String(100), nullable=True)
    avatar_url = db.Column(db.String(512), nullable=True)

    # Central Auth API specific (kept for backward-compat)
    name = db.Column(db.String(100), nullable=True)
    profile_picture = db.Column(db.String(512), nullable=True)

    # ── Email / Password Auth ─────────────────────────────────
    # Null for Google-only accounts
    password_hash = db.Column(db.String(255), nullable=True)

    # ── Email Verification ────────────────────────────────────
    # Google users are pre-verified; email/password users must verify once.
    is_email_verified = db.Column(db.Boolean, default=False)
    email_verification_token = db.Column(db.String(64), nullable=True, unique=True)
    email_verification_sent_at = db.Column(db.DateTime, nullable=True)

    # ── Password Reset ────────────────────────────────────────
    password_reset_token = db.Column(db.String(64), nullable=True, unique=True)
    password_reset_expires_at = db.Column(db.DateTime, nullable=True)

    # ── Auth Method ───────────────────────────────────────────
    # 'email' | 'google' | 'both'
    auth_method = db.Column(db.String(20), default='google')

    # ── Google OAuth Fields ───────────────────────────────────
    google_id = db.Column(db.String(50), nullable=True)
    google_email = db.Column(db.String(120), nullable=True)
    google_refresh_token = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Preferences ───────────────────────────────────────────
    notifications_enabled = db.Column(db.Boolean, default=True)
    decryption_key = db.Column(db.String(255), nullable=True)
    decryption_key_hash = db.Column(db.String(255), nullable=True)
    decryption_key_salt = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "avatar_url": self.avatar_url,
            "notifications_enabled": self.notifications_enabled,
            "is_email_verified": bool(self.is_email_verified),
            "auth_method": self.auth_method or "google",
            "has_decryption_key": self.decryption_key_hash is not None,
        }



class DeviceToken(db.Model):
    """
    Stores Expo push notification tokens for devices.
    """
    __tablename__ = "device_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(255), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "token": self.token,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat()
        }


class FinancialInsight(db.Model):
    """
    Stores high-signal financial knowledge and monthly summaries.
    Long-term memory (RAG) format.
    """
    __tablename__ = "financial_insights"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    month = db.Column(db.String(7), nullable=False) # "YYYY-MM"
    
    # Natural Language Content (The 'Story')
    content = db.Column(EncryptedText, nullable=False)
    
    # Structured Metrics (for graphing/analysis later)
    # Stored as JSON string or use db.JSON if supported by all envs (SQLite supports json in recent versions, but Text is safest)
    metrics_json = db.Column(db.Text, nullable=True) 
    
    # Retrieval Tags (e.g., "savings_trend", "high_expenses")
    tags = db.Column(db.String(255), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json
        metrics = {}
        if self.metrics_json:
            try:
                metrics = json.loads(self.metrics_json)
            except:
                metrics = {}
                
        return {
            "id": self.id,
            "month": self.month,
            "content": self.content,
            "metrics": metrics,
            "tags": self.tags,
            "created_at": self.created_at.isoformat()
        }

class StatementAnalysis(db.Model):
    """
    Stores the exact calculations for E-Statement Analysis.
    This is what populates the E-Statement Modal in Transaction History.
    Populated by 'calculate_statement'.
    """
    __tablename__ = "statement_analysis"

    id = db.Column(db.Integer, primary_key=True)
    # Phase 2: user ownership (nullable for safe migration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    month = db.Column(db.String(7), nullable=False)  # "YYYY-MM"
    
    __table_args__ = (db.UniqueConstraint('user_id', 'month', name='uq_statement_analysis_user_month'),)
    
    opening_balance = db.Column(db.Float, default=0.0)
    closing_balance = db.Column(db.Float, default=0.0)
    
    total_income = db.Column(db.Float, default=0.0)
    total_expense = db.Column(db.Float, default=0.0)
    net_result = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.String(20)) # "Surplus", "Deficit"
    
    # Store JSON string for breakdown: {"income": {...}, "expenses": {...}}
    breakdown_json = db.Column(db.Text, nullable=True)
    
    analysis_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    # NEW: Balance application tracking
    balance_applied = db.Column(db.Boolean, default=False)  # Track if balance was applied to AccountBalance
    reviewed_at = db.Column(db.DateTime, nullable=True)  # Timestamp of first review
    statement_id = db.Column(db.String(64), nullable=True)  # Unique identifier (can be month or hash)
    
    # NEW: Linking transactions
    transaction_ids = db.Column(db.Text, nullable=True) # JSON list of transaction IDs
    
    # NEW: Processing status tracking
    processing_status = db.Column(db.String(20), default='success')  # 'success', 'partial', 'failed'
    processing_notes = db.Column(db.Text, nullable=True)  # Details about any parsing issues
    # account_balances.source slug that received closing balance from this statement
    account_balance_source = db.Column(db.String(64), nullable=True)

    def to_dict(self):
        import json
        breakdown = {}
        if self.breakdown_json:
            try:
                breakdown = json.loads(self.breakdown_json)
            except:
                pass
        
        # Current balance for the wallet this statement was mapped to (if known)
        if self.account_balance_source:
            account_balance_record = AccountBalance.query.filter_by(
                source=self.account_balance_source
            ).first()
        else:
            account_balance_record = AccountBalance.query.order_by(
                AccountBalance.last_updated.desc()
            ).first()
        current_account_balance = (
            account_balance_record.current_balance if account_balance_record else 0.0
        )
        
        # Check if closing balance matches account balance
        balance_matches = abs(self.closing_balance - current_account_balance) < 0.01  # Allow small floating point difference
                
        return {
            "target_month": self.month,
            "opening_balance": self.opening_balance,
            "closing_balance": self.closing_balance,
            "account_balance": current_account_balance,  # NEW: Current account balance
            "balance_matches": balance_matches,  # NEW: True if closing matches account
            "summary": {
                "income": self.total_income,
                "expenses": self.total_expense,
                "net": self.net_result,
                "status": self.status
            },
            "breakdown": breakdown,
            "analyzed_at": self.analysis_date.isoformat(),
            "balance_applied": self.balance_applied,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "statement_id": self.statement_id,
            "transaction_ids": json.loads(self.transaction_ids) if self.transaction_ids else [],
            "processing_status": self.processing_status,  # NEW
            "processing_notes": self.processing_notes,  # NEW
            "read_status": "read" if self.reviewed_at else "unread",  # NEW: Show if statement has been viewed
            "account_balance_source": self.account_balance_source,
        }


class UserBackup(db.Model):
    """
    Tracks metadata for each per-user encrypted backup file.
    File paths are stored server-side only and never exposed via the API.
    Encryption: AES-256-GCM keyed from the user's decryption key via PBKDF2.
    """
    __tablename__ = "user_backups"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    filename    = db.Column(db.String(255), nullable=False)
    file_path   = db.Column(db.String(512), nullable=False)  # server-side only
    size_bytes  = db.Column(db.Integer, nullable=False, default=0)
    app_version = db.Column(db.String(20), nullable=False, default='1.0.0')
    db_version  = db.Column(db.Integer, nullable=False, default=1)
    enc_version = db.Column(db.String(30), nullable=False, default='AES256GCM-v1')
    status      = db.Column(db.String(20), nullable=False, default='completed')  # completed | failed
    table_counts = db.Column(db.Text, nullable=True)  # JSON: {"transactions": 42, ...}
    checksum    = db.Column(db.String(64), nullable=True)  # SHA-256 of the backup file payload
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json as _json
        counts = {}
        if self.table_counts:
            try:
                counts = _json.loads(self.table_counts)
            except Exception:
                pass
        return {
            "id":           self.id,
            "filename":     self.filename,          # safe — just the basename
            "size_bytes":   self.size_bytes,
            "size_mb":      round(self.size_bytes / 1024 / 1024, 2),
            "app_version":  self.app_version,
            "db_version":   self.db_version,
            "enc_version":  self.enc_version,
            "status":       self.status,
            "table_counts": counts,
            "checksum":     self.checksum,
            "created_at":   self.created_at.isoformat() if self.created_at else None,
        }

