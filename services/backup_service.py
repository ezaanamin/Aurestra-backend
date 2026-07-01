# services/backup_service.py
# Per-user encrypted backup and restore service.
#
# Encryption: AES-256-GCM
# Key derivation: PBKDF2-SHA256(user_decryption_key, backup_salt, 310_000 iter)
# File format: [4B magic "AUBE"][1B version][16B salt][12B nonce][ciphertext+16B GCM tag]
#
# The user's decryption key is NEVER stored. The backup file can only be
# decrypted by supplying the correct decryption key.

import os
import io
import json
import gzip
import struct
import datetime
import hashlib

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from database import db
from model import (
    UserBackup, Transaction, Category, AccountBalance,
    Budget, MonthlyBalance, FinancialInsight, StatementAnalysis,
)

# ── Constants ─────────────────────────────────────────────────────────────────
MAGIC        = b"AUBE"
FILE_VERSION = 1
ENC_VERSION  = "AES256GCM-v1"
APP_VERSION  = "1.0.0"
DB_VERSION   = 1
PBKDF2_ITER  = 310_000


# ── Key derivation ────────────────────────────────────────────────────────────
def _derive_backup_key(decryption_key: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from user's decryption key + a per-backup salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITER,
        backend=default_backend(),
    )
    return kdf.derive(decryption_key.encode("utf-8"))


# ── Encryption / Decryption ───────────────────────────────────────────────────
def _encrypt_payload(plaintext_bytes: bytes, decryption_key: str) -> bytes:
    """Encrypt bytes → AUBE file bytes."""
    salt  = os.urandom(16)
    nonce = os.urandom(12)
    key   = _derive_backup_key(decryption_key, salt)

    aesgcm     = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)  # includes 16B GCM tag

    return MAGIC + struct.pack("B", FILE_VERSION) + salt + nonce + ciphertext


def _decrypt_payload(file_bytes: bytes, decryption_key: str) -> bytes:
    """Decrypt AUBE file bytes → plaintext bytes. Raises ValueError on bad key/format."""
    if len(file_bytes) < 4 + 1 + 16 + 12 + 16:
        raise ValueError("File too short to be a valid AUBE backup.")
    if file_bytes[:4] != MAGIC:
        raise ValueError("Not a valid Aurestra backup file (bad magic bytes).")

    version = struct.unpack("B", file_bytes[4:5])[0]
    if version != FILE_VERSION:
        raise ValueError(f"Unsupported backup version {version}.")

    salt       = file_bytes[5:21]
    nonce      = file_bytes[21:33]
    ciphertext = file_bytes[33:]

    key = _derive_backup_key(decryption_key, salt)
    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        raise ValueError("Decryption failed — wrong decryption key or corrupted backup.")


# ── Data export / import ──────────────────────────────────────────────────────
def _serialize_row(obj) -> dict:
    """Safe serializer for any SQLAlchemy model row — uses __table__.columns."""
    out = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name, None)
        if hasattr(val, 'isoformat'):
            val = val.isoformat()
        out[col.name] = val
    return out


def _export_user_data(user_id: int) -> dict:
    """Export all user-owned rows as serialisable dicts."""

    def _rows_dict(query):
        return [r.to_dict() for r in query]

    def _rows_raw(query):
        return [_serialize_row(r) for r in query]

    def _safe(name, fn):
        try:
            return fn()
        except Exception as e:
            print(f"[Backup] Warning: skipping table '{name}' export: {e}")
            return []

    return {
        "version":     APP_VERSION,
        "user_id":     user_id,
        "exported_at": datetime.datetime.utcnow().isoformat(),
        "app_version": APP_VERSION,
        "db_version":  DB_VERSION,
        "tables": {
            "transactions":      _safe("transactions",      lambda: _rows_dict(Transaction.query.filter_by(user_id=user_id, is_deleted=False).all())),
            "categories":        _safe("categories",        lambda: _rows_dict(Category.query.filter_by(user_id=user_id).all())),
            "account_balances":  _safe("account_balances",  lambda: _rows_dict(AccountBalance.query.filter_by(user_id=user_id).all())),
            "budgets":           _safe("budgets",           lambda: _rows_raw(Budget.query.filter_by(user_id=user_id).all())),
            "monthly_balances":  _safe("monthly_balances",  lambda: _rows_raw(MonthlyBalance.query.filter_by(user_id=user_id).all())),
            "financial_insights":_safe("financial_insights",lambda: _rows_dict(FinancialInsight.query.filter_by(user_id=user_id).all())),
            "statement_analysis":_safe("statement_analysis",lambda: _rows_dict(StatementAnalysis.query.filter_by(user_id=user_id).all())),
        }
    }



