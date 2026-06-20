"""Apply account balance changes when a pending transaction is categorized (Organize)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from sqlalchemy import func

from account_matching import match_account_for_notification
from database import db
from model import AccountBalance, Transaction
from transfer_matching import (
    counterparty_matches_other_wallet,
    is_own_account_transfer_row,
    opposite_internal_transfer_leg_exists,
)

logger = logging.getLogger(__name__)

WALLET_ATTRIBUTION_TAG = "[WALLET_ATTRIBUTION]"

# Canonical Easypaisa slug is "easypaisa"; older DBs / typos may use these — keep one balance.
_EASYPAY_SLUGS = frozenset({"easypaisa", "easypasia", "easy_paisa"})


def find_account_balance_row_for_slug(slug: str) -> AccountBalance | None:
    """
    Return an existing AccountBalance for this wallet.

    Easypaisa: multiple `source` values (easypaisa / easypasia / easy_paisa) may exist after typos
    or duplicate rows. If more than one row exists, prefer the one with the highest balance so
    ingest does not keep posting to an empty canonical row while the real balance sits on a typo.
    """
    s = (slug or "sms").strip().lower()
    if s not in _EASYPAY_SLUGS:
        return AccountBalance.query.filter_by(source=s).first()

    rows = AccountBalance.query.filter(AccountBalance.source.in_(tuple(_EASYPAY_SLUGS))).all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]

    def _easypay_tie_order(src: str) -> int:
        # When balances tie, prefer older typo spellings so we do not flip arbitrarily.
        return {"easypasia": 0, "easy_paisa": 1, "easypaisa": 2}.get((src or "").strip().lower(), 99)

    def _sort_key(r: AccountBalance) -> tuple[float, int, int]:
        bal = float(r.current_balance or 0.0)
        oid = int(r.id or 0)
        return (-bal, _easypay_tie_order(getattr(r, "source", "") or ""), oid)

    return sorted(rows, key=_sort_key)[0]


def _account_balance_snapshot(slug: object) -> dict[str, object]:
    """Human-readable row from account_balances for debug logs."""
    s = (str(slug or "")).strip().lower()
    if not s:
        return {}
    row = find_account_balance_row_for_slug(s)
    if not row:
        return {
            "acct_row": f"(no row yet for slug={s!r}; commit runs ensure_account_balance_row → new wallet row)",
        }
    bal = float(row.current_balance or 0.0)
    kind = (row.account_kind or "").strip() or "unknown"
    label = (row.display_name or row.source or s)[:100]
    out: dict[str, object] = {
        "acct_slug": row.source,
        "acct_display": label,
        "acct_type": kind,
        "acct_balance_num": round(bal, 4),
        "acct_balance_pkr": f"PKR {bal:,.2f}",
    }
    if row.source != s:
        out["acct_alias_note"] = f"requested_slug={s} → using existing row source={row.source}"
    return out


def _parse_amount_for_attribution(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return None


def _transaction_after_balance_fields(
    acct_balance_num: object, amount: object, txn_type: object
) -> dict[str, object] | None:
    """Projected wallet balance after applying this txn to acct_balance_num (debit −, credit +)."""
    bal = _parse_amount_for_attribution(acct_balance_num)
    amt = _parse_amount_for_attribution(amount)
    if bal is None or amt is None:
        return None
    kind = (str(txn_type or "")).strip().lower()
    if kind == "debit":
        after = bal - amt
    elif kind == "credit":
        after = bal + amt
    else:
        return None
    after_r = round(float(after), 4)
    return {
        "transaction_after_balance_num": after_r,
        "transaction_after_balance_pkr": f"PKR {after_r:,.2f}",
    }


def _format_wallet_attribution_table(event: str, merged: dict[str, object], value_max_len: int = 100) -> str:
    """Aligned two-column table for terminal / log debugging."""
    rows: list[tuple[str, str]] = []
    for key in sorted(merged.keys()):
        val = merged[key]
        if val is None:
            continue
        s = str(val).replace("\n", " ").strip()
        if len(s) > value_max_len:
            s = s[: max(0, value_max_len - 3)] + "..."
        rows.append((key, s))
    if not rows:
        return f"{WALLET_ATTRIBUTION_TAG} {event}\n  (no fields)"

    key_width = min(max(len(k) for k, _ in rows), 34)
    key_width = max(key_width, 10)
    sep_width = key_width + 3 + min(value_max_len, max(len(v) for _, v in rows) if rows else 40)
    sep_width = min(max(sep_width, 48), key_width + 3 + value_max_len)

    out_lines = [
        f"{WALLET_ATTRIBUTION_TAG}  {event}",
        "-" * sep_width,
    ]
    for k, v in rows:
        out_lines.append(f"  {k.ljust(key_width)} | {v}")
    out_lines.append("-" * sep_width)
    return "\n".join(out_lines)


def log_wallet_attribution(event: str, **fields: object) -> None:
    """
    Wallet attribution for debugging: prints a two-column table (field | value).
    Adds acct_* snapshot when resolved_slug or resolved_wallet_slug is present.
    """
    merged: dict[str, object] = dict(fields)
    slug_for_snap = merged.get("resolved_slug") or merged.get("resolved_wallet_slug")
    if slug_for_snap:
        for k, v in _account_balance_snapshot(slug_for_snap).items():
            if k not in merged or merged[k] is None or merged[k] == "":
                merged[k] = v

    after_fields = _transaction_after_balance_fields(
        merged.get("acct_balance_num"),
        merged.get("amount"),
        merged.get("txn_type"),
    )
    if after_fields:
        for k, v in after_fields.items():
            if k not in merged or merged[k] is None or merged[k] == "":
                merged[k] = v

    block = _format_wallet_attribution_table(event, merged, value_max_len=140)
    logger.info("\n%s", block)
    print(block, flush=True)


def resolve_ingest_balance_slug(sender: str, balance_source_override: str | None) -> str:
    """Same routing rules as sms_parser.process_bank_sms (which wallet slug to use)."""
    if balance_source_override:
        return str(balance_source_override).strip().lower()
    if sender in ["BAHL", "BankALHabib", "AL-Habib", "8810", "8812", "8815"]:
        return "bank"
    if sender == "Easypaisa":
        return "easypaisa"
    if sender == "JazzCash":
        return "jazzcash"
    sl = (sender or "").lower()
    if "jazzcash" in sl or "mobilink" in sl:
        return "jazzcash"
    if "easypaisa" in sl or "phoenix" in sl or "telenor" in sl:
        return "easypaisa"
    if (
        "bahl" in sl
        or "habib" in sl
        or "al-habib" in sl
        or "al habib" in sl
        or "8810" in sl
        or "8812" in sl
        or "8815" in sl
        or "obdx" in sl
        or "digx" in sl
    ):
        return "bank"
    return "sms"


def resolve_balance_slug_for_ingest(
    sender: str,
    message: str,
    balance_source_override: str | None,
    transaction_data: dict | None = None,
) -> str:
    """
    Pick wallet slug for a new SMS/notification row.
    Order: explicit override → SMS *sender* institution (BAHL / Easypaisa / …) → body keywords
    (for generic senders like app notifications) → linked-account keyword match last (so “BAHL” in
    the body of an Easypaisa transfer does not steal routing to the bank wallet).
    """
    sender_in = (sender or "").strip()
    msg_preview = (message or "")[:160]

    if balance_source_override:
        out = str(balance_source_override).strip().lower()
        route = "balance_source_override"
        msg_l = (message or "").lower()
        snd_l = sender_in.lower()
        # Notification pipeline can still pass "bank" because "Bank AlHabib" appears in the *receiver* line.
        if out == "bank" and (
            snd_l == "easypaisa"
            or re.search(r"\beasypaisa\b", msg_l[:400])
        ):
            out = "easypaisa"
            route = "override_corrected_easypaisa_leg"
        log_wallet_attribution(
            "INGEST_WALLET_SLUG",
            resolved_slug=out,
            route=route,
            sms_sender_peer=sender_in,
            amount=str(transaction_data.get("amount")) if transaction_data else None,
            txn_type=str(transaction_data.get("type")) if transaction_data else None,
            msg_preview=msg_preview,
        )
        return out

    sender = sender_in
    sl = resolve_ingest_balance_slug(sender, None)
    if sl != "sms":
        log_wallet_attribution(
            "INGEST_WALLET_SLUG",
            resolved_slug=sl,
            route="sms_sender_peer",
            sms_sender_peer=sender,
            amount=str(transaction_data.get("amount")) if transaction_data else None,
            txn_type=str(transaction_data.get("type")) if transaction_data else None,
            msg_preview=msg_preview,
        )
        return sl

    parts = [sender, message or ""]
    if transaction_data:
        parts.extend(
            [
                str(transaction_data.get("notes") or ""),
                str(transaction_data.get("receiver") or ""),
                str(transaction_data.get("sender") or ""),
                str(transaction_data.get("purpose") or ""),
                str(transaction_data.get("source") or ""),
            ]
        )
    hay = " ".join(parts).lower()
    route = "body_keywords"
    out = "sms"
    if "easypaisa" in hay or "phoenix" in hay:
        out = "easypaisa"
    elif "jazzcash" in hay or "mobilink" in hay:
        out = "jazzcash"
    elif (
        "bahl" in hay
        or "al habib" in hay
        or "habib" in hay
        or "8810" in hay
        or "8812" in hay
        or "8815" in hay
        or "obdx" in hay
        or "digx" in hay
    ):
        out = "bank"
    else:
        accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
        matched = match_account_for_notification(accounts, title="", text=hay, package_name="")
        if matched:
            slug = (getattr(matched, "source", None) or "").strip().lower()
            if slug:
                out = slug
                route = "linked_account_match_keywords"

    log_wallet_attribution(
        "INGEST_WALLET_SLUG",
        resolved_slug=out,
        route=route,
        sms_sender_peer=sender,
        parsed_txn_sender=str(transaction_data.get("sender")) if transaction_data else None,
        parsed_txn_receiver=str(transaction_data.get("receiver")) if transaction_data else None,
        amount=str(transaction_data.get("amount")) if transaction_data else None,
        txn_type=str(transaction_data.get("type")) if transaction_data else None,
        msg_preview=msg_preview,
    )
    return out


def ensure_account_balance_row(source: str) -> AccountBalance:
    source = (source or "sms").strip().lower()
    found = find_account_balance_row_for_slug(source)
    if found:
        return found
    create_slug = source
    if create_slug in _EASYPAY_SLUGS:
        create_slug = "easypaisa"
    max_ord = db.session.query(func.max(AccountBalance.sort_order)).scalar()
    max_ord = int(max_ord) if max_ord is not None else 0
    dn = "Bank (SMS)" if create_slug == "bank" else create_slug.replace("_", " ").title()
    ak = "bank" if create_slug == "bank" else "mobile_wallet"
    if create_slug == "easypaisa":
        mk = list(dict.fromkeys(["easypaisa", "easypasia", "easy_paisa", "phoenix"]))
    else:
        mk = [create_slug]
    balance = AccountBalance(
        source=create_slug,
        display_name=dn,
        holder_name="",
        account_kind=ak,
        match_keywords=json.dumps(mk),
        accent_color="#6366F1",
        sort_order=max_ord + 1,
        current_balance=0.0,
    )
    db.session.add(balance)
    db.session.flush()
    return balance


def infer_balance_slug_for_transaction(txn: Transaction) -> str:
    """
    Wallet for categorize-time inference. Prefer persisted ingest slug, then SMS sender
    institution, then body keywords — *not* keyword-match against bank display_name using
    BAHL text that belongs to the counterparty field of an Easypaisa SMS.
    """
    hay = " ".join(
        filter(
            None,
            [
                getattr(txn, "sender", None),
                getattr(txn, "receiver", None),
                getattr(txn, "notes", None),
                getattr(txn, "purpose", None),
                getattr(txn, "source", None),
            ],
        )
    ).lower()
    stored = (getattr(txn, "account_balance_source", None) or "").strip().lower()
    if stored:
        return stored

    sender = getattr(txn, "sender", None) or ""
    sl = resolve_ingest_balance_slug(sender, None)
    if sl != "sms":
        return sl

    if "easypaisa" in hay or "phoenix" in hay:
        return "easypaisa"
    if "jazzcash" in hay or "mobilink" in hay:
        return "jazzcash"
    if (
        "bahl" in hay
        or "al habib" in hay
        or "habib" in hay
        or "8810" in hay
        or "8812" in hay
        or "8815" in hay
        or "obdx" in hay
        or "digx" in hay
    ):
        return "bank"
    if (getattr(txn, "source", None) or "").strip().lower() == "bank":
        return "bank"

    accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()
    matched = match_account_for_notification(accounts, title="", text=hay, package_name="")
    if matched:
        slug = (getattr(matched, "source", None) or "").strip().lower()
        if slug:
            return slug
    return "sms"


def _apply_balance_delta(
    balance: AccountBalance,
    slug: str,
    delta: float,
    *,
    respect_manual_lock: bool = True,
) -> None:
    """Apply signed delta to one wallet. When respect_manual_lock is True, skip manual-locked rows."""
    if respect_manual_lock and getattr(balance, "is_manual", False):
        logger.info("Categorize ledger: skip delta on manual-locked account %s", slug)
        return
    new_bal = float(balance.current_balance or 0) + delta
    if new_bal < 0:
        new_bal = 0.0
    balance.current_balance = new_bal
    balance.last_updated = datetime.now()
    if not respect_manual_lock:
        balance.is_manual = False


def _primary_slug_for_ledger(
    txn: Transaction, balance_slug_override: str | None
) -> str:
    ow = (balance_slug_override or "").strip().lower()
    if ow:
        return ow
    # Ingest already decided which institution owns this leg (Easypaisa vs BAHL vs …).
    stored = (getattr(txn, "account_balance_source", None) or "").strip().lower()
    if stored:
        return stored
    return infer_balance_slug_for_transaction(txn)


def apply_pending_transaction_ledger(
    txn: Transaction,
    balance_slug_override: str | None = None,
    *,
    respect_manual_lock: bool = True,
) -> bool:
    """
    First-time categorize: update wallet balances (credit +, debit -).
    Own-account / self-transfer: adjust *both* wallets (e.g. Easypaisa −amount, bank +amount).
    Idempotent via balance_applied.

    respect_manual_lock: set False for SMS/notification ingest so authoritative parsed txns
    still move balances (manual lock otherwise blocks deltas on user-seeded balances).
    """
    if getattr(txn, "balance_applied", None) is None:
        return False
    if txn.balance_applied:
        return False
    if getattr(txn, "is_deleted", False) or getattr(txn, "is_spam", False):
        txn.balance_applied = True
        return False

    amt = float(txn.amount or 0)
    if amt <= 0:
        txn.balance_applied = True
        return False

    kind = (txn.type or "").strip().lower()
    if kind not in ("credit", "debit"):
        logger.warning("Categorize ledger: unknown type %r — skipping", txn.type)
        txn.balance_applied = True
        return False

    primary = _primary_slug_for_ledger(txn, balance_slug_override)
    accounts = AccountBalance.query.order_by(AccountBalance.sort_order, AccountBalance.id).all()

    cp = None
    if kind == "debit":
        cp = getattr(txn, "receiver", None)
    else:
        cp = getattr(txn, "sender", None)

    other_acc = None
    if is_own_account_transfer_row(txn):
        other_acc = counterparty_matches_other_wallet(cp, accounts, exclude_source=primary)

    if other_acc:
        oslug = (getattr(other_acc, "source", None) or "").strip().lower()
        if oslug and oslug != primary:
            p_bal = ensure_account_balance_row(primary)
            if opposite_internal_transfer_leg_exists(txn, p_bal, other_acc, accounts):
                logger.info(
                    "Self-transfer: opposite SMS leg already in DB — applying single-wallet ledger on %s only",
                    primary,
                )
                other_acc = None

    plan_other = (getattr(other_acc, "source", None) or "").strip().lower() if other_acc else ""
    log_wallet_attribution(
        "CATEGORIZE_LEDGER_PLAN",
        transaction_id=getattr(txn, "id", None),
        resolved_slug=primary,
        primary_slug=primary,
        matched_counterparty_slug=plan_other or None,
        counterparty_field=str(cp)[:200] if cp else None,
        txn_type=kind,
        amount=amt,
        stored_account_balance_source=(getattr(txn, "account_balance_source", None) or "") or None,
        purpose=(getattr(txn, "purpose", None) or "") or None,
        self_transfer=is_own_account_transfer_row(txn),
        two_sided=bool(plan_other and plan_other != primary),
    )

    if other_acc:
        oslug = (getattr(other_acc, "source", None) or "").strip().lower()
        if oslug and oslug != primary:
            p_bal = ensure_account_balance_row(primary)
            o_bal = ensure_account_balance_row(oslug)
            if kind == "debit":
                # Funds left primary wallet, arrived at counterparty wallet.
                _apply_balance_delta(p_bal, primary, -amt, respect_manual_lock=respect_manual_lock)
                _apply_balance_delta(o_bal, oslug, amt, respect_manual_lock=respect_manual_lock)
                logger.info(
                    "Categorize self-transfer: debit %.2f %s → −%.2f / %s → +%.2f",
                    amt,
                    primary,
                    amt,
                    oslug,
                    amt,
                )
            else:
                # Credit on primary (destination); funds came from counterparty wallet.
                _apply_balance_delta(p_bal, primary, amt, respect_manual_lock=respect_manual_lock)
                _apply_balance_delta(o_bal, oslug, -amt, respect_manual_lock=respect_manual_lock)
                logger.info(
                    "Categorize self-transfer: credit %.2f %s → +%.2f / %s → −%.2f",
                    amt,
                    primary,
                    amt,
                    oslug,
                    amt,
                )
            txn.account_balance_source = primary
            txn.balance_applied = True
            return True

    # Single-wallet movement (external spend / income)
    slug = primary
    balance = ensure_account_balance_row(slug)
    if kind == "credit":
        _apply_balance_delta(balance, slug, amt, respect_manual_lock=respect_manual_lock)
        logger.info(
            "Categorize ledger: credit +%.2f → %s balance %.2f",
            amt,
            slug,
            balance.current_balance,
        )
    else:
        _apply_balance_delta(balance, slug, -amt, respect_manual_lock=respect_manual_lock)
        logger.info(
            "Categorize ledger: debit −%.2f → %s balance %.2f",
            amt,
            slug,
            balance.current_balance,
        )

    txn.account_balance_source = slug
    txn.balance_applied = True
    return True
