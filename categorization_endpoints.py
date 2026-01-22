"""
New API endpoints for Transaction Category Management
To be added to app.py
"""

# =======================================================
# TRANSACTION CATEGORIZATION ENDPOINTS (NEW)
# =======================================================

@app.route("/api/transactions/uncategorized", methods=["GET"])
@token_required
def get_uncategorized_transactions(current_user):
    """
    Get all transactions with categorization_status = 'pending'
    """
    try:
        transactions = Transaction.query.filter_by(
            categorization_status='pending'
        ).order_by(Transaction.date.desc()).all()
        
        return jsonify({
            "count": len(transactions),
            "transactions": [t.to_dict() for t in transactions]
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transactions/bulk-categorize", methods=["POST"])
@token_required
def bulk_categorize_transactions(current_user):
    """
    Bulk update categories for multiple transactions
    Request body: { transaction_ids: [1, 2, 3], category_id: 5 }
    """
    try:
        data = request.get_json()
        transaction_ids = data.get('transaction_ids', [])
        category_id = data.get('category_id')
        
        if not transaction_ids or not category_id:
            return jsonify({"error": "Missing required fields"}), 400
        
        # Get category for backward compat
        category = Category.query.get(category_id)
        if not category:
            return jsonify({"error": "Category not found"}), 404
        
        # Bulk update
        updated_count = 0
        for tx_id in transaction_ids:
            tx = Transaction.query.get(tx_id)
            if tx:
                tx.category_id = category_id
                tx.purpose = category.name  # Backward compat
                tx.categorization_status = 'manual'
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            "message": f"Updated {updated_count} transactions",
            "updated_count": updated_count
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/categories/suggest", methods=["GET"])
@token_required
def suggest_category(current_user):
    """
    Get suggested category for a merchant
    Query param: merchant (string)
    """
    try:
        from model import CategorizationRule
        
        merchant = request.args.get('merchant', '').lower().strip()
        
        if not merchant:
            return jsonify({"suggestion": None}), 200
        
        # 1. Check user rules first (highest priority)
        rule = CategorizationRule.query.filter(
            CategorizationRule.user_id == current_user.id,
            CategorizationRule.merchant_pattern.ilike(f"%{merchant}%")
        ).first()
        
        if rule:
            return jsonify({
                "suggestion": rule.category.to_dict(),
                "source": "user_rule",
                "confidence": "high"
            }), 200
        
        # 2. Check common patterns (hardcoded knowledge base)
        common_patterns = {
            "mcdonald": "Food & Snacks",
            "burger": "Food & Snacks",
            "pizza": "Food & Snacks",
            "kfc": "Food & Snacks",
            "subway": "Food & Snacks",
            "uber": "Ride / Transport",
            "careem": "Ride / Transport",
            "indriver": "Ride / Transport",
            "netflix": "Entertainment",
            "spotify": "Entertainment",
            "youtube": "Entertainment",
            "amazon": "Shopping",
            "daraz": "Shopping",
            "gym": "Gym & Fitness",
            "fitness": "Gym & Fitness",
            "hospital": "Healthcare",
            "pharmacy": "Healthcare",
            "clinic": "Healthcare",
            "electricity": "Bills & Utilities",
            "gas": "Bills & Utilities",
            "water": "Bills & Utilities",
            "internet": "Bills & Utilities",
            "google one": "Subscription (Google one )",
            "google": "Online Services",
        }
        
        for pattern, cat_name in common_patterns.items():
            if pattern in merchant:
                category = Category.query.filter_by(name=cat_name).first()
                if category:
                    return jsonify({
                        "suggestion": category.to_dict(),
                        "source": "common_pattern",
                        "confidence": "medium"
                    }), 200
        
        # 3. No suggestion
        return jsonify({"suggestion": None}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/categorization-rules", methods=["GET"])
@token_required
def get_categorization_rules(current_user):
    """
    Get all categorization rules for the current user
    """
    try:
        from model import CategorizationRule
        
        rules = CategorizationRule.query.filter_by(
            user_id=current_user.id
        ).order_by(CategorizationRule.created_at.desc()).all()
        
        return jsonify({
            "count": len(rules),
            "rules": [r.to_dict() for r in rules]
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/categorization-rules", methods=["POST"])
@token_required
def create_categorization_rule(current_user):
    """
    Create a new categorization rule
    Request body: { merchant_pattern: "Netflix", category_id: 5 }
    """
    try:
        from model import CategorizationRule
        
        data = request.get_json()
        merchant_pattern = data.get('merchant_pattern', '').strip()
        category_id = data.get('category_id')
        
        if not merchant_pattern or not category_id:
            return jsonify({"error": "Missing required fields"}), 400
        
        # Check if category exists
        category = Category.query.get(category_id)
        if not category:
            return jsonify({"error": "Category not found"}), 404
        
        # Check if rule already exists for this user and pattern
        existing_rule = CategorizationRule.query.filter_by(
            user_id=current_user.id,
            merchant_pattern=merchant_pattern
        ).first()
        
        if existing_rule:
            # Update existing rule
            existing_rule.category_id = category_id
            db.session.commit()
            return jsonify({
                "message": "Rule updated",
                "rule": existing_rule.to_dict()
            }), 200
        
        # Create new rule
        rule = CategorizationRule(
            user_id=current_user.id,
            merchant_pattern=merchant_pattern,
            category_id=category_id
        )
        db.session.add(rule)
        db.session.commit()
        
        return jsonify({
            "message": "Rule created",
            "rule": rule.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/categorization-rules/<int:id>", methods=["DELETE"])
@token_required
def delete_categorization_rule(current_user, id):
    """
    Delete a categorization rule
    """
    try:
        from model import CategorizationRule
        
        rule = CategorizationRule.query.filter_by(
            id=id,
            user_id=current_user.id
        ).first()
        
        if not rule:
            return jsonify({"error": "Rule not found"}), 404
        
        db.session.delete(rule)
        db.session.commit()
        
        return jsonify({"message": "Rule deleted"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
