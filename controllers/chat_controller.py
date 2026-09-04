# controllers/chat_controller.py

import os
import requests
import json
import re
from datetime import datetime
from flask import request, jsonify, g
from database import db
from model import AccountBalance, Budget, Transaction, ChatMessage, FinancialInsight
from services.rag_service import _get_month_totals
import ai_agent_api
from prompts import SYSTEM_PROMPT_TEMPLATE, RAG_PROMPT_TEMPLATE, PLANNING_PROMPT_TEMPLATE

from sqlalchemy.orm import Query
from sqlalchemy import event
from flask import has_request_context

# Thread-local / request-local multi-tenant protection listener
_restrict_user_active = False

@event.listens_for(Query, "before_compile", retval=True)
def restrict_queries_by_user(query):
    if not _restrict_user_active:
        return query
    if not has_request_context():
        return query
    user_id = getattr(g, 'restrict_user_id', None)
    if user_id is None:
        return query

    # Scope all queries to this user if model contains user_id attribute
    for desc in query.column_descriptions:
        entity = desc.get('entity')
        if entity and hasattr(entity, 'user_id'):
            # Temporarily bypass limit/offset check in legacy Query
            limit_clause = query._limit_clause
            offset_clause = query._offset_clause
            query._limit_clause = None
            query._offset_clause = None
            
            # Apply filter
            query = query.filter(entity.user_id == user_id)
            
            # Restore limit/offset
            query._limit_clause = limit_clause
            query._offset_clause = offset_clause
    return query

def enable_user_restriction(user_id):
    global _restrict_user_active
    g.restrict_user_id = user_id
    _restrict_user_active = True

def disable_user_restriction():
    global _restrict_user_active
    _restrict_user_active = False

LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://llm.elyestra.org')
LLM_URL = os.getenv('LLM_API_URL', 'https://llm.elyestra.org/api/generate')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:3b')

def compact_json_data(data):
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            if k in ["accent_color", "holder_name", "match_keywords", "statement_account_numbers", "id", "sort_order", "is_manual", "source"]:
                continue
            new_dict[k] = compact_json_data(v)
        return new_dict
    elif isinstance(data, list):
        if len(data) > 5:
            return [compact_json_data(item) for item in data[:5]]
        return [compact_json_data(item) for item in data]
    else:
        return data

class ClassifierManager:
    _intent_tokenizer = None
    _intent_model = None
    _route_tokenizer = None
    _route_model = None

    @classmethod
    def get_intent_classifier(cls):
        if cls._intent_model is None:
            import torch
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base_dir, "ai_models", "financial_intent_classifier")
            cls._intent_tokenizer = AutoTokenizer.from_pretrained(path)
            cls._intent_model = AutoModelForSequenceClassification.from_pretrained(path)
            cls._intent_model.eval()
        return cls._intent_tokenizer, cls._intent_model

    @classmethod
    def get_route_classifier(cls):
        if cls._route_model is None:
            import torch
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base_dir, "ai_models", "financial_api_route_classifier")
            cls._route_tokenizer = AutoTokenizer.from_pretrained(path)
            cls._route_model = AutoModelForSequenceClassification.from_pretrained(path)
            cls._route_model.eval()
        return cls._route_tokenizer, cls._route_model

MONTH_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12"
}

def extract_month_from_text(text):
    text_lower = text.lower()
    match = re.search(r'\b(20\d{2})-(0[1-9]|1[0-2])\b', text)
    if match:
        return match.group(0)
    
    for mname, mnum in MONTH_MAP.items():
        if re.search(r'\b' + mname + r'\b', text_lower):
            now = datetime.utcnow()
            year = now.year
            return f"{year}-{mnum}"
            
    if "last month" in text_lower:
        now = datetime.utcnow()
        y = now.year
        m = now.month - 1
        if m == 0:
            m = 12
            y -= 1
        return f"{y}-{m:02d}"
        
    return datetime.utcnow().strftime("%Y-%m")

def extract_search_query(text):
    words = text.split()
    stop_words = {
        "did", "i", "make", "any", "payment", "payments", "to", "for", "on", "at", "in", "a", "an", "the",
        "show", "me", "find", "search", "list", "transactions", "transaction", "recent", "history", "of",
        "how", "much", "what", "is", "was", "were", "yesterday", "today", "tomorrow", "this", "last", "week", "month"
    }
    filtered = [w.strip("?,.!") for w in words if w.lower().strip("?,.!") not in stop_words]
    return " ".join(filtered) if filtered else None

def extract_amount(text):
    match = re.search(r'\b(\d+[\d,]*)\b', text)
    if match:
        num_str = match.group(1).replace(",", "")
        try:
            return float(num_str)
        except ValueError:
            pass
    return None

