import os
from database import db, app
from sqlalchemy import text

def run_seeders():
    """
    Executes SQL seeder files in alphanumeric order.
    """
    seeders_dir = os.path.join(os.path.dirname(__file__), 'seeders')
    
    if not os.path.exists(seeders_dir):
        print("ℹ️ [Seeders] No seeders directory found.")
        return

    files = sorted([f for f in os.listdir(seeders_dir) if f.endswith('.sql')])
    
    print(f"🌱 [Seeders] Found {len(files)} seeder files.")
    
    with app.app_context():
        try:
            for filename in files:
                file_path = os.path.join(seeders_dir, filename)
                print(f"🔄 [Seeders] Running {filename}...")
                
                with open(file_path, 'r') as f:
                    sql_content = f.read()
                
                statements = sql_content.split(';')
                
                for statement in statements:
                    if statement.strip():
                        try:
                            db.session.execute(text(statement))
                        except Exception as e:
                            # Unique constraint errors are common in seeders
                            err_str = str(e).lower()
                            if "duplicate entry" in err_str or "unique constraint" in err_str:
                                print(f"   ⚠️ Duplicate entry (Skipping).")
                            else:
                                print(f"   ❌ Error executing statement: {e}")
                
                db.session.commit()
                print(f"   ✅ Done.")
                
            print("✨ [Seeders] Seeding completed.")
            
        except Exception as e:
            print(f"❌ [Seeders] Critical Error: {e}")

if __name__ == "__main__":
    run_seeders()
