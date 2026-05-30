import os
from app import app
from database import get_postgres_engine
from model import db
from sqlalchemy import MetaData

def sync_schema():
    with app.app_context():
        pg_eng = get_postgres_engine()
        if not pg_eng:
            print("❌ PostgreSQL engine not found. Check your .env file.")
            return

        print("⚠️  Warning: This will drop ALL tables in your PostgreSQL database.")
        print("Connecting to PostgreSQL...")
        
        # 1. Drop all existing tables in PostgreSQL
        meta = MetaData()
        meta.reflect(bind=pg_eng)
        
        if meta.tables:
            print(f"🗑️  Dropping {len(meta.tables)} existing tables in PostgreSQL (CASCADE)...")
            meta.drop_all(bind=pg_eng)
        else:
            print("ℹ️  No existing tables found in PostgreSQL.")

        # 2. Recreate all tables based on the exact current SQLAlchemy models
        print("🏗️  Creating fresh schema from current models...")
        db.metadata.create_all(bind=pg_eng)
        
        print("✅ PostgreSQL schema successfully synced!")
        print("You can now run your backup trigger to safely UPSERT your data.")

if __name__ == "__main__":
    sync_schema()
