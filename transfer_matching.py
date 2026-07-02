"""Detect transfers between the user's own wallets for auto-categorization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from model import Transaction

# User-facing label for movements between the user's own linked accounts/wallets.
SELF_TRANSFER_PURPOSE = "Self-transfer"
# Older rows / migrations may still use this string.
LEGACY_SELF_TRANSFER_PURPOSE = "Own account transfer"

# Ledger sync: when only one SMS leg exists, we adjust the matched wallet's balance;
# tag documents the delta so we can undo if the other leg arrives later.
MIRROR_ADJ_RE = re.compile(r"\[mirror_adj:([^:\]]+):([-+]?\d+(?:\.\d+)?)\]")


def _amount_close(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(float(a) - float(b)) <= tol


def parse_mirror_adjs(notes: str | None) -> list[tuple[str, float]]:
    if not notes:
        return []
    out: list[tuple[str, float]] = []
    for m in MIRROR_ADJ_RE.finditer(notes):
        try:
            out.append((m.group(1).strip(), float(m.group(2))))
        except ValueError:
            continue
    return out


def _strip_one_mirror_adj(notes: str, slug: str, delta: float) -> str:
    out = notes or ""
    for m in MIRROR_ADJ_RE.finditer(out):
        if m.group(1).strip().lower() != slug.strip().lower():
            continue
        try:
            d = float(m.group(2))
        except ValueError:
            continue
        if _amount_close(d, delta):
            return (out[: m.start()] + out[m.end() :]).strip()
    return out.strip()


def opposite_internal_transfer_leg_exists(
    tx: Any,
    origin: Any,
    matched: Any,
    accounts: list[Any],
) -> bool:
    """
    True if we already have the other SMS leg (same amount, opposite type, nearby date)
    linking origin ↔ matched — then both balances were/will be updated without mirroring.
    """
    opp_type = "debit" if (getattr(tx, "type", "") or "").lower() == "credit" else "credit"
    start = tx.date - timedelta(days=4)
    end = tx.date + timedelta(days=4)
    ex = (getattr(matched, "source", None) or "").strip().lower()
    origin_slug = (getattr(origin, "source", None) or "").strip().lower()

    candidates = Transaction.query.filter(
        Transaction.id != getattr(tx, "id", None),
        Transaction.type == opp_type,
        Transaction.date >= start,
        Transaction.date <= end + timedelta(days=1),
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).all()

    for otx in candidates:
        if not _amount_close(getattr(otx, "amount", 0), getattr(tx, "amount", 0)):
            continue
        cp = otx.receiver if opp_type == "debit" else otx.sender
        hit = counterparty_matches_other_wallet(cp, accounts, exclude_source=ex)
        if hit and (getattr(hit, "source", None) or "").strip().lower() == origin_slug:
            return True
    return False


def undo_own_account_transfer_mirror_if_second_leg(tx: Any, origin_balance: Any) -> None:
    """
    If this transaction is the second leg of an internal transfer and we previously
    mirrored the origin account's balance, reverse that adjustment once.
    """
    opp_type = "credit" if (getattr(tx, "type", "") or "").lower() == "debit" else "debit"
    start = tx.date - timedelta(days=4)
    end = tx.date + timedelta(days=4)
    origin_slug = (getattr(origin_balance, "source", None) or "").strip()

    candidates = Transaction.query.filter(
        Transaction.id != getattr(tx, "id", None),
        Transaction.type == opp_type,
        Transaction.date >= start,
        Transaction.date <= end + timedelta(days=1),
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    ).all()

    for otx in candidates:
        if not _amount_close(getattr(otx, "amount", 0), getattr(tx, "amount", 0)):
            continue
        notes = getattr(otx, "notes", None) or ""
        for slug, delta in parse_mirror_adjs(notes):
            if slug.lower() != origin_slug.lower():
                continue
            origin_balance.current_balance = float(origin_balance.current_balance or 0) - delta
            origin_balance.last_updated = datetime.now()
            otx.notes = _strip_one_mirror_adj(notes, slug, delta)[:250]
            return


def apply_own_account_transfer_mirror_balance(
    tx: Any,
    origin: Any,
    matched: Any,
    accounts: list[Any],
) -> None:
    """
    Adjust matched wallet balance when only one notification exists (mirror of funds movement).
    Skips manual locks and when the opposite SMS leg already exists in the DB.
    """
    if getattr(matched, "is_manual", False):
        return

    amt = float(getattr(tx, "amount", 0) or 0)
    if amt <= 0:
        return

    if opposite_internal_transfer_leg_exists(tx, origin, matched, accounts):
        return

    t = (getattr(tx, "type", "") or "").lower()
    if t == "credit":
        delta = -amt
    elif t == "debit":
        delta = amt
    else:
        return

    matched.current_balance = float(matched.current_balance or 0) + delta
    matched.last_updated = datetime.now()

    slug = (getattr(matched, "source", None) or "").strip()
    tag = f"[mirror_adj:{slug}:{delta:+.2f}]"
    existing = (getattr(tx, "notes", None) or "").strip()
    combined = (existing + " " + tag).strip()
    tx.notes = combined[:250]


def is_own_account_transfer_row(txn: Any) -> bool:
    p = (getattr(txn, "purpose", None) or "").strip()
    return p in (SELF_TRANSFER_PURPOSE, LEGACY_SELF_TRANSFER_PURPOSE)


def exclude_own_account_transfer_sql():
    """
    SQLAlchemy criterion: rows that should affect discretionary spending / income totals.
    Own-account movements are ledger hygiene, not new spending or earnings.
    """
    from sqlalchemy import and_, or_

    return or_(
        Transaction.purpose.is_(None),
        and_(
            Transaction.purpose != SELF_TRANSFER_PURPOSE,
            Transaction.purpose != LEGACY_SELF_TRANSFER_PURPOSE,
        ),
    )


_MIN_TOKEN_LEN = 3
_SIGNIFICANT_TOKEN = 4

_GENERIC_SKIP = frozenset(
    {
        "bank",
        "me",
        "fee",
        "sms",
        "customer",
        "merchant",
        "pk",
        "iban",
        "rs",
        "pkr",
    }
)


def _keywords_json_to_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def scrub_counterparty(name: str | None) -> str:
    """Strip bank SMS trailing refs (TMIC…, EBPL, IBAN tails) from parsed sender/receiver."""
    if not name:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    upper = s.upper()
    for marker in (" TMIC", ", TMIC", " EBPL", ", EBPL", "\n"):
        idx = upper.find(marker)
        if idx != -1:
            s = s[:idx].strip()
            upper = s.upper()
    parts = re.split(r"\s+of\s+IBAN\b", s, maxsplit=1, flags=re.IGNORECASE)
    s = parts[0].strip()
    parts = re.split(r"\s+from\s+your\b", s, maxsplit=1, flags=re.IGNORECASE)
    return parts[0].strip()


def _norm_compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str) -> set[str]:
    return {
        t
        for t in re.sub(r"[^a-z0-9\s]", " ", (s or "").lower()).split()
        if len(t) >= _MIN_TOKEN_LEN and t not in _GENERIC_SKIP
    }


def _identity_strings(acc: Any) -> list[str]:
    out: list[str] = []
    for attr in ("holder_name", "display_name"):
        v = (getattr(acc, attr, None) or "").strip()
        if len(v) >= _MIN_TOKEN_LEN:
            out.append(v)
    slug = (getattr(acc, "source", None) or "").strip()
    if slug and slug != "cash":
        out.append(slug.replace("_", " "))
    for kw in _keywords_json_to_list(getattr(acc, "match_keywords", None)):
        if len(kw) >= _MIN_TOKEN_LEN:
            out.append(kw)
    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def _holder_name_priority_wallet_match(
    counterparty: str | None,
    accounts: list[Any],
    *,
    exclude_source: str | None,
) -> Any | None:
    """
    Match counterparty to another wallet primarily via saved `holder_name` on AccountBalance.
    This runs before keyword/display matching so “money to myself” wins over generic institution names.
    """
    cp_raw = scrub_counterparty(counterparty)
    if not cp_raw or len(cp_raw) < _MIN_TOKEN_LEN:
        return None
    cp_compact = _norm_compact(cp_raw)
    cp_lower = cp_raw.lower()
    ex = (exclude_source or "").strip().lower()

    best = None
    best_len = 0

    for acc in accounts:
        src = (getattr(acc, "source", None) or "").strip().lower()
        if src == ex or src == "cash":
            continue
        hn = (getattr(acc, "holder_name", None) or "").strip()
        if len(hn) < 3:
            continue
        hn_compact = _norm_compact(hn)
        if len(hn_compact) < _SIGNIFICANT_TOKEN:
            continue
        hn_lower = hn.lower()
        hit = False
        if hn_compact in cp_compact or cp_compact in hn_compact:
            hit = True
        elif hn_lower in cp_lower or cp_lower in hn_lower:
            hit = True
        if hit and len(hn_compact) > best_len:
            best_len = len(hn_compact)
            best = acc

    return best


def counterparty_matches_other_wallet(
    counterparty: str | None,
    accounts: list[Any],
    *,
    exclude_source: str | None,
) -> Any | None:
    """
    If counterparty (sender on credit, receiver on debit) matches another AccountBalance
    (holder, display name, keywords, slug), return that row — indicates own-account movement.
    """
    cp_raw = scrub_counterparty(counterparty)
    if not cp_raw or len(cp_raw) < _MIN_TOKEN_LEN:
        return None
    cp_compact = _norm_compact(cp_raw)
    cp_tokens = _tokens(cp_raw)
    if not cp_tokens and len(cp_compact) < _SIGNIFICANT_TOKEN:
        return None

    holder_hit = _holder_name_priority_wallet_match(counterparty, accounts, exclude_source=exclude_source)
    if holder_hit:
        return holder_hit

    best = None
    best_score = 0

    ex = (exclude_source or "").strip().lower()

    for acc in accounts:
        src = (getattr(acc, "source", None) or "").strip().lower()
        if src == ex:
            continue
        if src == "cash":
            continue

        for identity in _identity_strings(acc):
            id_compact = _norm_compact(identity)
            id_tokens = _tokens(identity)
            score = 0
            if len(id_compact) >= _SIGNIFICANT_TOKEN and (
                id_compact in cp_compact or cp_compact in id_compact
            ):
                score = max(score, min(len(id_compact), len(cp_compact)))
            overlap = cp_tokens & id_tokens
            sig_overlap = {t for t in overlap if len(t) >= _SIGNIFICANT_TOKEN}
            if sig_overlap:
                score = max(score, sum(len(t) for t in sig_overlap))
            elif overlap:
                score = max(score, sum(len(t) for t in overlap))

            if score > best_score:
                best_score = score
                best = acc

    return best if best_score > 0 else None


def maybe_mark_own_account_transfer(
    tx: Any, accounts: list[Any], *, balance_slug: str | None
) -> tuple[bool, Any | None]:
    """
    Set purpose + categorization_status when the counterparty is another of the user's wallets.
    Returns (applied, matched_account_or_None).
    """
    if tx is None:
        return False, None
    if getattr(tx, "categorization_status", None) not in (None, "pending"):
        return False, None

    cp = None
    t = (getattr(tx, "type", "") or "").lower()
    if t == "credit":
        cp = getattr(tx, "sender", None)
    elif t == "debit":
        cp = getattr(tx, "receiver", None)

    matched = counterparty_matches_other_wallet(cp, accounts, exclude_source=balance_slug)
    if not matched:
        return False, None

    tx.purpose = SELF_TRANSFER_PURPOSE
    tx.categorization_status = "pending"
    other = (getattr(matched, "display_name", None) or getattr(matched, "source", "") or "").strip()
    note_extra = f" [matched wallet: {other}]"
    existing = (getattr(tx, "notes", None) or "").strip()
    if note_extra.strip() not in existing:
        tx.notes = (existing + note_extra).strip()[:250]
    return True, matched