def _count_tables(data: dict) -> dict:
    return {k: len(v) for k, v in data.get("tables", {}).items()}


# ── User-scoped storage path ──────────────────────────────────────────────────
def _user_backup_dir(user_id: int) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")
    user_dir = os.path.join(base, f"user_{user_id}")
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


# ── Public API ────────────────────────────────────────────────────────────────
def create_user_backup(user_id: int, decryption_key: str) -> UserBackup:
    """
    Export user data → gzip → AES-256-GCM encrypt → save to disk → DB record.
    Returns the new UserBackup ORM object.
    """
    data        = _export_user_data(user_id)
    counts      = _count_tables(data)
    json_bytes  = json.dumps(data, default=str).encode("utf-8")
    compressed  = gzip.compress(json_bytes, compresslevel=9)
    encrypted   = _encrypt_payload(compressed, decryption_key)

    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"backup_{timestamp}.enc"
    dir_path  = _user_backup_dir(user_id)
    file_path = os.path.join(dir_path, filename)

    with open(file_path, "wb") as f:
        f.write(encrypted)

    backup = UserBackup(
        user_id     = user_id,
        filename    = filename,
        file_path   = file_path,
        size_bytes  = len(encrypted),
        app_version = APP_VERSION,
        db_version  = DB_VERSION,
        enc_version = ENC_VERSION,
        status      = "completed",
        table_counts = json.dumps(counts),
    )
    db.session.add(backup)
    db.session.commit()
    return backup


def list_user_backups(user_id: int) -> list:
    """Return metadata for all backups belonging to this user. Never file paths."""
    backups = (
        UserBackup.query
        .filter_by(user_id=user_id)
        .order_by(UserBackup.created_at.desc())
        .all()
    )
    return [b.to_dict() for b in backups]


def get_latest_backup(user_id: int) -> dict | None:
    """Return the most recent backup metadata, or None if no backups exist."""
    backup = (
        UserBackup.query
        .filter_by(user_id=user_id)
        .order_by(UserBackup.created_at.desc())
        .first()
    )
    return backup.to_dict() if backup else None


def get_backup_or_404(user_id: int, backup_id: int) -> UserBackup:
    """Fetch backup, enforcing user ownership. Raises LookupError if not found/owned."""
    backup = UserBackup.query.filter_by(id=backup_id, user_id=user_id).first()
    if not backup:
        raise LookupError(f"Backup {backup_id} not found for this user.")
    return backup


def restore_user_backup(user_id: int, backup_id: int, decryption_key: str) -> dict:
    """
    Decrypt backup → validate → restore rows into DB.
    Returns a summary of restored row counts.

    Strategy:
    - For each table, UPSERT rows (insert or update by primary key).
    - Never wipe existing data — only merge/overwrite matching rows.
    - Transactions with is_deleted=True are skipped on restore.
    """
    backup = get_backup_or_404(user_id, backup_id)

    if not os.path.exists(backup.file_path):
        raise FileNotFoundError("Backup file not found on server. It may have been deleted.")

    with open(backup.file_path, "rb") as f:
        file_bytes = f.read()

    # Decrypt (raises ValueError on wrong key)
    compressed = _decrypt_payload(file_bytes, decryption_key)
    json_bytes = gzip.decompress(compressed)
    data       = json.loads(json_bytes.decode("utf-8"))

    if data.get("user_id") != user_id:
        raise PermissionError("Backup user_id mismatch — refusing restore.")

    restored = {}

    # ── Transactions ──────────────────────────────────────────────────────────
    restored["transactions"] = _restore_transactions(user_id, data["tables"].get("transactions", []))

    # ── Categories ────────────────────────────────────────────────────────────
    restored["categories"] = _restore_categories(user_id, data["tables"].get("categories", []))

    # ── Account Balances ──────────────────────────────────────────────────────
    restored["account_balances"] = _restore_account_balances(user_id, data["tables"].get("account_balances", []))

    # ── Budgets ───────────────────────────────────────────────────────────────
    restored["budgets"] = _restore_budgets(user_id, data["tables"].get("budgets", []))

    # ── Financial Insights ────────────────────────────────────────────────────
    restored["financial_insights"] = _restore_insights(user_id, data["tables"].get("financial_insights", []))

    db.session.commit()
    return {
        "backup_id":   backup_id,
        "restored_at": datetime.datetime.utcnow().isoformat(),
        "counts":      restored,
    }


