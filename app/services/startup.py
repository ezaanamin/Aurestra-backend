"""Startup helpers (category seeding)."""
from app.extensions import db
from app.models import Category


def seed_categories(app):
    """Seed the database with default categories."""
    """Seed the database with default categories."""
    defaults = [
        {"name": "Food & Snacks", "icon": "food", "color": "#FF6B6B", "type": "spending"},
        {"name": "Movies", "icon": "movie", "color": "#EC4899", "type": "spending"},
        {"name": "Tea", "icon": "coffee", "color": "#F59E0B", "type": "spending"},
        {"name": "Therapy", "icon": "brain", "color": "#8B5CF6", "type": "spending"},
        {"name": "Uber", "icon": "car", "color": "#4ECDC4", "type": "spending"},
        {"name": "Audible Subscription", "icon": "headphones", "color": "#A78BFA", "type": "spending"},
        {"name": "Google One Subscription", "icon": "google", "color": "#3B82F6", "type": "spending"},
        {"name": "Ride / Transport", "icon": "car", "color": "#4ECDC4", "type": "spending"},
        {"name": "Bills & Utilities", "icon": "receipt", "color": "#3B82F6", "type": "spending"},
        {"name": "Shopping", "icon": "shopping", "color": "#A78BFA", "type": "spending"},
        {"name": "Healthcare", "icon": "hospital", "color": "#10B981", "type": "spending"},
        {"name": "Education", "icon": "school", "color": "#F59E0B", "type": "spending"},
        {"name": "Groceries", "icon": "cart", "color": "#10B981", "type": "spending"},
        {"name": "Personal Care", "icon": "sparkles", "color": "#8B5CF6", "type": "spending"},
        {"name": "Online Services", "icon": "web", "color": "#3B82F6", "type": "spending"},
        {"name": "Gym & Fitness", "icon": "dumbbell", "color": "#FF6B6B", "type": "spending"},
        {"name": "Income", "icon": "cash", "color": "#10B981", "type": "income"},
        {"name": "Bonus", "icon": "gift", "color": "#F59E0B", "type": "income"},
        {"name": "Investment", "icon": "trending-up", "color": "#3B82F6", "type": "income"},
        {"name": "Uncategorized", "icon": "help-circle", "color": "#64748B", "type": "both"},
    ]
    
    with app.app_context():
        for cat_data in defaults:
            cat = Category.query.filter_by(name=cat_data["name"]).first()
            if not cat:
                cat = Category(
                    name=cat_data["name"],
                    icon=cat_data["icon"],
                    color=cat_data["color"],
                    cat_type=cat_data["type"],
                    is_default=True
                )
                db.session.add(cat)
            else:
                # Update type for existing default categories if needed
                cat.cat_type = cat_data["type"]
        db.session.commit()
        print("✅ Categories seeded")
