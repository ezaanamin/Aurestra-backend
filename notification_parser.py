"""Persist device notifications and parse transactional ones via sms_parser."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy.exc import IntegrityError

from account_matching import match_account_for_notification
from database import db
from model import AccountBalance, DeviceNotification, Transaction
from sms_parser import (
    BankAlhabibSMSParser,
    extract_money_amount_from_text,
    infer_transaction_kind_from_text,
    notification_hint_is_usable,
    process_bank_sms,
    text_contains_money_amount,
)

from transfer_matching import is_own_account_transfer_row

logger = logging.getLogger(__name__)


def _accounts_api_list() -> list[dict[str, Any]]:
    rows = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
    return [r.to_dict() for r in rows]


def build_combined_for_bank_parse(payload: dict[str, Any]) -> str:
    """
    For Google Messages / WhatsApp threads from BAHL short codes (8810, 8815), the notification
    title is often just the sender number — parse using the message body + MessagingStyle lines only.
    """
    peer = payload.get("messagingBankSmsPeer") is True
    title = (payload.get("title") or "").strip()
    text = (payload.get("text") or "").strip()
    combined = (payload.get("combinedMessage") or "").strip()
    fallback = "\n".join(x for x in (title, text) if x).strip()

    if not peer:
        return combined if combined else fallback

    parts: list[str] = []
    if text:
        parts.append(text)
    lines = payload.get("messagingLines")
    if isinstance(lines, list):
        for line in lines:
            line = (line or "").strip()
            if line and line not in parts:
                parts.append(line)
    body = "\n".join(parts).strip()
    if body:
        return body
    return combined if combined else fallback


def build_notification_parse_hint(payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    Build hint for process_bank_sms from Kotlin/React `parsed` plus notification text.

    Amount may come from native code or by scanning title/body for PKR/Rs (credits often omit txn keywords).
    """
    hay_full = (
        f"{payload.get('combinedMessage') or ''} "
        f"{payload.get('title') or ''} "
        f"{payload.get('text') or ''}"
    ).strip()
    if not hay_full:
        return None

    raw = payload.get("parsed")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if raw is not None and not isinstance(raw, dict):
        raw = None

    amt = None
    if isinstance(raw, dict) and raw.get("amount") is not None:
        try:
            amt = float(raw.get("amount"))
        except (TypeError, ValueError):
            amt = None
    if amt is None or amt <= 0:
        amt = extract_money_amount_from_text(hay_full)
    if amt is None or amt <= 0:
        return None

    ttype = ""
    cp = None
    ts = None
    if isinstance(raw, dict):
        ttype = (raw.get("transactionType") or raw.get("transaction_type") or "").strip().lower()
        cp = (raw.get("counterparty") or "").strip() or None
        ts = raw.get("timestampIso") or raw.get("timestamp_iso")
    post_ms = payload.get("postTime")

    hay = hay_full.lower()

    if ttype not in ("debit", "credit"):
        debit_hints = (
            "sent to",
            "debited",
            "paid to",
            "transfer to",
            "fund transfer",
            "money sent",
            "you sent",
            "successful transfer",
            "txn debited",
            "withdrawal",
            "purchase",
            "pos ",
            "atm ",
        )
        credit_hints = (
            "credited",
            "received from",
            "received in",
            "received on",
            "money received",
            "has been credited",
            "incoming",
            "deposit",
            "cash in",
            "balance received",
            "amount received",
            "successfully received",
            "received pkr",
            "received rs",
            "credit alert",
            "incoming funds",
            "ibft",
            "salary",
            "payroll",
            "wages",
            "payment received",
            "funds received",
        )
        d_score = sum(1 for h in debit_hints if h in hay)
        c_score = sum(1 for h in credit_hints if h in hay)
        if d_score > c_score:
            ttype = "debit"
        elif c_score > d_score:
            ttype = "credit"
        elif d_score > 0 and c_score > 0:
            if "debited" in hay or "sent to" in hay:
                ttype = "debit"
            elif "credited" in hay or "received from" in hay or "received in" in hay:
                ttype = "credit"

    if ttype not in ("debit", "credit"):
        inferred = infer_transaction_kind_from_text(hay_full)
        if inferred:
            ttype = inferred

    if ttype not in ("debit", "credit"):
        return None

    return {
        "amount": amt,
        "type": ttype,
        "counterparty": cp,
        "timestamp_iso": ts,
        "post_time_ms": post_ms,
    }


