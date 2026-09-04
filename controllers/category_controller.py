import os
import uuid
from flask import request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from database import db
from model import Category

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static/uploads/category-icons')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def list_categories(current_user):
    from sqlalchemy import or_, and_
    categories = Category.query.filter(
        or_(
            Category.user_id == current_user.id, 
            and_(Category.is_default == True, Category.user_id == None)
        )
    ).filter(Category.is_deleted.isnot(True)).all()
    return jsonify([c.to_dict() for c in categories]), 200


def add_category(current_user):
    data = request.json or {}
    name = data.get('name')
    if not name:
        return jsonify({"error": "Name is required"}), 400
    
    from sqlalchemy import or_, and_
    existing = Category.query.filter(
        Category.name == name,
        Category.is_deleted.isnot(True),
        or_(
            Category.user_id == current_user.id, 
            and_(Category.is_default == True, Category.user_id == None)
        )
    ).first()
    if existing:
        return jsonify({"error": "Category already exists"}), 400
        
    cat = Category(
        name=name,
        icon=data.get('icon', 'cash'),
        icon_type=data.get('icon_type', 'library'),
        custom_icon_url=data.get('custom_icon_url'),
        color=data.get('color', '#64748B'),
        cat_type=data.get('cat_type', 'spending'),
        is_default=False,
        is_deleted=False,
        user_id=current_user.id
    )
    db.session.add(cat)
    db.session.commit()
    return jsonify(cat.to_dict()), 201


def update_category(current_user, id):
    try:
        data = request.json or {}
        cat = Category.query.get(id)
        if not cat or cat.is_deleted:
            return jsonify({"error": "Category not found"}), 404
        if cat.user_id != current_user.id:
            return jsonify({"error": "Cannot update default or other user's categories"}), 403
            
        if "name"            in data: cat.name            = data["name"]
        if "icon"            in data: cat.icon            = data["icon"]
        if "icon_type"       in data: cat.icon_type       = data["icon_type"]
        if "custom_icon_url" in data: cat.custom_icon_url = data["custom_icon_url"]
        if "color"           in data: cat.color           = data["color"]
        if "cat_type"        in data: cat.cat_type        = data["cat_type"]
        db.session.commit()
        return jsonify(cat.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def delete_category(current_user, id):
    cat = Category.query.get(id)
    if not cat or cat.is_deleted:
        return jsonify({"error": "Category not found"}), 404
    if cat.is_default or cat.user_id != current_user.id:
        return jsonify({"error": "Cannot delete default or other user's categories"}), 403
    cat.is_deleted = True
    db.session.commit()
    return jsonify({"message": "Category archived"}), 200


def upload_category_icon(current_user):
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, WebP'}), 400

    # Max size 5MB validation
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({'error': 'File size exceeds 5MB limit'}), 400

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_filename = f"cat_icon_{current_user.id}_{uuid.uuid4().hex[:10]}.{ext}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
    
    file.save(file_path)
    icon_url = f"/static/uploads/category-icons/{unique_filename}"
    return jsonify({'icon_url': icon_url}), 200


def serve_category_icon(filename):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    return send_from_directory(UPLOAD_FOLDER, filename)


def set_category_bucket(current_user, id):
    try:
        from model import CategoryBucketMapping
        data = request.json or {}
        raw_bucket = (data.get('bucket') or '').strip().lower()
        if raw_bucket not in ['needs', 'wants', 'savings']:
            return jsonify({"error": "Invalid bucket"}), 400
        bucket = raw_bucket
            
        cat = Category.query.get(id)
        if not cat:
            return jsonify({"error": "Category not found"}), 404
            
        mapping = CategoryBucketMapping.query.filter_by(user_id=current_user.id, category_id=id).first()
        if mapping:
            mapping.bucket = bucket
            mapping.source = 'user_override'
        else:
            mapping = CategoryBucketMapping(
                user_id=current_user.id,
                category_id=id,
                bucket=bucket,
                source='user_override'
            )
            db.session.add(mapping)
            
        db.session.commit()
        return jsonify(mapping.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


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
