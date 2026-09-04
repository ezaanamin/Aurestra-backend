# controllers/transaction_controller.py  (Phase 3: pass current_user.id to all services)

from flask import request, jsonify
import services.transaction_service as svc


def get_latest(current_user):
    limit = request.args.get('limit', default=4, type=int)
    txns  = svc.get_latest_transactions(current_user.id, limit)
    return jsonify([t.to_dict() for t in txns]), 200


def search_transactions(current_user):
    try:
        account_source = request.args.get('account_source') or request.args.get('account_id')
        q = request.args.get('q') or request.args.get('search')
        tx_type = request.args.get('type')
        period = request.args.get('period') or request.args.get('date') or request.args.get('month')
        category_id = request.args.get('category_id', type=int)
        limit = request.args.get('limit', default=100, type=int)
        offset = request.args.get('offset', default=0, type=int)

        txs, total_count = svc.search_transactions(
            user_id=current_user.id,
            account_source=account_source,
            q=q,
            tx_type=tx_type,
            period=period,
            category_id=category_id,
            limit=limit,
            offset=offset
        )
        return jsonify({
            "total": total_count,
            "count": len(txs),
            "transactions": [t.to_dict() for t in txs]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_account_statement(current_user):
    try:
        account_source = request.args.get('account_source') or request.args.get('account_id')
        month_str = request.args.get('month')
        if not account_source:
            return jsonify({"error": "account_source is required"}), 400

        data = svc.get_account_statement(current_user.id, account_source, month_str)
        return jsonify(data), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_total_expenses(current_user):
    try:
        result = svc.get_total_expenses_for_current_month(current_user.id)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_analytics_trend(current_user):
    period = request.args.get('period', 'month')
    return jsonify(svc.get_analytics_trend(current_user.id, period)), 200


def get_analytics_dashboard(current_user):
    period = request.args.get('period', 'month')
    from services.analytics_service import get_analytics_dashboard_data
    return jsonify(get_analytics_dashboard_data(current_user.id, period)), 200


def get_top_categories(current_user):
    period = request.args.get('period', 'month')
    return jsonify(svc.get_top_categories(current_user.id, period)), 200


def get_monthly_category_totals(current_user):
    month_str = request.args.get('month')
    if not month_str:
        return jsonify({"message": "Month is required (YYYY-MM)"}), 400
    try:
        return jsonify(svc.get_monthly_category_totals(current_user.id, month_str)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_uncategorized(current_user):
    txns = svc.get_uncategorized(current_user.id)
    return jsonify({"count": len(txns), "transactions": [t.to_dict() for t in txns]}), 200


def get_spam(current_user):
    return jsonify([t.to_dict() for t in svc.get_spam(current_user.id)]), 200


def get_categorized(current_user):
    return jsonify([t.to_dict() for t in svc.get_categorized(current_user.id)]), 200


def create_transaction(current_user):
    try:
        data     = request.get_json() or {}
        tx, accs = svc.create_manual_transaction(current_user.id, data)
        return jsonify({
            "message":     "Transaction added successfully",
            "transaction": tx.to_dict(),
            "accounts":    accs,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def update_transaction(current_user, id):
    try:
        data = request.get_json(silent=True) or {}
        tx   = svc.update_transaction_category(current_user.id, id, data)
        return jsonify({"message": "Transaction updated", "transaction": tx.to_dict()}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def delete_transaction(current_user, txn_id):
    try:
        svc.soft_delete_transaction(current_user.id, txn_id)
        return jsonify({"success": True, "message": "Transaction deleted"}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def mark_spam(current_user, txn_id):
    try:
        svc.mark_spam(current_user.id, txn_id)
        return jsonify({"success": True, "message": "Marked as spam"}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def bulk_categorize(current_user):
    try:
        data   = request.get_json() or {}
        ids    = data.get('transaction_ids', [])
        cat_id = data.get('category_id')
        slug   = (data.get("account_balance_source") or data.get("balance_account_slug") or "").strip().lower()
        if not ids or not cat_id:
            return jsonify({"error": "Missing required fields"}), 400
        n = svc.bulk_categorize(current_user.id, ids, cat_id, slug)
        return jsonify({"message": f"Updated {n} transactions"}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def bulk_delete(current_user):
    try:
        ids = (request.get_json() or {}).get('transaction_ids', [])
        if not ids:
            return jsonify({"error": "No transactions selected"}), 400
        n = svc.bulk_delete(current_user.id, ids)
        return jsonify({"message": f"Deleted {n} transactions"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def bulk_spam(current_user):
    try:
        ids = (request.get_json() or {}).get('transaction_ids', [])
        if not ids:
            return jsonify({"error": "No transactions selected"}), 400
        n = svc.bulk_spam(current_user.id, ids)
        return jsonify({"message": f"Marked {n} as spam"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def upload_receipt(current_user):
    # Check subscription feature and limits
    from services.subscription_service import can_access, has_remaining_limit
    if not can_access(current_user, "receipt_ocr"):
        return jsonify({
            "error": "FEATURE_LOCKED",
            "message": "Upgrade to Aurestra Plus to unlock receipt scanning.",
            "required_plan": "PLUS"
        }), 403
        
    if not has_remaining_limit(current_user, "ocr"):
        return jsonify({
            "error": "LIMIT_EXCEEDED",
            "message": "Monthly receipt scanning limit reached. Upgrade to Aurestra Plus for unlimited access.",
            "required_plan": "PLUS"
        }), 403

    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected for uploading"}), 400
    try:
        result = svc.process_receipt_upload(file, current_user)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
