from database import db
from datetime import datetime

class MonthlyBalance(db.Model):
    """
    Stores the monthly summary of balances for bank + wallet combined.
    Includes calculated fields for expense and net savings. Budget data 
    is managed separately in the Budget model.
    """
    __tablename__ = "monthly_balances"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(20), nullable=False)  # e.g., "combined"
    month = db.Column(db.String(7), nullable=False, unique=True)    # Format: "YYYY-MM"
    
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
    
    source = db.Column(db.String(20), nullable=False)
    date = db.Column(db.DateTime, nullable=False)

    # Category (prints, gym, food, research paper etc.)
    purpose = db.Column(db.String(255), nullable=True)

    # Amount received
    amount = db.Column(db.Float, nullable=False)

    # Sender / Who actually sent the money
    sender = db.Column(db.String(255), nullable=True)

    # NEW — Receiver (Hiba Dawood, Haris Masood, Umair etc.)
    receiver = db.Column(db.String(255), nullable=True)

    # NEW — unique easypaisa transaction ID for duplicate protection
    transaction_id = db.Column(db.String(50), unique=True, nullable=True)
    
    # NEW — robust hash for deduplication
    transaction_hash = db.Column(db.String(64), unique=True, nullable=True)

    # Optional extra details (store name, bank name etc.)
    notes = db.Column(db.String(255), nullable=True)

    # NEW — type: credit or debit
    type = db.Column(db.String(10), nullable=False)
    
    # NEW — Categorization tracking
    categorization_status = db.Column(db.String(20), default='pending')  # 'pending', 'categorized', 'auto', 'manual'
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)  # Proper FK relationship

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to Category (will be defined after Category model)
    # category = db.relationship('Category', backref='transactions')

    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source,
            "date": self.date.isoformat(),
            "purpose": self.purpose,
            "amount": self.amount,
            "sender": self.sender,
            "receiver": self.receiver,
            "transaction_id": self.transaction_id,
            "notes": self.notes,
            "type": self.type,
            "categorization_status": self.categorization_status,
            "category_id": self.category_id,
            "created_at": self.created_at.isoformat()
        }


    def __repr__(self):
        return f"<Transaction {self.source} | {self.amount} | {self.date} | {self.sender} → {self.receiver}>"


class Budget(db.Model):
   
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(7), nullable=False, unique=True)  # Format: "YYYY-MM"
    total_budget = db.Column(db.Float, nullable=False)

    # Updated columns for detailed savings tracking: Needs, Wants, and Total
    needs = db.Column(db.Float, nullable=False, default=0.0)
    wants = db.Column(db.Float, nullable=False, default=0.0) # Re-introduced for non-essential goals
    saving = db.Column(db.Float, nullable=False, default=0.0) # Total planned or achieved savings

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Budget {self.month} | Total: {self.total_budget} | Savings (Total): {self.saving}>"


class AccountBalance(db.Model):
    """
    Stores current balances for each account/wallet.
    Updated whenever transactions are processed.
    """
    __tablename__ = "account_balances"
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Account source (matches Transaction.source)
    source = db.Column(db.String(50), nullable=False, unique=True)  # e.g., "bank", "jazzcash", "easypaisa"
    
    # Current balance
    current_balance = db.Column(db.Float, nullable=False, default=0.0)
    
    # Last update timestamp
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # NEW: Manual override flag
    is_manual = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f"<AccountBalance {self.source} | Balance: {self.current_balance:.2f}>"
    
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "source": self.source,
            "balance": self.current_balance,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None
        }


class SavingsGoal(db.Model):
    """
    Stores user savings goals (e.g., 'New Laptop', 'Emergency Fund').
    """
    __tablename__ = "savings_goals"

    id = db.Column(db.Integer, primary_key=True)
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
    name = db.Column(db.String(100), nullable=False, unique=True)
    icon = db.Column(db.String(50), nullable=False, default="cash")
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
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False) # Storing 'PIN' as hash for now (or plain if simple)
    full_name = db.Column(db.String(100), nullable=True)
    
    # Google Auth Fields
    google_id = db.Column(db.String(50), nullable=True)
    google_email = db.Column(db.String(120), nullable=True)
    google_refresh_token = db.Column(db.String(255), nullable=True)
    
    # OTP Fields
    otp_code = db.Column(db.String(6), nullable=True)
    otp_expiry = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # New Avatar URL
    avatar_url = db.Column(db.String(512), nullable=True)
    
    # Notifications Preference
    notifications_enabled = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "avatar_url": self.avatar_url,
            "notifications_enabled": self.notifications_enabled
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
    month = db.Column(db.String(7), nullable=False) # "YYYY-MM"
    
    # Natural Language Content (The 'Story')
    content = db.Column(db.Text, nullable=False)
    
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
    month = db.Column(db.String(7), nullable=False, unique=True)  # "YYYY-MM"
    
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

    def to_dict(self):
        import json
        breakdown = {}
        if self.breakdown_json:
            try:
                breakdown = json.loads(self.breakdown_json)
            except:
                pass
        
        # Get current account balance for comparison
        from model import AccountBalance
        account_balance_record = AccountBalance.query.order_by(AccountBalance.last_updated.desc()).first()
        current_account_balance = account_balance_record.current_balance if account_balance_record else 0.0
        
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
            "read_status": "read" if self.reviewed_at else "unread"  # NEW: Show if statement has been viewed
        }
