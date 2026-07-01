import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aurestra.db')

def get_table_schema(conn, table_name):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table_name})")
    columns = c.fetchall()
    
    col_defs = []
    col_names = []
    
    for col in columns:
        col_id, name, type_, notnull, default, pk = col
        col_names.append(name)
        
        def_str = f"{name} {type_}"
        if pk == 1:
            def_str += " PRIMARY KEY"
        if notnull == 1:
            def_str += " NOT NULL"
        if default is not None:
            def_str += f" DEFAULT {default}"
            
        col_defs.append(def_str)
        
    return col_names, col_defs

def update_constraints():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("PRAGMA foreign_keys=off;")
    c.execute("BEGIN TRANSACTION;")

    try:
        tables_to_update = {
            "transactions": [("user_id", "transaction_id"), ("user_id", "transaction_hash")],
            "budgets": [("user_id", "month")],
            "account_balances": [("user_id", "source")],
            "categories": [("user_id", "name")],
            "monthly_balances": [("user_id", "month")],
            "statement_analysis": [("user_id", "month")],
            "sms_history": [("user_id", "sms_hash")]
        }

        for table, unq_constraints in tables_to_update.items():
            print(f"Updating {table}...")
            # Check if table exists
            c.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
            if c.fetchone()[0] == 0:
                print(f"Skipping {table} (does not exist)")
                continue

            col_names, col_defs = get_table_schema(conn, table)
            
            # Recreate table definition
            create_stmt = f"CREATE TABLE {table}_new (\n"
            create_stmt += ",\n".join(col_defs)
            
            # Add new unique constraints
            for constraint_cols in unq_constraints:
                # Ensure columns exist before adding constraint
                valid = True
                for col in constraint_cols:
                    if col not in col_names:
                        valid = False
                if valid:
                    cols_str = ", ".join(constraint_cols)
                    create_stmt += f",\nUNIQUE({cols_str})"
            
            # Re-add foreign keys manually if we really want to, but SQLite doesn't strictly require it 
            # for backward compatibility unless PRAGMA foreign_keys=ON is strictly enforced.
            # We'll just add user_id foreign key if it exists
            if "user_id" in col_names:
                create_stmt += ",\nFOREIGN KEY(user_id) REFERENCES users(id)"
            if "category_id" in col_names and table == "transactions":
                create_stmt += ",\nFOREIGN KEY(category_id) REFERENCES categories(id)"
                
            create_stmt += "\n);"
            
            c.execute(create_stmt)
            
            cols_csv = ", ".join(col_names)
            c.execute(f"INSERT INTO {table}_new ({cols_csv}) SELECT {cols_csv} FROM {table};")
            c.execute(f"DROP TABLE {table};")
            c.execute(f"ALTER TABLE {table}_new RENAME TO {table};")

        # Recreate indexes
        c.execute("CREATE INDEX IF NOT EXISTS ix_transactions_sms_hash ON transactions (sms_hash);")

        c.execute("COMMIT;")
        print("✅ Unique constraints successfully updated for multi-tenant isolation.")
    except Exception as e:
        c.execute("ROLLBACK;")
        print(f"❌ Error updating constraints: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    update_constraints()
