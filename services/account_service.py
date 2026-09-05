# services/account_service.py  —  Account / wallet business logic (Phase 3: user-scoped)

import json
import re
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from database import db
from model import AccountBalance


from utils.money import to_money


def ensure_default_cash_wallet(user_id: int):
    """Ensure the reserved physical-cash wallet exists for this user. Idempotent."""
    if AccountBalance.query.filter_by(user_id=user_id, source="cash").first():
        return
    acc = AccountBalance(
        user_id=user_id,
        source="cash",
        display_name="Cash",
        holder_name="",
        account_kind="cash",
        match_keywords=json.dumps(["cash"]),
        accent_color="#22C55E",
        sort_order=-1000,
        current_balance=0.0,
        last_updated=datetime.now(),
        is_manual=False,
    )
    db.session.add(acc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
def get_all_accounts(user_id: int):
    ensure_default_cash_wallet(user_id)
    accounts = (
        AccountBalance.query
        .filter_by(user_id=user_id)
        .filter(AccountBalance.is_deleted.isnot(True))
        .order_by(AccountBalance.sort_order, AccountBalance.id)
        .all()
    )
    result = []
    for acc in accounts:
        d = acc.to_dict()
        d["statement_base"]   = acc.current_balance
        d["live_adjustment"]  = 0.0
        d["savings_reduction"] = 0.0
        result.append(d)
    return result


def create_account(user_id: int, data: dict) -> AccountBalance:
    display_name = (data.get("display_name") or "").strip()
    if not display_name:
        raise ValueError("display_name is required")

    holder_name  = (data.get("holder_name") or data.get("account_holder_name") or "").strip()
    account_kind = (data.get("account_kind") or "bank").strip()
    if account_kind not in ("bank", "mobile_wallet", "cash", "digital_bank"):
        account_kind = "bank"
    if account_kind == "cash":
        raise ValueError("Cash is included automatically. Edit it from home.")

    raw_slug = (data.get("slug") or "").strip().lower()
    base = raw_slug or re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")[:48] or "wallet"
    slug, n = base, 2
    # Slug uniqueness scoped to this user (excluding deleted accounts to allow re-using names)
    while AccountBalance.query.filter_by(user_id=user_id, source=slug).filter(AccountBalance.is_deleted.isnot(True)).first():
        slug = f"{base}_{n}"; n += 1
    if slug == "cash":
        raise ValueError("That name is reserved for built-in Cash.")

    kws = data.get("match_keywords")
    if isinstance(kws, str):
        kws = [x.strip() for x in kws.split(",") if x.strip()]
    elif isinstance(kws, list):
        kws = [str(x).strip() for x in kws if str(x).strip()]
    else:
        kws = [display_name]

    nums = data.get("statement_account_numbers")
    stmt_nums = None
    if isinstance(nums, list) and nums:
        stmt_nums = json.dumps([str(x).strip() for x in nums if str(x).strip()])
    elif isinstance(nums, str) and nums.strip():
        stmt_nums = nums.strip()

    max_ord = db.session.query(func.max(AccountBalance.sort_order)).filter_by(user_id=user_id).scalar()
    max_ord = int(max_ord) if max_ord is not None else 0
    initial = float(data.get("initial_balance", 0) or 0)

    acc = AccountBalance(
        user_id=user_id,
        source=slug,
        display_name=display_name,
        holder_name=holder_name,
        account_kind=account_kind,
        match_keywords=json.dumps(kws),
        statement_account_numbers=stmt_nums,
        accent_color=(data.get("accent_color") or "#6366F1").strip(),
        sort_order=max_ord + 1,
        current_balance=initial,
        last_updated=datetime.now(),
        is_manual=bool(initial),
        is_deleted=False,
    )
    db.session.add(acc)
    try:
        db.session.commit()
    except IntegrityError:
        # Slug collision despite the loop (race condition) — append a short unique suffix and retry
        import uuid
        db.session.rollback()
        acc.source = f"{slug}_{uuid.uuid4().hex[:6]}"
        db.session.add(acc)
        db.session.commit()
    return acc


def set_manual_balance(user_id: int, account_id: int = None, source: str = None, amount: float = 0.0):
    if account_id is not None:
        balance = AccountBalance.query.filter_by(id=int(account_id), user_id=user_id).first()
        if not balance:
            raise LookupError("Account not found")
        source = balance.source
    else:
        balance = AccountBalance.query.filter_by(user_id=user_id, source=source).first()

    amt_val = to_money(amount)
    if amt_val < 0:
        raise ValueError("Balance cannot be negative")

    if not balance:
        if not source:
            raise LookupError("Account not found")
        dn      = (source or "bank").replace("_", " ").title()
        max_ord = db.session.query(func.max(AccountBalance.sort_order)).filter_by(user_id=user_id).scalar() or 0
        balance = AccountBalance(
            user_id=user_id, source=source or "bank", display_name=dn, holder_name="",
            account_kind="bank", match_keywords=json.dumps([source or "bank"]),
            accent_color="#6366F1", sort_order=int(max_ord) + 1,
            current_balance=amt_val,
            is_deleted=False,
        )
        db.session.add(balance)

    balance.current_balance = amt_val
    balance.is_manual       = True
    balance.last_updated    = datetime.now()
    db.session.commit()
    return balance


def update_account(user_id: int, acc: AccountBalance, data: dict) -> AccountBalance:
    if acc.user_id != user_id:
        raise PermissionError("Access denied")
    if "display_name" in data:
        acc.display_name = (data["display_name"] or acc.display_name).strip()
    if "holder_name" in data or "account_holder_name" in data:
        hn = data.get("holder_name") if "holder_name" in data else data.get("account_holder_name")
        acc.holder_name = (hn or "").strip()
    if "account_kind" in data:
        ak = data["account_kind"].strip()
        if ak == "cash" and acc.source != "cash":
            raise ValueError("Use the built-in Cash wallet for physical cash.")
        if ak in ("bank", "mobile_wallet", "cash", "digital_bank"):
            acc.account_kind = ak
    if "match_keywords" in data:
        kws = data["match_keywords"]
        if isinstance(kws, str):
            kws = [x.strip() for x in kws.split(",") if x.strip()]
        if isinstance(kws, list):
            acc.match_keywords = json.dumps([str(x).strip() for x in kws if str(x).strip()])
    if "statement_account_numbers" in data:
        nums = data["statement_account_numbers"]
        if nums is None or nums == []:
            acc.statement_account_numbers = None
        elif isinstance(nums, list):
            acc.statement_account_numbers = json.dumps([str(x).strip() for x in nums if str(x).strip()])
        elif isinstance(nums, str) and nums.strip():
            acc.statement_account_numbers = nums.strip()
    if "accent_color" in data:
        acc.accent_color = (data["accent_color"] or acc.accent_color).strip()
    if "sort_order" in data:
        acc.sort_order = int(data["sort_order"] or 0)

    if "balance" in data or "current_balance" in data:
        raw = data.get("balance", data.get("current_balance"))
        acc.current_balance = to_money(raw)
        val = to_money(raw)
        if val < 0:
            raise ValueError("Balance cannot be negative")
        acc.current_balance = val
        acc.is_manual = True

    acc.last_updated = datetime.now()
    db.session.commit()
    return acc


def delete_account(user_id: int, acc: AccountBalance):
    if acc.user_id != user_id:
        raise PermissionError("Access denied")
    if acc.source == "cash":
        raise ValueError("The Cash wallet cannot be deleted.")
    acc.is_deleted = True
    db.session.commit()