def _package_sender_hint(package_name: str) -> str:
    pkg = (package_name or "").lower()
    if "phoenix" in pkg or "easypaisa" in pkg:
        return "Easypaisa"
    if "jazzcash" in pkg or "mobilinkmicrofinance" in pkg:
        return "JazzCash"
    if "bahl" in pkg or "obdx" in pkg or "digx" in pkg:
        return "BAHL"
    return package_name or "NOTIFICATION"


def ingest_notification_for_user(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Upsert DeviceNotification, then run transaction parsing when appropriate.

    Expected payload keys: dedupeHash, packageName, notificationKey, title, text,
    combinedMessage, postTime, messagingLines (list[str]), parsed (dict),
    isTransactional, isMonitoredBankApp, messagingBankSmsPeer (bool), balance_source_hint.
    """
    dedupe = (payload.get("dedupeHash") or "").strip()
    if not dedupe:
        raw = "|".join(
            [
                str(payload.get("packageName") or ""),
                str(payload.get("title") or ""),
                str(payload.get("text") or ""),
                str(payload.get("postTime") or ""),
            ]
        )
        dedupe = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    pkg = payload.get("packageName") or ""
    title = payload.get("title") or ""
    body = payload.get("text") or ""
    combined = (payload.get("combinedMessage") or "").strip()
    if not combined:
        combined = "\n".join(x for x in (title, body) if x).strip()

    post_raw = payload.get("postTime")
    try:
        post_ms = int(float(post_raw)) if post_raw is not None else None
    except (TypeError, ValueError):
        post_ms = None

    msg_lines = payload.get("messagingLines")
    if msg_lines is not None and not isinstance(msg_lines, list):
        msg_lines = None
    messaging_json = json.dumps(msg_lines) if msg_lines else None

    client_parsed = payload.get("parsed")
    client_parsed_json = json.dumps(client_parsed) if client_parsed is not None else None

    notif_key = payload.get("notificationKey")

    row = DeviceNotification.query.filter_by(user_id=user_id, dedupe_hash=dedupe).first()
    created_new = False

    if row is None:
        row = DeviceNotification(
            user_id=user_id,
            notification_key=notif_key,
            dedupe_hash=dedupe,
            package_name=pkg,
            title=title,
            body=body,
            combined_message=combined,
            post_time_ms=post_ms,
            messaging_style_json=messaging_json,
            client_parsed_json=client_parsed_json,
            is_transactional=False,
            parse_attempted=False,
            transaction_id=None,
        )
        db.session.add(row)
        try:
            db.session.commit()
            created_new = True
        except IntegrityError:
            db.session.rollback()
            row = DeviceNotification.query.filter_by(user_id=user_id, dedupe_hash=dedupe).first()
            if row is None:
                raise
    else:
        row.notification_key = notif_key or row.notification_key
        row.package_name = pkg or row.package_name
        row.title = title
        row.body = body
        row.combined_message = combined
        row.post_time_ms = post_ms if post_ms is not None else row.post_time_ms
        if messaging_json is not None:
            row.messaging_style_json = messaging_json
        if client_parsed_json is not None:
            row.client_parsed_json = client_parsed_json
        db.session.commit()

    combined_for_parse = build_combined_for_bank_parse(payload).strip()
    if not combined_for_parse:
        combined_for_parse = (row.combined_message or "").strip()

    parse_hint = build_notification_parse_hint(payload)
    money_in_body = text_contains_money_amount(combined_for_parse)
    should_parse = (
        BankAlhabibSMSParser.is_transaction_sms(combined_for_parse)
        or (payload.get("isMonitoredBankApp") and payload.get("isTransactional"))
        or notification_hint_is_usable(parse_hint)
        or (
            payload.get("isMonitoredBankApp")
            and payload.get("is_money_related")
            and money_in_body
        )
    )

    tx_id = None
    tx = None
    if row.transaction_id:
        ptx = Transaction.query.get(row.transaction_id)
        return {
            "device_notification_id": row.id,
            "created": created_new,
            "transaction_id": row.transaction_id,
            "parsed_transaction": True,
            "status": "success",
            "is_self_transfer": bool(ptx and is_own_account_transfer_row(ptx)),
            "needs_organization": True,
            "notify_user": True,
            "accounts": _accounts_api_list(),
        }

    if should_parse:
        row.parse_attempted = True
        sender_hint = _package_sender_hint(pkg)
        if payload.get("messagingBankSmsPeer"):
            # Align with sms_parser routing for BAHL SMS short codes forwarded into Messages.
            sender_hint = "8810"
        accounts = AccountBalance.query.all()
        pkg_l = (pkg or "").lower()
        text_l = combined_for_parse.lower()

        balance_source = (payload.get("balance_source_hint") or "").strip() or None

        # Originating app / institution MUST win before match_account_for_notification:
        # Easypaisa "sent to … Bank AlHabib …" still contains "habib" — naive keyword match steals routing to bank.
        if not balance_source:
            if re.search(r"\beasypaisa\b", text_l) or "phoenix" in pkg_l or "easypaisa" in pkg_l:
                balance_source = "easypaisa"
            elif "jazzcash" in text_l or "mobilinkmicrofinance" in pkg_l or "jazzcash" in pkg_l:
                balance_source = "jazzcash"
            elif payload.get("messagingBankSmsPeer"):
                balance_source = "bank"
            elif re.search(
                r"\b(bahl|bank\s*al\s*habib|al-?habib|8810|8812|8815|obdx|digx)\b",
                text_l,
                re.I,
            ) or "bahl" in pkg_l or "obdx" in pkg_l or "digx" in pkg_l:
                balance_source = "bank"

        if not balance_source:
            matched = match_account_for_notification(
                accounts, title=title, text=combined_for_parse, package_name=pkg
            )
            balance_source = matched.source if matched else None
        if not balance_source:
            pl = pkg_l
            if "phoenix" in pl or "easypaisa" in pl:
                balance_source = "easypaisa"
            elif "jazzcash" in pl or "mobilinkmicrofinance" in pl:
                balance_source = "jazzcash"
            elif "bahl" in pl or "obdx" in pl or "digx" in pl:
                balance_source = "bank"
            elif payload.get("messagingBankSmsPeer"):
                balance_source = "bank"

        ext_hash = hashlib.sha256(f"n|{user_id}|{dedupe}".encode()).hexdigest()
        tx, _is_new = process_bank_sms(
            combined_for_parse,
            sender=sender_hint,
            external_sms_hash=ext_hash,
            transaction_source_override="notification",
            balance_source_override=balance_source,
            notification_parse_hint=parse_hint,
            notification_relaxed_gate=True,
        )
        if tx:
            tx_id = tx.id
            row.is_transactional = True
            row.transaction_id = tx_id
        else:
            row.is_transactional = False
            tx = None
    else:
        row.parse_attempted = False

    db.session.commit()

    # Parsed txns: wallet balances are updated at ingest (same rules as Organize ledger).
    parsed_ok = tx_id is not None
    out: dict[str, Any] = {
        "device_notification_id": row.id,
        "created": created_new,
        "transaction_id": tx_id,
        "parsed_transaction": parsed_ok,
        "status": "success",
        "is_self_transfer": bool(tx and is_own_account_transfer_row(tx)),
        "needs_organization": parsed_ok,
        "notify_user": parsed_ok,
        "accounts": _accounts_api_list(),
    }
    return out


def list_notifications_for_user(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 100), 500))
    rows = (
        DeviceNotification.query.filter_by(user_id=user_id)
        .order_by(DeviceNotification.created_at.desc())
        .limit(lim)
        .all()
    )
    return [r.to_dict() for r in rows]
