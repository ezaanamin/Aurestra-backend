# controllers/chat_controller.py

import os
import requests
import json
from datetime import datetime
from flask import request, jsonify, g
from model import AccountBalance, Budget, Transaction
from services.rag_service import _get_month_totals

LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://depending-nsw-participating-thoughts.trycloudflare.com')
LLM_URL = f'{LLM_BASE_URL.rstrip("/")}/api/generate'
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:3b')

def chat_session(current_user):
    """
    POST /api/chat
    Request Body:
    {
      "message": "User query",
      "history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }
    """
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    history = data.get("history", [])

    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    # 1. Fetch user accounts and balances
    try:
        accounts = AccountBalance.query.filter_by(user_id=current_user.id).all()
        accounts_info = "\n".join([
            f"- {a.display_name} ({a.account_kind}): PKR {a.current_balance:,.2f}"
            for a in accounts
        ]) if accounts else "No accounts linked."
    except Exception as e:
        accounts_info = "Could not load account balances."

    # 2. Fetch current month metrics
    month_str = datetime.utcnow().strftime('%Y-%m')
    try:
        current_metrics = _get_month_totals(current_user.id, month_str)
        metrics_info = (
            f"- Total Income: PKR {current_metrics['total_income']:,.2f}\n"
            f"- Total Expenses: PKR {current_metrics['total_expense']:,.2f}\n"
            f"- Net Savings: PKR {current_metrics['savings']:,.2f}\n"
            f"- Transactions recorded this month: {current_metrics['transaction_count']}"
        )
    except Exception as e:
        metrics_info = "Could not compute current month metrics."

    # 3. Fetch current month budget
    try:
        budget = Budget.query.filter_by(user_id=current_user.id, month=month_str).first()
        if budget:
            budget_info = (
                f"Monthly Budget Info for {month_str}:\n"
                f"- Total Income Budget: PKR {budget.total_budget:,.2f}\n"
                f"- Total Budgeted Expenses: PKR {budget.total_expenses:,.2f}\n"
                f"- Needs: PKR {budget.needs:,.2f}, Wants: PKR {budget.wants:,.2f}, Savings: PKR {budget.saving:,.2f}\n"
                f"- Spending Limit: PKR {budget.spending_limit:,.2f}"
            )
        else:
            budget_info = "No budget is set for this month."
    except Exception as e:
        budget_info = "Could not load budget information."

    # 4. Fetch recent transactions (limit to 10)
    try:
        txns = (
            Transaction.query
            .filter_by(user_id=current_user.id, is_deleted=False, is_spam=False)
            .order_by(Transaction.date.desc())
            .limit(10)
            .all()
        )
        txns_info = "\n".join([
            f"- {t.date.strftime('%Y-%m-%d')}: {t.type.upper()} of PKR {t.amount:,.2f} for {t.purpose or 'Uncategorized'} (sender: {t.sender or 'N/A'}, receiver: {t.receiver or 'N/A'})"
            for t in txns
        ]) if txns else "No recent transactions found."
    except Exception as e:
        txns_info = "Could not load recent transactions."

    # 5. Format history
    history_str = ""
    for msg in history:
        role = "User" if msg.get("role") == "user" else "Aurestra AI"
        history_str += f"\n{role}: {msg.get('content', '')}"

    # 6. Build the LLM Prompt
    user_name = current_user.full_name.split()[0] if current_user.full_name else "User"
    system_prompt = f"""You are a helpful, professional financial AI assistant called "Aurestra AI", designed to assist {user_name} with their personal finances.
All monetary amounts are in Pakistani Rupees (PKR). Always format currency amounts with the prefix "PKR" directly before the number (e.g. PKR 1,500) and never use the dollar sign.

Here is {user_name}'s current financial profile and context:
User Name: {current_user.full_name or 'User'}
Current Month: {datetime.utcnow().strftime('%B %Y')}

Current Account/Wallet Balances:
{accounts_info}

This Month's Financial Summary:
{metrics_info}

{budget_info}

Recent Transactions:
{txns_info}

Instructions:
1. Answer the user's questions clearly, concisely, and accurately based on the financial context provided above.
2. If the user asks something unrelated to their personal finances, politely guide them back to talking about their Aurestra account or finances.
3. Be friendly, encouraging, and helpful. Suggest ways they can improve their savings or stay within budget.
4. Keep answers brief (1-3 paragraphs maximum) so they are easy to read in a mobile chat interface.
"""

    full_prompt = f"{system_prompt}\nConversation History:{history_str}\nUser: {user_message}\nAurestra AI:"

    try:
        response = requests.post(LLM_URL, json={
            "model": LLM_MODEL,
            "prompt": full_prompt,
            "stream": False
        }, timeout=35)

        if response.status_code == 200:
            data = response.json()
            reply = data.get("response", "").strip()
            return jsonify({"reply": reply}), 200
        else:
            print(f"❌ LLM request failed with status {response.status_code}: {response.text}")
            return jsonify({"reply": "I am having trouble connecting to my brain right now. Please try again in a moment!"}), 200

    except Exception as e:
        print(f"❌ Error communicating with LLM: {e}")
        return jsonify({"reply": "I'm sorry, I couldn't reach the AI model. Please check your network or try again later."}), 200