def classify_intent_and_route(text):
    import torch
    
    # 1. Classify intent
    try:
        tok, model = ClassifierManager.get_intent_classifier()
        inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            outputs = model(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1)
        pred_idx = torch.argmax(probs, dim=-1).item()
        intent = model.config.id2label.get(pred_idx) or model.config.id2label.get(str(pred_idx))
        intent_conf = probs[0][pred_idx].item()
    except Exception as e:
        print(f"Error in intent classification: {e}")
        intent, intent_conf = "GENERAL_FINANCIAL_KNOWLEDGE", 1.0

    # 2. Classify route if needed
    route = None
    route_conf = 0.0
    if intent in ["LIVE_FINANCIAL_DATA", "FINANCIAL_PLANNING", "HYBRID"]:
        try:
            tok, model = ClassifierManager.get_route_classifier()
            inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
            with torch.no_grad():
                outputs = model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1)
            pred_idx = torch.argmax(probs, dim=-1).item()
            route = model.config.id2label.get(pred_idx) or model.config.id2label.get(str(pred_idx))
            route_conf = probs[0][pred_idx].item()
        except Exception as e:
            print(f"Error in route classification: {e}")
            route = None
            
    return intent, intent_conf, route, route_conf

def chat_session(current_user):
    """
    POST /api/chat
    Request Body:
    {
      "message": "User query"
    }
    """
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    # Check subscription feature and message limits
    from services.subscription_service import can_access, has_remaining_limit
    
    if not can_access(current_user, "ai_chat"):
        return jsonify({
            "error": "FEATURE_LOCKED",
            "message": "Upgrade to Aurestra Plus to unlock AI financial chat.",
            "required_plan": "PLUS"
        }), 403
        
    if not has_remaining_limit(current_user, "ai_chat"):
        return jsonify({
            "error": "LIMIT_EXCEEDED",
            "message": "Monthly AI assistant limit reached. Upgrade to Aurestra Plus for unlimited access.",
            "required_plan": "PLUS"
        }), 403

    # Enable multi-tenant SQL query restriction to protect current user's data
    enable_user_restriction(current_user.id)
    # Scope all live-state / agent API functions called within this request to this user
    g.agent_user_id = current_user.id
    
    try:
        # 1. Classify intent and route using local DistilBERT models
        intent, intent_conf, route, route_conf = classify_intent_and_route(user_message)
        print(f"DEBUG [Chatbot]: Intent={intent} ({intent_conf:.2f}), Route={route} ({route_conf:.2f})")

        # Check feature-specific subscription access
        if intent in ["FINANCIAL_PLANNING", "HYBRID"] or route == "/api/agent/analytics/investments":
            if not can_access(current_user, "investment_planning"):
                return jsonify({
                    "error": "FEATURE_LOCKED",
                    "message": "Upgrade to Aurestra Pro to unlock AI investment planning and roadmaps.",
                    "required_plan": "PRO"
                }), 403
                
        if intent == "RAG_FINANCIAL_MEMORY":
            if not can_access(current_user, "rag_memory"):
                return jsonify({
                    "error": "FEATURE_LOCKED",
                    "message": "Upgrade to Aurestra Plus to unlock financial memory access.",
                    "required_plan": "PLUS"
                }), 403

        # 2. Save user message to database
        user_msg = None
        try:
            user_msg = ChatMessage(
                user_id=current_user.id,
                role="user",
                content=user_message,
                intent=intent,
                api_route=route
            )
            db.session.add(user_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Warning: Failed to save user message to DB: {e}")

        # 3. Retrieve Live Financial Data if requested (for live, planning, and hybrid intents)
        live_data = None
        planning_data = {}
        parsed_params = {}
        
        if intent in ["FINANCIAL_PLANNING", "HYBRID"]:
            # Deep Research Mode: call all live analytical APIs to build a holistic context
            PLANNING_APIS = {
                "overview": "financial_overview",
                "expense_income_summary": "expense_income_summary",
                "net_worth": "net_worth",
                "budget_adherence": "budget_adherence",
                "top_categories": "top_spending_categories",
                "anomalies": "anomaly_detection",
                "recurring": "recurring_expenses",
                "savings": "savings_analytics",
                "projections": "projected_balances",
                "month_over_month": "month_over_month"
            }
            # Extract month parameters if specified
            month = extract_month_from_text(user_message)
            if month:
                parsed_params["month"] = month
            else:
                parsed_params["month"] = datetime.utcnow().strftime('%Y-%m')
                
            original_args = request.args
            request.args = parsed_params
            
            for key, func_name in PLANNING_APIS.items():
                func = getattr(ai_agent_api, func_name, None)
                if func:
                    try:
                        func_to_call = getattr(func, '__wrapped__', func)
                        response = func_to_call()
                        if hasattr(response, 'get_json'):
                            planning_data[key] = compact_json_data(response.get_json())
                    except Exception as e:
                        print(f"Error calling {func_name} in deep research: {e}")
            
            request.args = original_args
            print(f"DEBUG [Chatbot Deep Research]: Successfully queried all live APIs! Aggregated size: {len(planning_data)} endpoints.")
            
        elif route and intent == "LIVE_FINANCIAL_DATA":
            ROUTE_FUNCTIONS = {
                "/api/agent/analytics/anomalies": "anomaly_detection",
                "/api/agent/analytics/budget": "budget_adherence",
                "/api/agent/analytics/investments": "investment_performance",
                "/api/agent/analytics/net-worth": "net_worth",
                "/api/agent/analytics/projections": "projected_balances",
                "/api/agent/analytics/recurring": "recurring_expenses",
                "/api/agent/analytics/savings": "savings_analytics",
                "/api/agent/analytics/statements": "statement_analysis",
                "/api/agent/analytics/top-categories": "top_spending_categories",
                "/api/agent/overview": "financial_overview",
                "/api/agent/summary/expense-income": "expense_income_summary",
                "/api/agent/transactions/search": "transaction_search",
                "/api/agent/trends/cashflow": "cashflow_trends",
                "/api/agent/trends/mom": "month_over_month",
                "/api/agent/trends/monthly-balance": "monthly_balance_trends"
            }
            func_name = ROUTE_FUNCTIONS.get(route)
            if func_name:
                func = getattr(ai_agent_api, func_name, None)
                if func:
                    try:
                        func_to_call = getattr(func, '__wrapped__', func)
                        
                        # Build args
                        month = extract_month_from_text(user_message)
                        if month:
                            parsed_params["month"] = month
                        if route == "/api/agent/transactions/search":
                            q_term = extract_search_query(user_message)
                            if q_term:
                                parsed_params["q"] = q_term
                            amt = extract_amount(user_message)
                            if amt:
                                parsed_params["min_amount"] = amt
                                parsed_params["max_amount"] = amt
                        
                        # Call within request context
                        original_args = request.args
                        request.args = parsed_params
                        
                        response = func_to_call()
                        
                        request.args = original_args
                        if hasattr(response, 'get_json'):
                            live_data = response.get_json()
                        print(f"DEBUG [Chatbot API Invocation]: Successfully called {func_name}! Data received: {json.dumps(live_data, indent=2)[:500]}...")
                    except Exception as e:
                        print(f"Error fetching live data from {route}: {e}")

        # 4. Fetch user accounts and balances
        try:
            accounts = AccountBalance.query.filter_by(user_id=current_user.id).all()
            if accounts:
                total_balance = sum(a.current_balance for a in accounts)
                accounts_info = f"- Total Combined Balance: PKR {total_balance:,.2f}\n" + "\n".join([
                    f"- {a.display_name} ({a.account_kind}): PKR {a.current_balance:,.2f}"
                    for a in accounts
                ])
            else:
                accounts_info = "No accounts linked."
        except Exception as e:
            accounts_info = "Could not load account balances."

        # 5. Fetch current month metrics
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

        # 6. Fetch current month budget
        try:
            budget = Budget.query.filter_by(user_id=current_user.id, month=month_str).first()
            if budget:
                budget_info = (
                    f"Monthly Budget Info for {month_str}:\n"
                    f"- Total Income Budget: PKR {budget.total_budget:,.2f}\n"
                    f"- Total Budgeted Expenses: PKR {budget.total_expenses:,.2f}\n"
                    f"- Needs: PKR {budget.needs:,.2f}, Wants: PKR {budget.wants:,.2f}, Savings: PKR {budget.saving:,.2f}"
                )
            else:
                budget_info = "No budget is set for this month."
        except Exception as e:
            budget_info = "Could not load budget information."

        # 7. Fetch recent transactions (limit to 10)
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

        # 8. Fetch recent conversation history from database for LLM context
        history_str = ""
        try:
            past_messages = (
                ChatMessage.query
                .filter_by(user_id=current_user.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(10)
                .all()
            )
            past_messages.reverse()
            for msg in past_messages:
                if user_msg and msg.id == user_msg.id:
                    continue
                role = "User" if msg.role == "user" else "Aurestra AI"
                history_str += f"\n{role}: {msg.content}"
        except Exception as e:
            print(f"Error loading chat history for context: {e}")

        # 9. Format live data if retrieved
        live_data_info = ""
        if intent in ["FINANCIAL_PLANNING", "HYBRID"] and planning_data:
            live_data_info = f"\n--- DEEP RESEARCH LIVE FINANCIAL DATA ---\n{json.dumps(planning_data, indent=2)}\n-----------------------------------------\n"
        elif live_data:
            live_data_info = f"\n--- LIVE FINANCIAL DATA FOR USER QUERY ---\nRoute matched: {route}\nParameters: {json.dumps(parsed_params)}\nData:\n{json.dumps(live_data, indent=2)}\n-----------------------------------------\n"

        # 10. Fetch historical RAG financial insights if needed (for RAG memory, planning, and hybrid intents)
        insights_info = ""
        if intent in ["RAG_FINANCIAL_MEMORY", "FINANCIAL_PLANNING", "HYBRID"]:
            try:
                insights = FinancialInsight.query.filter_by(user_id=current_user.id).order_by(FinancialInsight.month.desc()).all()
                insights_info = "\n".join([
                    f"- Month {i.month}: {i.content} (tags: {i.tags or 'None'})"
                    for i in insights
                ]) if insights else "No historical financial insights recorded yet."
            except Exception as e:
                print(f"Error fetching financial insights: {e}")
                insights_info = "Could not fetch historical financial insights."

        # 11. Select and build the proper LLM system prompt template based on classification intent
        user_name = current_user.full_name.split()[0] if current_user.full_name else "User"
        current_month_str = datetime.utcnow().strftime('%B %Y')

        if intent in ["FINANCIAL_PLANNING", "HYBRID"]:
            system_prompt = PLANNING_PROMPT_TEMPLATE.format(
                user_name=user_name,
                full_name=current_user.full_name or 'User',
                current_month=current_month_str,
                accounts_info=accounts_info,
                metrics_info=metrics_info,
                budget_info=budget_info,
                txns_info=txns_info,
                live_data_info=live_data_info,
                insights_info=insights_info
            )
        elif intent == "RAG_FINANCIAL_MEMORY":
            system_prompt = RAG_PROMPT_TEMPLATE.format(
                user_name=user_name,
                full_name=current_user.full_name or 'User',
                current_month=current_month_str,
                insights_info=insights_info,
                metrics_info=metrics_info
            )
        else:
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                user_name=user_name,
                full_name=current_user.full_name or 'User',
                current_month=current_month_str,
                accounts_info=accounts_info,
                metrics_info=metrics_info,
                budget_info=budget_info,
                txns_info=txns_info,
                live_data_info=live_data_info
            )

        # 12. Send structured request payload to LLM via centralized client
        print(f"DEBUG [Chatbot Prompt Builder]: Intent: {intent}\nSystem Prompt built:\n{system_prompt[:600]}...\n" + ("-" * 60))
        prompt_content = f"Conversation History:{history_str}\nUser: {user_message}\nAurestra AI:"
        
        try:
            from services.llm_client import generate_llm
            res_data = generate_llm(
                prompt=prompt_content,
                system=system_prompt,
                model=LLM_MODEL,
                options={"temperature": 0.0},
                timeout=300
            )

            reply = res_data.get("response", "").strip()
            if reply:
                # Save assistant reply to database
                try:
                    assistant_msg = ChatMessage(
                        user_id=current_user.id,
                        role="assistant",
                        content=reply
                    )
                    db.session.add(assistant_msg)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"Warning: Failed to save assistant reply to DB: {e}")
                    
                return jsonify({"reply": reply}), 200
            else:
                print(f"❌ LLM request returned empty response.")
                return jsonify({"reply": "I am having trouble connecting to my brain right now. Please try again in a moment!"}), 200

        except Exception as e:
            print(f"❌ Error communicating with LLM: {e}")
            return jsonify({"reply": "I'm sorry, I couldn't reach the AI model. Please check your network or try again later."}), 200

    finally:
        # Guarantee restriction is disabled at the end of execution block
        disable_user_restriction()

def get_chat_history(current_user):
    """
    GET /api/chat/history
    """
    try:
        messages = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.created_at.asc()).all()
        # Format for front-end structure (which uses role, content, and time/created_at)
        return jsonify([
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "intent": m.intent,
                "api_route": m.api_route
            }
            for m in messages
        ]), 200
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return jsonify({"error": "Failed to fetch chat history"}), 500

def clear_chat_history(current_user):
    """
    DELETE /api/chat/history
    """
    try:
        ChatMessage.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        return jsonify({"message": "Chat history cleared successfully"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error clearing chat history: {e}")
        return jsonify({"error": "Failed to clear chat history"}), 500
