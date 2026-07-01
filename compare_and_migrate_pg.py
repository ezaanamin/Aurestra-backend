import os
import sys
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

# Ensure backend directory is in python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import app
from database import get_sqlite_engine, get_postgres_engine
from model import db
import model  # Registers all SQLAlchemy tables on db.metadata

def run_compare_and_migrate():
    with app.app_context():
        sqlite_engine = get_sqlite_engine()
        pg_engine = get_postgres_engine()

        if not pg_engine:
            print("❌ PostgreSQL is not configured on this server. Check your .env file.")
            return

        print("🔍 Inspecting SQLite and PostgreSQL database schemas...")
        sqlite_inspector = inspect(sqlite_engine)
        pg_inspector = inspect(pg_engine)

        sqlite_tables = sqlite_inspector.get_table_names()
        pg_tables = pg_inspector.get_table_names()

        print(f"SQLite tables found ({len(sqlite_tables)}): {sqlite_tables}")
        print(f"PostgreSQL tables found ({len(pg_tables)}): {pg_tables}")

        # 1. Handle missing tables in PostgreSQL
        for t_name in sqlite_tables:
            if t_name not in pg_tables:
                print(f"🏗️  Table '{t_name}' is missing in PostgreSQL.")
                table_obj = db.metadata.tables.get(t_name)
                if table_obj is not None:
                    try:
                        print(f"Creating table '{t_name}' in PostgreSQL...")
                        table_obj.create(bind=pg_engine)
                        print(f"✅ Created table '{t_name}' successfully!")
                    except Exception as e:
                        print(f"❌ Failed to create table '{t_name}': {e}")
                else:
                    print(f"⚠️  SQLAlchemy metadata not found for table '{t_name}', skipping auto-creation.")

        # Re-inspect PG tables after creations
        pg_inspector = inspect(pg_engine)
        pg_tables = pg_inspector.get_table_names()

        # 2. Handle missing columns in existing PostgreSQL tables
        for t_name in sqlite_tables:
            if t_name in pg_tables:
                sqlite_cols = {c['name']: c for c in sqlite_inspector.get_columns(t_name)}
                pg_cols = {c['name']: c for c in pg_inspector.get_columns(t_name)}

                table_obj = db.metadata.tables.get(t_name)
                if table_obj is None:
                    continue

                for col_name, col_info in sqlite_cols.items():
                    if col_name not in pg_cols:
                        print(f"➕ Column '{col_name}' in table '{t_name}' is missing in PostgreSQL.")
                        if col_name in table_obj.columns:
                            col_obj = table_obj.columns[col_name]
                            try:
                                # Compile type for PostgreSQL
                                type_str = str(col_obj.type.compile(dialect=postgresql.dialect()))
                                
                                # Make sure newly added columns to existing tables are nullable to avoid conflicts with existing data
                                alter_query = f'ALTER TABLE "{t_name}" ADD COLUMN "{col_name}" {type_str};'
                                print(f"Executing: {alter_query}")
                                
                                with pg_engine.begin() as conn:
                                    conn.execute(text(alter_query))
                                print(f"✅ Column '{col_name}' added to '{t_name}' successfully!")
                            except Exception as e:
                                print(f"❌ Failed to add column '{col_name}' to '{t_name}': {e}")
                        else:
                            print(f"⚠️  SQLAlchemy Column metadata not found for '{col_name}' in '{t_name}', skipping.")

        print("🎉 Compare and migrate process finished!")

if __name__ == "__main__":
    run_compare_and_migrate()
