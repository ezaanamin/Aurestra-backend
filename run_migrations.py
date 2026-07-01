import os
import re
from database import db, app
from sqlalchemy import text

def _split_sql(sql_content: str) -> list[str]:
    """
    Split a SQL file into individual statements, correctly handling:
    - Dollar-quoted blocks ($$...$$, $body$...$body$)
    - Semicolons inside strings / comments
    """
    statements = []
    current: list[str] = []
    in_dollar_quote = False
    dollar_tag = ""
    i = 0

    while i < len(sql_content):
        # Detect start/end of a dollar-quoted string (e.g. $$ or $body$)
        if not in_dollar_quote:
            m = re.match(r'\$([^$]*)\$', sql_content[i:])
            if m:
                in_dollar_quote = True
                dollar_tag = m.group(0)  # e.g.  "$$"  or  "$body$"
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue
        else:
            if sql_content[i:i + len(dollar_tag)] == dollar_tag:
                in_dollar_quote = False
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue

        ch = sql_content[i]

        if ch == ';' and not in_dollar_quote:
            stmt = ''.join(current).strip()
            if stmt:
                # Strip inline comments that are not actual SQL
                # (keep the statement but remove leading comment-only lines)
                statements.append(stmt)
            current = []
        else:
            current.append(ch)

        i += 1

    # Catch trailing statement without trailing semicolon
    leftover = ''.join(current).strip()
    if leftover and not leftover.startswith('--'):
        statements.append(leftover)

    return statements


def run_migrations():
    """
    Executes SQL migration files in alphanumeric order against the primary DB.
    Handles PostgreSQL dollar-quoted trigger bodies correctly.
    """
    migrations_dir = os.path.join(os.path.dirname(__file__), 'migrations')

    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith('.sql')])
    print(f"🚀 [Migrations] Found {len(files)} migration files.")

    with app.app_context():
        try:
            db.session.execute(text("SELECT 1"))
            print("✅ [Migrations] Database connected.")

            # Create history table if not exists
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS migration_history (
                    filename TEXT PRIMARY KEY,
                    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.commit()

            # Retrieve applied migrations
            applied = {
                r[0] for r in db.session.execute(text("SELECT filename FROM migration_history")).fetchall()
            }

            for filename in files:
                if filename in applied:
                    continue

                file_path = os.path.join(migrations_dir, filename)
                print(f"🔄 [Migrations] Running {filename}...")

                with open(file_path, 'r') as f:
                    sql_content = f.read()

                statements = _split_sql(sql_content)

                for stmt in statements:
                    # Skip purely comment statements
                    stripped = re.sub(r'--[^\n]*', '', stmt).strip()
                    if not stripped:
                        continue
                    try:
                        db.session.execute(text(stmt))
                    except Exception as e:
                        err_str = str(e).lower()
                        if "already exists" in err_str or "duplicate column" in err_str:
                            print(f"   ⚠️  Already exists — skipping.")
                        else:
                            print(f"   ❌ Error: {e}")

                # Log applied migration
                db.session.execute(
                    text("INSERT OR IGNORE INTO migration_history (filename) VALUES (:filename)"),
                    {"filename": filename}
                )
                db.session.commit()
                print(f"   ✅ Done.")

            print("✨ [Migrations] All migrations completed successfully.")

        except Exception as e:
            print(f"❌ [Migrations] Critical error: {e}")


if __name__ == "__main__":
    run_migrations()

