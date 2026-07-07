from app import app
from database import db
from model import User

with app.app_context():
    names = ["ezaan amin", "kashif raza", "eezaan amin"]
    users = User.query.all()
    updated = False
    for u in users:
        if u.full_name and u.full_name.lower() in names:
            if getattr(u, 'current_plan_id', None) != 'developer':
                print(f"Updating {u.full_name} ({u.email}) to developer plan...")
                u.current_plan_id = 'developer'
                updated = True
    if updated:
        db.session.commit()
        print("Commit successful.")
    else:
        print("No users needed updating.")
    print("Done")
