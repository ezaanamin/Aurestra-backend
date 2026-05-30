# services/sms_service.py  —  SMS batch / single processing logic

import hashlib
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from database import db
from model import Transaction, SMSHistory, AccountBalance
from sms_parser import process_bank_sms, BankAlhabibSMSParser, generate_sms_hash
from transfer_matching import is_own_account_transfer_row
from ledger_sync import log_wallet_attribution
from fcm_utils import send_push_to_all


def process_single_sms(message: str, sender: str, meta: dict = None):
    """
    Parse and persist a single SMS.
    Returns (transaction, is_new).
    `meta` can contain device _id and date for deterministic hashing.
    """
    external_sms_hash = None
    if meta:
        device_sms_id = meta.get('_id') or meta.get('id')
        date_val      = meta.get('date')
        if device_sms_id and date_val:
            device_ts = _parse_timestamp(date_val)
            hash_input = f"{device_sms_id}|{sender}|{message}|{device_ts.isoformat()}"
            external_sms_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    transaction, is_new = process_bank_sms(message, sender, external_sms_hash=external_sms_hash)
    return transaction, is_new


def process_sms_batch(messages: list):
    """
    Insert raw messages into SMSHistory (deduplicated by sms_hash),
    then process only newly-inserted rows into Transactions.
    Returns stats dict.
    """
    stats = {
        'received':             len(messages),
        'inserted':             0,
        'processed':            0,
        'transactions_created': 0,
        'duplicates_ignored':   0,
        'errors':               0,
        'transactions':         [],
    }

    new_sms_ids = []

    for msg_data in messages:
        try:
            body            = msg_data.get('body') or msg_data.get('message')
            sender          = msg_data.get('address') or msg_data.get('sender')
            device_sms_id   = str(msg_data.get('_id', '') or msg_data.get('id', ''))
            device_ts       = _parse_timestamp(msg_data.get('date'))

            if not body or not sender:
                continue

            hash_input = f"{device_sms_id}|{sender}|{body}|{device_ts.isoformat()}"
            sms_hash   = hashlib.sha256(hash_input.encode()).hexdigest()

            new_msg = SMSHistory(
                device_sms_id=device_sms_id, sender=sender, body=body,
                device_timestamp=device_ts, sms_hash=sms_hash, status='pending',
            )
            db.session.add(new_msg)
            try:
                db.session.flush()
                new_sms_ids.append(new_msg.id)
                stats['inserted'] += 1
            except Exception as flush_err:
                db.session.rollback()
                if 'Duplicate entry' in str(flush_err) or 'UNIQUE constraint' in str(flush_err):
                    stats['duplicates_ignored'] += 1
                else:
                    stats['errors'] += 1
        except Exception:
            stats['errors'] += 1

    db.session.commit()

    pending = SMSHistory.query.filter(SMSHistory.id.in_(new_sms_ids)).all()
    for sms in pending:
        try:
            transaction, is_new = process_bank_sms(sms.body, sms.sender, external_sms_hash=sms.sms_hash)
            if transaction:
                if is_new:
                    stats['transactions_created'] += 1
                    stats['transactions'].append({
                        'id':     transaction.id,
                        'type':   transaction.type,
                        'amount': transaction.amount,
                        'date':   transaction.date.isoformat(),
                        'purpose': transaction.purpose,
                        'is_new': True,
                    })
                sms.status = 'processed'
            else:
                sms.status = 'ignored'
            stats['processed'] += 1
        except Exception:
            sms.status = 'error'
            stats['errors'] += 1

    db.session.commit()
    return stats


def _parse_timestamp(val) -> datetime:
    if isinstance(val, int):
        return datetime.fromtimestamp(val / 1000.0)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00'))
        except Exception:
            pass
    return datetime.utcnow()


def get_last_sms_sync():
    latest = Transaction.query.filter(
        Transaction.source.in_(['bank_sms', 'sms'])
    ).order_by(Transaction.created_at.desc()).first()

    if latest:
        return {"last_sync_time": latest.created_at.isoformat(), "source": "database"}
    return {"last_sync_time": None, "source": "empty_db"}
