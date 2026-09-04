"""Persist device notifications without SMS parsing dependencies."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError

from database import db
from model import AccountBalance, DeviceNotification

logger = logging.getLogger(__name__)


def _accounts_api_list() -> list[dict[str, Any]]:
    rows = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
    return [r.to_dict() for r in rows]


def ingest_notification_for_user(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Ingest and persist DeviceNotification for logs.
    SMS/notification-to-transaction parsing has been disabled and removed.
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

    return {
        "device_notification_id": row.id,
        "created": created_new,
        "transaction_id": None,
        "parsed_transaction": False,
        "status": "success",
        "is_self_transfer": False,
        "needs_organization": False,
        "notify_user": False,
        "accounts": _accounts_api_list(),
    }


def list_notifications_for_user(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 100), 500))
    rows = (
        DeviceNotification.query.filter_by(user_id=user_id)
        .order_by(DeviceNotification.created_at.desc())
        .limit(lim)
        .all()
    )
    return [r.to_dict() for r in rows]