def delete_user_backup(user_id: int, backup_id: int) -> None:
    """Delete a backup file + its DB record. Enforces user ownership."""
    backup = get_backup_or_404(user_id, backup_id)
    try:
        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)
    except OSError as e:
        print(f"[Backup] Warning: could not delete file {backup.file_path}: {e}")
    db.session.delete(backup)
    db.session.commit()


# ── Table restore helpers ─────────────────────────────────────────────────────
def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.datetime.fromisoformat(str(val))
    except Exception:
        return None


def _restore_transactions(user_id: int, rows: list) -> int:
    count = 0
    for r in rows:
        if r.get("is_deleted"):
            continue
        existing = Transaction.query.filter_by(id=r["id"], user_id=user_id).first()
        if existing:
            existing.purpose   = r.get("purpose")
            existing.sender    = r.get("sender")
            existing.receiver  = r.get("receiver")
            existing.notes     = r.get("notes")
            existing.amount    = r.get("amount")
            existing.type      = r.get("type")
            existing.category_id = r.get("category_id")
            existing.categorization_status = r.get("categorization_status", "pending")
        else:
            txn = Transaction(
                id=r["id"], user_id=user_id,
                source=r.get("source", "backup"),
                date=_parse_dt(r.get("date")),
                amount=r.get("amount", 0),
                type=r.get("type", "debit"),
                purpose=r.get("purpose"),
                sender=r.get("sender"),
                receiver=r.get("receiver"),
                transaction_id=r.get("transaction_id"),
                transaction_hash=r.get("transaction_hash"),
                sms_hash=r.get("sms_hash"),
                notes=r.get("notes"),
                categorization_status=r.get("categorization_status", "pending"),
                category_id=r.get("category_id"),
                account_balance_source=r.get("account_balance_source"),
                balance_applied=r.get("balance_applied", True),
                is_deleted=False,
                is_spam=r.get("is_spam", False),
            )
            db.session.merge(txn)
        count += 1
    return count


def _restore_categories(user_id: int, rows: list) -> int:
    count = 0
    for r in rows:
        existing = Category.query.filter_by(id=r["id"], user_id=user_id).first()
        if not existing:
            cat = Category(
                id=r["id"], user_id=user_id,
                name=r.get("name", "Unknown"),
                icon=r.get("icon"),
                color=r.get("color"),
                is_default=r.get("is_default", False),
            )
            db.session.merge(cat)
        count += 1
    return count


def _restore_account_balances(user_id: int, rows: list) -> int:
    count = 0
    for r in rows:
        existing = AccountBalance.query.filter_by(source=r["source"], user_id=user_id).first()
        if existing:
            existing.current_balance = r.get("current_balance", existing.current_balance)
        else:
            ab = AccountBalance(
                id=r["id"], user_id=user_id,
                source=r["source"],
                display_name=r.get("display_name"),
                current_balance=r.get("current_balance", 0.0),
            )
            db.session.merge(ab)
        count += 1
    return count


def _restore_budgets(user_id: int, rows: list) -> int:
    count = 0
    for r in rows:
        existing = Budget.query.filter_by(user_id=user_id).first()
        if not existing:
            b = Budget(
                id=r.get("id"), user_id=user_id,
                income=r.get("income", 0.0),
                savings_goal=r.get("savings_goal", 0.0),
                total_budget=r.get("total_budget", 0.0),
            )
            db.session.merge(b)
        count += 1
    return count


def _restore_insights(user_id: int, rows: list) -> int:
    count = 0
    for r in rows:
        existing = FinancialInsight.query.filter_by(user_id=user_id, month=r["month"]).first()
        if not existing:
            fi = FinancialInsight(
                id=r.get("id"), user_id=user_id,
                month=r["month"],
                content=r.get("content", ""),
                metrics_json=json.dumps(r.get("metrics", {})),
                tags=r.get("tags"),
            )
            db.session.merge(fi)
        count += 1
    return count
