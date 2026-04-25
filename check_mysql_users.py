from database import app, db
from model import User

with app.app_context():
    users = User.query.all()
    print(f"--- Users Found ({len(users)}) ---")
    for u in users:
        print(f"ID: {u.id} | Email: {u.email}")
