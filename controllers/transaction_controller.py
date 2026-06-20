# controllers/transaction_controller.py

from flask import request, jsonify
import services.transaction_service as svc


def get_latest(current_user=None):
    limit = request.args.get('limit', default=4, type=int)
    txns  = svc.get_latest_transactions(limit)
    return jsonify([t.to_dict() for t in txns]), 200


def get_total_expenses(current_user):
    try:
        result = svc.get_total_expenses_for_current_month()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_analytics_trend():
    period = request.args.get('period', 'month')
    return jsonify(svc.get_analytics_trend(period)), 200


def get_top_categories():
    period = request.args.get('period', 'month')
    return jsonify(svc.get_top_categories(period)), 200


def get_monthly_category_totals(current_user):
    month_str = request.args.get('month')
    if not month_str:
        return jsonify({"message": "Month is required (YYYY-MM)"}), 400
    try:
        return jsonify(svc.get_monthly_category_totals(month_str)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_uncategorized(current_user):
    txns = svc.get_uncategorized()
    return jsonify({"count": len(txns), "transactions": [t.to_dict() for t in txns]}), 200


def get_spam(current_user):
    return jsonify([t.to_dict() for t in svc.get_spam()]), 200


def get_categorized(current_user):
    return jsonify([t.to_dict() for t in svc.get_categorized()]), 200


def create_transaction(current_user):
    try:
        data     = request.get_json() or {}
        tx, accs = svc.create_manual_transaction(data)
        return jsonify({
            "message":     "Transaction added successfully",
            "transaction": tx.to_dict(),
            "accounts":    accs,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def update_transaction(id):
    try:
        data = request.get_json(silent=True) or {}
        tx   = svc.update_transaction_category(id, data)
        return jsonify({"message": "Transaction updated", "transaction": tx.to_dict()}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def delete_transaction(current_user, txn_id):
    try:
        svc.soft_delete_transaction(txn_id)
        return jsonify({"success": True, "message": "Transaction deleted"}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def mark_spam(current_user, txn_id):
    try:
        svc.mark_spam(txn_id)
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
        n = svc.bulk_categorize(ids, cat_id, slug)
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
        n = svc.bulk_delete(ids)
        return jsonify({"message": f"Deleted {n} transactions"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def bulk_spam(current_user):
    try:
        ids = (request.get_json() or {}).get('transaction_ids', [])
        if not ids:
            return jsonify({"error": "No transactions selected"}), 400
        n = svc.bulk_spam(ids)
        return jsonify({"message": f"Marked {n} as spam"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def upload_receipt(current_user):
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

