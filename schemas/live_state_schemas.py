# schemas/live_state_schemas.py  —  Typed response contracts for LIVE_STATE
#
# These Pydantic models describe the exact shape returned by each
# live_state_service function.  They serve as:
#   1. Machine-readable documentation for the AI agent that consumes these endpoints
#   2. A validation layer you can opt into (call Model(**result).dict() in the controller)
#   3. The source of truth when building the Elyestra response parser
#
# Usage (optional strict mode in controller):
#   from schemas.live_state_schemas import CurrentBalanceResponse
#   return jsonify(CurrentBalanceResponse(**result).dict()), 200

from __future__ import annotations
from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


# ─── Shared building block ────────────────────────────────────────────────────

class TransactionSnapshot(BaseModel):
    """Minimal live representation of a single transaction row."""
    id:                       int
    amount:                   float
    type:                     str                  # "credit" | "debit"
    source:                   str
    sender:                   Optional[str]
    receiver:                 Optional[str]
    purpose:                  Optional[str]
    notes:                    Optional[str]
    date:                     Optional[str]        # ISO-8601
    created_at:               Optional[str]        # ISO-8601
    account_balance_source:   Optional[str]
    categorization_status:    Optional[str]


# ─── 1. GET /api/live-state/balance ──────────────────────────────────────────

class AccountBalanceItem(BaseModel):
    source:       str
    display_name: str
    account_kind: str
    balance:      float
    last_updated: Optional[str]

class CurrentBalanceResponse(BaseModel):
    """
    LIVE_STATE — What is my current balance?
    One entry per AccountBalance row plus a combined total.
    """
    total_balance: float   = Field(..., description="Sum of all account balances")
    account_count: int
    accounts:      List[AccountBalanceItem]
    as_of:         str     = Field(..., description="UTC timestamp of the query")


# ─── 2. GET /api/live-state/balance/available ─────────────────────────────────

class AvailableBalanceResponse(BaseModel):
    """
    LIVE_STATE — How much is actually available right now?
    total_balance minus any held/pending debit amounts.
    """
    total_balance:      float
    held_amount:        float  = Field(..., description="Sum of all pending debit amounts")
    available_balance:  float  = Field(..., description="total_balance − held_amount")
    pending_count:      int
    as_of:              str


# ─── 3. GET /api/live-state/transactions/last ─────────────────────────────────

class LastTransactionResponse(BaseModel):
    """
    LIVE_STATE — What was my most recent transaction?
    found=False means no transactions exist yet (valid empty-state, not an error).
    """
    found:       bool
    transaction: Optional[TransactionSnapshot]


# ─── 4. GET /api/live-state/transactions/pending ──────────────────────────────

class PendingTransactionsResponse(BaseModel):
    """
    LIVE_STATE — Are there any pending charges on my account?
    count=0 + empty list is the valid empty-state (200, not 404).
    """
    count:        int
    total_amount: float
    transactions: List[TransactionSnapshot]


# ─── 5. GET /api/live-state/transactions/today ────────────────────────────────

class TodaysTransactionsResponse(BaseModel):
    """
    LIVE_STATE — What transactions happened today?
    Covers the full calendar day in UTC.
    """
    date:          str   = Field(..., description="YYYY-MM-DD")
    total_count:   int
    credit_count:  int
    debit_count:   int
    total_in:      float = Field(..., description="Sum of today's credits")
    total_out:     float = Field(..., description="Sum of today's debits")
    transactions:  List[TransactionSnapshot]


# ─── 6. GET /api/live-state/salary-status ────────────────────────────────────

class SalaryStatusResponse(BaseModel):
    """
    LIVE_STATE — Did my salary arrive yet this month?
    arrived=False is a valid state when pay day hasn't happened yet.
    """
    arrived:          bool
    pay_period_start: str  = Field(..., description="First day of the current month (YYYY-MM-DD)")
    as_of:            str
    transaction:      Optional[TransactionSnapshot]


# ─── 7. GET /api/live-state/spending/today ────────────────────────────────────

class TodaysSpendingResponse(BaseModel):
    """
    LIVE_STATE — How much have I spent today?
    total_spent=0.0 is the valid empty-state when nothing has been spent.
    """
    date:        str   = Field(..., description="YYYY-MM-DD")
    total_spent: float
    debit_count: int


# ─── 8. GET /api/live-state/subscriptions/this-week ──────────────────────────

class SubscriptionsThisWeekResponse(BaseModel):
    """
    LIVE_STATE — What subscriptions were charged in the last 7 days?
    Empty list is valid — it means no recurring charges were detected.
    """
    period_start:   str   = Field(..., description="YYYY-MM-DD (7 days ago)")
    period_end:     str   = Field(..., description="YYYY-MM-DD (today)")
    count:          int
    total_charged:  float
    subscriptions:  List[TransactionSnapshot]


# ─── 9. GET /api/live-state/bills/{bill_name}/status ─────────────────────────

class BillPaymentStatusResponse(BaseModel):
    """
    LIVE_STATE — Did my [bill_name] payment go through this month?
    paid=False is a valid answer — it means the bill hasn't been paid yet.
    """
    bill_name:        str
    pay_period_start: str  = Field(..., description="First of the current month")
    as_of:            str
    paid:             bool
    transaction:      Optional[TransactionSnapshot]


# ─── 10. GET /api/live-state/credit-status ───────────────────────────────────

class OverdrawnAccountItem(BaseModel):
    source:       str
    display_name: str
    balance:      float  = Field(..., description="Negative value")
    overdrawn_by: float  = Field(..., description="Absolute overdraft amount")

class CreditAccountItem(BaseModel):
    source:          str
    display_name:    str
    current_balance: float
    account_kind:    str

class CreditStatusResponse(BaseModel):
    """
    LIVE_STATE — Am I overdrawn or using a credit facility right now?
    is_overdrawn=False + empty lists means all accounts are in the black.
    """
    is_overdrawn:           bool
    overdrawn_accounts:     List[OverdrawnAccountItem]
    total_overdrawn_amount: float
    credit_accounts:        List[CreditAccountItem]
    as_of:                  str


# ─── Registry: maps endpoint → schema (used by the Elyestra agent) ────────────

LIVE_STATE_SCHEMA_MAP = {
    "GET /api/live-state/balance":                  CurrentBalanceResponse,
    "GET /api/live-state/balance/available":        AvailableBalanceResponse,
    "GET /api/live-state/transactions/last":        LastTransactionResponse,
    "GET /api/live-state/transactions/pending":     PendingTransactionsResponse,
    "GET /api/live-state/transactions/today":       TodaysTransactionsResponse,
    "GET /api/live-state/salary-status":            SalaryStatusResponse,
    "GET /api/live-state/spending/today":           TodaysSpendingResponse,
    "GET /api/live-state/subscriptions/this-week":  SubscriptionsThisWeekResponse,
    "GET /api/live-state/bills/{bill_name}/status": BillPaymentStatusResponse,
    "GET /api/live-state/credit-status":            CreditStatusResponse,
}
