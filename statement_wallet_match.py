"""
Match bank e-statement PDF text / detected account numbers to an AccountBalance row
so statement closing balance updates only the correct wallet/bank.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model import AccountBalance


def extract_statement_account_numbers(text: str) -> list[str]:
    """Return unique account-style digit runs found in statement text."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    patterns = [
        re.compile(r"Account\s*(?:No|Number|#)\s*[.:\s]*(\d{8,24})", re.I),
        re.compile(r"Acct\.?\s*No\.?\s*[.:\s]*(\d{8,24})", re.I),
        re.compile(r"A\/C\s*(?:No|Number)?\s*[.:\s]*(\d{8,24})", re.I),
    ]
    for pat in patterns:
        for m in pat.finditer(text):
            d = m.group(1).strip()
            if d and d not in seen:
                seen.add(d)
                found.append(d)
    return found


def _norm_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _row_statement_numbers(ab: Any) -> list[str]:
    raw = getattr(ab, "statement_account_numbers", None)
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _row_matches_detected(ab: Any, detected: list[str]) -> bool:
    cfg_list = _row_statement_numbers(ab)
    if not cfg_list:
        return False
    for cfg in cfg_list:
        c = _norm_digits(cfg)
        if len(c) < 4:
            continue
        for det in detected:
            d = _norm_digits(det)
            if len(d) < 4:
                continue
            if c in d or d in c or d.endswith(c) or c.endswith(d):
                return True
    return False


def resolve_account_balance_for_statement(
    user_id: int,
    statement_detected: list[str] | None,
    statement_text: str | None,
    env_target: str | None,
) -> tuple[Any | None, str, str]:
    """
    Pick exactly one AccountBalance for this statement, or return (None, reason, detail).

    Order:
    1) Match `account_balances.statement_account_numbers` JSON against detected numbers.
    2) If TARGET_ACCOUNT_NUMBER appears in PDF text and there is exactly one bank row → that row.
    3) If TARGET in text → legacy row with source == 'bank' if present.
    4) If only one AccountBalance row exists → that row.
    """
    from model import AccountBalance

    text = statement_text or ""
    detected = list(
        dict.fromkeys(
            (statement_detected or []) + extract_statement_account_numbers(text)
        )
    )
    accounts = (
        AccountBalance.query.filter_by(user_id=user_id).order_by(
            AccountBalance.sort_order.asc(), AccountBalance.id.asc()
        ).all()
    )

    matched = [ab for ab in accounts if _row_matches_detected(ab, detected)]
    if len(matched) == 1:
        ab = matched[0]
        return ab, "matched_statement_account_numbers", f"PDF digits matched wallet `{ab.source}`"
    if len(matched) > 1:
        slugs = [m.source for m in matched]
        return None, "ambiguous", f"Multiple wallets match this statement: {slugs}. Set distinct statement_account_numbers on each row."

    t = (env_target or "").strip()
    if t and t in text:
        banks = [a for a in accounts if (a.account_kind or "").lower() == "bank"]
        if len(banks) == 1:
            ab = banks[0]
            return ab, "single_bank_env_target", f"TARGET_ACCOUNT_NUMBER found in PDF; only one bank row (`{ab.source}`)."
        legacy = AccountBalance.query.filter_by(user_id=user_id, source="bank").first()
        if legacy:
            return legacy, "legacy_source_bank", "TARGET_ACCOUNT_NUMBER in PDF; using AccountBalance with source=`bank` (configure statement_account_numbers for multiple banks)."

    if len(accounts) == 1:
        ab = accounts[0]
        return ab, "single_account_row", f"Only one AccountBalance row (`{ab.source}`)."

    return (
        None,
        "unresolved",
        "Could not map statement to a wallet. Add JSON `statement_account_numbers` on the correct "
        "account_balances row (list of full or partial account numbers), or set TARGET_ACCOUNT_NUMBER in .env.",
    )
