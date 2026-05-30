# controllers/category_controller.py

from flask import request, jsonify
from database import db
from model import Category


def list_categories():
    return jsonify([c.to_dict() for c in Category.query.all()]), 200


def add_category():
    data = request.json or {}
    name = data.get('name')
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if Category.query.filter_by(name=name).first():
        return jsonify({"error": "Category already exists"}), 400
    cat = Category(
        name=name,
        icon=data.get('icon', 'cash'),
        color=data.get('color', '#64748B'),
        cat_type=data.get('cat_type', 'spending'),
        is_default=False,
    )
    db.session.add(cat)
    db.session.commit()
    return jsonify(cat.to_dict()), 201


def update_category(id):
    try:
        data = request.json or {}
        cat  = Category.query.get(id)
        if not cat:
            return jsonify({"error": "Category not found"}), 404
        if "name"     in data: cat.name     = data["name"]
        if "icon"     in data: cat.icon     = data["icon"]
        if "color"    in data: cat.color    = data["color"]
        if "cat_type" in data: cat.cat_type = data["cat_type"]
        db.session.commit()
        return jsonify(cat.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def delete_category(id):
    cat = Category.query.get(id)
    if not cat:
        return jsonify({"error": "Category not found"}), 404
    if cat.is_default:
        return jsonify({"error": "Cannot delete default categories"}), 400
    db.session.delete(cat)
    db.session.commit()
    return jsonify({"message": "Category deleted"}), 200


def suggest_category(current_user):
    try:
        from model import CategorizationRule
        merchant = request.args.get('merchant', '').lower().strip()
        if not merchant:
            return jsonify({"suggestion": None}), 200

        rule = CategorizationRule.query.filter(
            CategorizationRule.user_id == current_user.id,
            CategorizationRule.merchant_pattern.ilike(f"%{merchant}%"),
        ).first()
        if rule:
            return jsonify({"suggestion": rule.category.to_dict(), "source": "user_rule", "confidence": "high"}), 200

        common = {
            "mcdonald": "Food & Snacks", "burger": "Food & Snacks", "kfc": "Food & Snacks",
            "uber": "Ride / Transport",  "careem": "Ride / Transport",
            "netflix": "Entertainment",  "spotify": "Entertainment",
            "amazon": "Shopping",        "daraz": "Shopping",
            "gym": "Gym & Fitness",      "fitness": "Gym & Fitness",
            "hospital": "Healthcare",    "pharmacy": "Healthcare",
            "electricity": "Bills & Utilities", "gas": "Bills & Utilities",
        }
        for pattern, cat_name in common.items():
            if pattern in merchant:
                cat = Category.query.filter_by(name=cat_name).first()
                if cat:
                    return jsonify({"suggestion": cat.to_dict(), "source": "common_pattern", "confidence": "medium"}), 200

        return jsonify({"suggestion": None}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def list_rules(current_user):
    try:
        from model import CategorizationRule
        rules = CategorizationRule.query.filter_by(user_id=current_user.id).order_by(
            CategorizationRule.created_at.desc()
        ).all()
        return jsonify({"count": len(rules), "rules": [r.to_dict() for r in rules]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def create_rule(current_user):
    try:
        from model import CategorizationRule
        data    = request.get_json() or {}
        pattern = data.get('merchant_pattern', '').strip()
        cat_id  = data.get('category_id')
        if not pattern or not cat_id:
            return jsonify({"error": "Missing required fields"}), 400
        cat = Category.query.get(cat_id)
        if not cat:
            return jsonify({"error": "Category not found"}), 404

        existing = CategorizationRule.query.filter_by(user_id=current_user.id, merchant_pattern=pattern).first()
        if existing:
            existing.category_id = cat_id
            db.session.commit()
            return jsonify({"message": "Rule updated", "rule": existing.to_dict()}), 200

        rule = CategorizationRule(user_id=current_user.id, merchant_pattern=pattern, category_id=cat_id)
        db.session.add(rule)
        db.session.commit()
        return jsonify({"message": "Rule created", "rule": rule.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def delete_rule(current_user, id):
    try:
        from model import CategorizationRule
        rule = CategorizationRule.query.filter_by(id=id, user_id=current_user.id).first()
        if not rule:
            return jsonify({"error": "Rule not found"}), 404
        db.session.delete(rule)
        db.session.commit()
        return jsonify({"message": "Rule deleted"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
