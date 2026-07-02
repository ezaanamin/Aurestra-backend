import sys
from app import app
from database import db
from model import Category

def fix_categories():
    with app.app_context():
        # 1. Assign all non-default categories that have no user_id to user_id = 1
        orphaned_customs = Category.query.filter(
            Category.is_default == False, 
            Category.user_id == None
        ).all()
        
        count_assigned = 0
        for cat in orphaned_customs:
            cat.user_id = 1
            count_assigned += 1
            
        # 2. Delete duplicate default categories
        # Let's keep the lowest ID for each name
        defaults = Category.query.filter_by(is_default=True).all()
        seen_names = set()
        count_deleted = 0
        
        for cat in sorted(defaults, key=lambda c: c.id):
            if cat.name.lower() in seen_names:
                # It's a duplicate, delete it
                print(f"Deleting duplicate default: {cat.name} (ID: {cat.id})")
                db.session.delete(cat)
                count_deleted += 1
            else:
                seen_names.add(cat.name.lower())
                
        db.session.commit()
        print(f"\n✅ Fixed Database Categories!")
        print(f"Assigned {count_assigned} orphaned custom categories to User 1.")
        print(f"Deleted {count_deleted} duplicate default categories.")

if __name__ == '__main__':
    fix_categories()
