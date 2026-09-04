"""
decrypt_transactions.py
=======================
Reads transactions_user1_clean.csv, decrypts any AES-256-GCM encrypted
values in the 'purpose' column, and saves the result.

Encryption scheme (from utils/crypto_helpers.py):
  - Key derivation : PBKDF2-SHA256, 100 000 iterations
  - Cipher         : AES-256-GCM
  - Storage format : "__enc__:<base64(12-byte-nonce + ciphertext)>"
  - Plaintext data : returned as-is (legacy rows not encrypted)

Usage:
    python3 scratch/decrypt_transactions.py
    python3 scratch/decrypt_transactions.py --user 1 --out my_output.csv
"""

import os
import sys
import base64
import hashlib
import sqlite3
import argparse
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(BACKEND_DIR, "aurestra.db")
ENC_PREFIX  = "__enc__:"

# ── Crypto (mirrored from utils/crypto_helpers.py) ───────────────────────────

def derive_key(password: str, salt_hex: str) -> bytes:
    """PBKDF2-SHA256, 100 000 iterations → 32-byte AES-256 key."""
    salt = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)


def decrypt_field(value: str, key: bytes) -> str:
    """
    Decrypt a single field value.
    - Plaintext (no '__enc__:' prefix) → returned as-is.
    - Wrong key / corrupt data        → returns '[Decryption Failed]'.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    if not isinstance(value, str):
        value = str(value)
    if not value.startswith(ENC_PREFIX):
        return value          # already plaintext
    if not key:
        return "[Encrypted — no key]"
    try:
        raw      = base64.b64decode(value[len(ENC_PREFIX):])
        if len(raw) < 12:
            return "[Decryption Failed — too short]"
        nonce, ct = raw[:12], raw[12:]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except Exception as e:
        return f"[Decryption Failed: {e}]"


# ── Main ──────────────────────────────────────────────────────────────────────

def main(user_id: int, input_csv: str, output_csv: str):
    # 1. Load CSV
    if not os.path.exists(input_csv):
        print(f"❌  Input CSV not found: {input_csv}")
        sys.exit(1)

    df = pd.read_csv(input_csv)
    print(f"📂  Loaded {len(df)} rows from {input_csv}")

    if df.empty:
        print("⚠️   CSV has no data rows — nothing to decrypt.")
        print("     Run your cleaning script first to populate it.")
        sys.exit(0)

    # 2. Fetch decryption key + category names from DB
    if not os.path.exists(DB_PATH):
        print(f"❌  Database not found at: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT decryption_key, decryption_key_salt FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not row:
        print(f"❌  No user found with id={user_id}")
        conn.close()
        sys.exit(1)

    password, salt_hex = row
    if password and salt_hex:
        key = derive_key(password, salt_hex)
        print(f"🔑  Derived AES-256-GCM key for user {user_id}  (salt={salt_hex[:8]}…)")
    else:
        key = None
        print(f"⚠️   No decryption key stored for user {user_id}. Encrypted fields will be left as-is.")

    # 3. Fetch category names from DB using transaction ids
    #    (category_id was dropped by the cleaning script, so we re-join via the kept 'id' column)
    if "id" in df.columns:
        tx_ids = df["id"].dropna().astype(int).tolist()
        placeholders = ",".join("?" * len(tx_ids))
        cat_rows = conn.execute(f"""
            SELECT t.id AS tx_id, c.name AS category_name
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.id IN ({placeholders})
        """, tx_ids).fetchall()

        id_to_category = {r[0]: r[1] or "" for r in cat_rows}
        df["category"] = df["id"].map(id_to_category).fillna("")
        print(f"🏷️   Category names fetched for {len(id_to_category)} transactions")
    else:
        print("⚠️   No 'id' column found — skipping category lookup")

    conn.close()

    # 4. Decrypt 'purpose' column (and any other __enc__ columns present)
    encrypted_cols = ["purpose", "sender", "receiver", "notes"]
    for col in encrypted_cols:
        if col not in df.columns:
            continue
        before = df[col].astype(str).str.startswith(ENC_PREFIX).sum()
        if before > 0:
            print(f"🔓  Decrypting column '{col}'  ({before} encrypted values found)…")
        df[col] = df[col].apply(lambda v: decrypt_field(v, key))

    # 5. Save
    df.to_csv(output_csv, index=False)
    print(f"✅  Saved {len(df)} rows → {output_csv}")
    print("\n── Preview (first 5 rows) ──────────────────────────────────────")
    print(df.head(5).to_string(index=False))
    print(f"\nShape: {df.shape}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Decrypt __enc__ fields in transactions_user1_clean.csv"
    )
    parser.add_argument("--user", "-u", type=int, default=1,
                        help="User ID whose key is used (default: 1)")
    parser.add_argument("--in",  dest="input",  default=None,
                        help="Input CSV  (default: backend/transactions_user1_clean.csv)")
    parser.add_argument("--out", dest="output", default=None,
                        help="Output CSV (default: backend/transactions_user1_decrypted.csv)")
    args = parser.parse_args()

    input_csv  = args.input  or os.path.join(BACKEND_DIR, "transactions_user1_clean.csv")
    output_csv = args.output or os.path.join(BACKEND_DIR, "transactions_user1_decrypted.csv")

    main(args.user, input_csv, output_csv)
