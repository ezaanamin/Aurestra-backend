import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(backend_dir)

from database import app, db
from decorator.helpers import seed_categories

print("🔄 Starting category seeding...")
seed_categories()
print("✅ Seeding completed!")
