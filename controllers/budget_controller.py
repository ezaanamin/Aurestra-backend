# controllers/budget_controller.py

from flask import request, jsonify
import services.budget_service as svc


def get_budget(current_user):
    try:
        return jsonify(svc.get_current_budget()), 200
    except LookupError as e:
        return jsonify({"message": str(e)}), 404


def save_budget(current_user):
    try:
        data, created = svc.save_budget(request.get_json() or {})
        code = 201 if created else 200
        return jsonify({"message": f"Budget {'created' if created else 'updated'} successfully.", **data}), code
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": f"Database error: {str(e)}"}), 500


def set_salary(current_user):
    try:
        data   = request.get_json() or {}
        amount = float(data.get("amount", 0))
        month  = data.get("month")
        budget = svc.set_salary(amount, month)
        return jsonify({
            "message":  "Salary updated successfully.",
            "salary":   amount,
            "month":    budget.month,
            "breakdown": {
                "needs":          budget.needs,
                "wants":          budget.wants,
                "saving":         budget.saving,
                "spending_limit": budget.total_budget,
            },
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_budget_history():
    return jsonify(svc.get_budget_history()), 200


def get_monthly_summary(current_user):
    try:
        return jsonify(svc.get_monthly_summary()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
