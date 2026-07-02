import sys
import json
from app import app
from model import Category

def view_categories_for_user(user_id: int):
    with app.app_context():
        from sqlalchemy import or_
        
        print(f"\nFetching categories for User ID {user_id} (including defaults)...\n")
        
        categories = Category.query.filter(
            or_(Category.is_default == True, Category.user_id == user_id)
        ).all()
        
        results = [c.to_dict() for c in categories]
        
        for c in results:
            print(f"[{'DEFAULT' if c['is_default'] else ' CUSTOM'}] ID: {c['id']:<3} | Name: {c['name']:<20} | Type: {c['cat_type']}")
            
        print(f"\nTotal Categories: {len(results)}\n")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 view_user_categories.py <user_id>")
        sys.exit(1)
        
    try:
        user_id = int(sys.argv[1])
        view_categories_for_user(user_id)
    except ValueError:
        print("Error: User ID must be an integer.")
