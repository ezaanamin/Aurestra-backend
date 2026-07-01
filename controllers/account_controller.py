# controllers/account_controller.py  (Phase 3: pass current_user.id to all services)

from flask import request, jsonify
from model import AccountBalance
from services import account_service
from account_matching import match_account_for_notification


def list_accounts(current_user):
    try:
        accounts = account_service.get_all_accounts(current_user.id)
        return jsonify(accounts), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def create_account(current_user):
    try:
        data = request.get_json() or {}
        acc  = account_service.create_account(current_user.id, data)
        return jsonify({"message": "Account created", "account": acc.to_dict()}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def update_account(current_user, account_id):
    acc = AccountBalance.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    if acc.source == "cash":
        return jsonify({"error": "Cash only supports balance updates from the app."}), 400
    try:
        data = request.get_json() or {}
        acc  = account_service.update_account(current_user.id, acc, data)
        return jsonify({"account": acc.to_dict()}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def delete_account(current_user, account_id):
    acc = AccountBalance.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    try:
        account_service.delete_account(current_user.id, acc)
        return jsonify({"message": "Deleted"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def set_balance(current_user):
    try:
        data       = request.get_json() or {}
        amount     = float(data.get('amount', 0))
        account_id = data.get("account_id")
        source     = data.get("source", "bank") if account_id is None else None

        balance = account_service.set_manual_balance(
            user_id=current_user.id,
            account_id=int(account_id) if account_id else None,
            source=source,
            amount=amount,
        )
        all_accounts = (
            AccountBalance.query
            .filter_by(user_id=current_user.id)
            .order_by(AccountBalance.sort_order, AccountBalance.id)
            .all()
        )
        return jsonify({
            "message":  "Balance updated manually",
            "account":  balance.to_dict(),
            "accounts": [acc.to_dict() for acc in all_accounts],
        }), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def match_account(current_user):
    data     = request.get_json() or {}
    accounts = (
        AccountBalance.query
        .filter_by(user_id=current_user.id)
        .order_by(AccountBalance.sort_order, AccountBalance.id)
        .all()
    )
    m = match_account_for_notification(
        accounts,
        data.get("title")       or "",
        data.get("text")        or "",
        data.get("packageName") or data.get("package_name") or "",
    )
    return jsonify({"match": m.to_dict() if m else None}), 200
