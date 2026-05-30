# controllers/savings_controller.py

from datetime import datetime
from flask import request, jsonify
from database import db
from model import SavingsGoal, AccountBalance, Transaction


def list_goals(current_user):
    return jsonify([g.to_dict() for g in SavingsGoal.query.all()]), 200


def create_goal(current_user):
    data    = request.get_json() or {}
    name    = data.get("name")
    target  = float(data.get("target_amount", 0))
    current = float(data.get("current_amount", 0))
    emoji   = data.get("emoji", "💰")

    deadline = None
    if data.get("deadline"):
        try:
            deadline = datetime.strptime(data["deadline"], "%Y-%m-%d").date()
        except Exception:
            pass

    goal = SavingsGoal(name=name, target_amount=target, current_amount=current,
                       emoji=emoji, deadline=deadline)
    db.session.add(goal)
    db.session.commit()
    return jsonify(goal.to_dict()), 201


def update_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
        data = request.get_json() or {}
        if "name"          in data: goal.name          = data["name"]
        if "target_amount" in data: goal.target_amount = float(data["target_amount"])
        if "current_amount" in data: goal.current_amount = float(data["current_amount"])
        if "emoji"         in data: goal.emoji         = data["emoji"]
        if "deadline"      in data:
            try:
                goal.deadline = datetime.strptime(data["deadline"], "%Y-%m-%d").date()
            except Exception:
                pass
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def delete_goal(current_user, id):
    try:
        goal = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404

        if goal.current_amount > 0:
            refund = goal.current_amount
            bank   = AccountBalance.query.filter_by(source='bank').first() or AccountBalance.query.first()
            if bank:
                bank.current_balance  += refund
                bank.last_updated      = datetime.utcnow()
                db.session.add(Transaction(
                    source='bank', date=datetime.utcnow(), amount=refund, type='credit',
                    purpose='Savings Refund', sender='Savings Goal', receiver='Me',
                    notes=f"Refund from deleted goal: {goal.name}",
                ))

        db.session.delete(goal)
        db.session.commit()
        return jsonify({"message": "Goal deleted and funds refunded"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def contribute(current_user, id):
    try:
        goal   = SavingsGoal.query.get(id)
        if not goal:
            return jsonify({"error": "Goal not found"}), 404
        amount = float((request.get_json() or {}).get("amount", 0))
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than zero"}), 400

        bank = AccountBalance.query.filter_by(source='bank').first() or AccountBalance.query.first()
        if not bank:
            return jsonify({"error": "No account found to fund savings."}), 400

        bank.current_balance -= amount
        bank.last_updated     = datetime.utcnow()
        db.session.add(Transaction(
            source='bank', date=datetime.utcnow(), amount=amount, type='debit',
            purpose='Savings', sender='Me', receiver='Savings Goal',
            notes=f"Contribution to: {goal.name}",
        ))
        goal.current_amount += amount
        db.session.commit()
        return jsonify(goal.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
