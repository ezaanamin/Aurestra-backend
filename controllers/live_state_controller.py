# controllers/live_state_controller.py  —  HTTP layer for LIVE_STATE
#
# Auth: agent_auth_required (AGENT_FIXED_TOKEN).
# No current_user is injected — all queries are global (agent identity).
# Thin controller: validate → call service → jsonify.

from flask import jsonify
import services.live_state_service as svc


def current_balance():
    """
    LIVE_STATE: What is my current balance?
    Returns live AccountBalance totals for all wallets/accounts.
    """
    try:
        result = svc.get_current_balance()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def available_balance():
    """
    LIVE_STATE: How much is available right now (after pending holds)?
    Deducts uncleared pending debits from the current balance.
    """
    try:
        result = svc.get_available_balance()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def last_transaction():
    """
    LIVE_STATE: What was my most recent transaction?
    Returns amount, counterparty, and timestamp of the last settled transaction.
    """
    try:
        result = svc.get_last_transaction()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def pending_transactions():
    """
    LIVE_STATE: Are there any pending charges on my account?
    Lists all transactions with categorization_status = 'pending'.
    """
    try:
        result = svc.get_pending_transactions()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def todays_transactions():
    """
    LIVE_STATE: What transactions happened today?
    All credits and debits for the current calendar day (UTC).
    """
    try:
        result = svc.get_todays_transactions()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def salary_status():
    """
    LIVE_STATE: Did my salary arrive yet this month?
    Returns arrived=True/False plus the matching transaction if found.
    """
    try:
        result = svc.has_salary_arrived()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def spending_today():
    """
    LIVE_STATE: How much have I spent today?
    Sum of all debit transactions in today's calendar day.
    Returns 0.0 gracefully when nothing spent yet.
    """
    try:
        result = svc.get_todays_spending_total()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def subscriptions_this_week():
    """
    LIVE_STATE: What subscriptions were charged this week?
    Debit transactions in the last 7 days matching subscription keywords.
    """
    try:
        result = svc.get_subscriptions_charged_this_week()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def bill_status(bill_name: str):
    """
    LIVE_STATE: Did my [bill_name] payment go through this month?
    Case-insensitive name search across purpose, notes, sender, receiver
    for debit transactions since the 1st of this month.
    Returns paid=True plus the transaction if matched; paid=False + null otherwise.
    """
    try:
        if not bill_name or not bill_name.strip():
            return jsonify({"error": "bill_name path parameter is required"}), 400
        result = svc.check_bill_payment_status(bill_name)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def credit_status():
    """
    LIVE_STATE: Am I overdrawn or using a credit facility right now?
    Surfaces any accounts with negative balance (overdraft) and any
    accounts typed as credit cards / credit lines.
    """
    try:
        result = svc.get_overdraft_or_credit_status()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
