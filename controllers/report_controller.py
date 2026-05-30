# controllers/report_controller.py
#
# The report/statement logic is complex and already lives in app.py.
# This controller is a thin wrapper that keeps the HTTP layer separate.
# The actual computation stays in services/report_service.py which you
# can extract incrementally from the original app.py functions.

from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import json, re
from flask import request, jsonify
from database import db
from model import (
    Transaction, MonthlyBalance, Budget, StatementAnalysis, AccountBalance
)
from drive_utils import get_drive_service, ensure_folder_path, upload_json


# ── GET Statement ─────────────────────────────────────────────────────────────

def get_statement(current_user):
    """Return a previously calculated statement for a given month."""
    from model import StatementAnalysis
    data           = request.get_json() or {}
    month_str      = data.get("month") or datetime.now().strftime("%Y-%m")
    analysis       = StatementAnalysis.query.filter_by(month=month_str).first()

    if not analysis:
        now_str = datetime.now().strftime("%Y-%m")
        if month_str == now_str:
            return jsonify({"message": f"Statement for {month_str} is not finalised yet."}), 404
        return jsonify({"message": f"Analysis for {month_str} isn't computed yet."}), 404

    try:
        ids      = json.loads(analysis.transaction_ids) if analysis.transaction_ids else []
        linked   = Transaction.query.filter(Transaction.id.in_(ids)).order_by(Transaction.date.desc()).all()
    except Exception:
        linked = []

    tx_data = [{
        "date":        t.date.strftime("%d/%m/%Y"),
        "amount":      t.amount,
        "description": t.notes or t.sender or "Transaction",
        "type":        t.type,
        "status":      "existing",
    } for t in linked]

    resp = analysis.to_dict()
    resp["data"] = tx_data

    # Drive backup
    if current_user.google_refresh_token:
        try:
            svc = get_drive_service(current_user)
            if svc:
                fid = ensure_folder_path(svc, ["Aurestra Finance", month_str])
                if fid:
                    upload_json(svc, fid, "statement.json", resp)
        except Exception as e:
            print(f"⚠️ Drive Backup Failed: {e}")

    return jsonify(resp)


# ── Calculate Statement ───────────────────────────────────────────────────────

def calculate_statement(current_user):
    """
    Fetch the bank statement PDF from Gmail for a given month,
    import transactions, run analysis, and update account balance.
    Delegates to the existing logic — extracted here for MVC separation.
    """
    from fetchers import fetch_previous_month_statement
    from config import TARGET_ACCOUNT_NUMBER
    from statement_wallet_match import resolve_account_balance_for_statement

    data        = request.get_json() or {}
    month_param = data.get("month")

    try:
        if month_param:
            target_dt  = datetime.strptime(month_param, "%Y-%m")
            month_str  = month_param
            next_month = target_dt + relativedelta(months=1)
            ref_date   = next_month.replace(day=15)
        else:
            today     = datetime.now()
            prev      = (today.replace(day=1) - timedelta(days=1))
            month_str = prev.strftime("%Y-%m")
            ref_date  = today

        # Cache hit
        force    = request.args.get('force', 'false').lower() == 'true'
        existing = StatementAnalysis.query.filter_by(month=month_str).first()
        if existing and existing.reviewed_at and not force:
            return _cached_response(existing, month_str)

        # Fetch from Gmail
        result = fetch_previous_month_statement(current_user, reference_date=ref_date)
        if "error" in result:
            return jsonify({"error": result["error"], "month": month_str}), 400

        # Manual selection check
        is_confirmed = data.get("confirmed", False)
        if not is_confirmed and "all_email_data" in result:
            return jsonify({
                "message":          "Multiple balances found. Manual verification required.",
                "requires_selection": True,
                "month":            month_str,
                "all_email_data":   result.get("all_email_data", []),
                "suggested_balances": result.get("balances", {}),
            }), 200

        extracted_txs = result.get("transactions", [])
        balances      = result.get("balances", {})

        user_bal = data.get("user_selected_balance")
        if is_confirmed and user_bal is not None:
            balances["closing_balance"] = float(user_bal)

        added, skipped, stmt_ids, tx_data = _import_transactions(extracted_txs)

        open_bal  = balances.get("opening_balance", 0)
        close_bal = balances.get("closing_balance",  0)
        _update_monthly_balance(month_str, open_bal, close_bal)

        stmt_analysis = _update_statement_analysis(
            month_str, open_bal, close_bal, extracted_txs, stmt_ids
        )

        balance_msg, wallet_info = _apply_balance(
            stmt_analysis, result, close_bal,
            TARGET_ACCOUNT_NUMBER, resolve_account_balance_for_statement
        )

        db.session.commit()
        _drive_backup(current_user, month_str, extracted_txs, balances, stmt_analysis)

        return jsonify({
            "message":                 "Statement processed",
            "month":                   month_str,
            "added_transactions":      added,
            "skipped_transactions":    skipped,
            "data":                    tx_data,
            "balances":                {"opening": open_bal, "closing": close_bal},
            "balance_update":          balance_msg,
            "processing_status":       stmt_analysis.processing_status,
            "read_status":             "read" if stmt_analysis.reviewed_at else "unread",
            "balance_matches":         stmt_analysis.to_dict().get("balance_matches", True),
            "account_balance_source":  stmt_analysis.account_balance_source,
            "statement_wallet_match":  wallet_info,
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ── Mark Read ─────────────────────────────────────────────────────────────────

def mark_read(current_user):
    from model import StatementAnalysis
    data      = request.get_json() or {}
    month_str = data.get("month")
    if not month_str:
        return jsonify({"error": "Month is required"}), 400

    stmt = StatementAnalysis.query.filter_by(month=month_str).first()
    if not stmt:
        return jsonify({"error": "Statement not found"}), 404

    if not stmt.balance_applied:
        close_bal = stmt.closing_balance
        slug      = getattr(stmt, "account_balance_source", None)
        acc       = (AccountBalance.query.filter_by(source=slug).first()
                     if slug else AccountBalance.query.filter_by(source="bank").first())

        if not acc and not slug:
            acc = AccountBalance(
                source="bank", display_name="Bank Account", holder_name="",
                account_kind="bank", match_keywords=json.dumps(["bank"]),
                accent_color="#A855F7", sort_order=0,
                current_balance=close_bal, is_manual=False,
            )
            db.session.add(acc)
        elif not acc:
            return jsonify({"error": f"No AccountBalance row for source `{slug}`."}), 400

        acc.current_balance = close_bal
        acc.last_updated    = datetime.utcnow()
        acc.is_manual       = False
        stmt.balance_applied = True

    if not stmt.reviewed_at:
        stmt.reviewed_at = datetime.utcnow()

    db.session.commit()
    return jsonify({
        "message":       "Statement marked as read and balance updated",
        "balance_applied": stmt.balance_applied,
        "reviewed_at":   stmt.reviewed_at.isoformat(),
    })


# ── Insights ──────────────────────────────────────────────────────────────────

def generate_insights(current_user):
    try:
        from financial_agent import FinancialAgent
        from fetchers import fetch_latest_bank_email
        data = request.get_json() or {}
        try:
            fetch_latest_bank_email()
        except Exception:
            pass

        month_str = data.get("month")
        if month_str:
            dt = datetime.strptime(month_str, "%Y-%m")
            yr, mo = dt.year, dt.month
        else:
            prev = (date.today().replace(day=1) - timedelta(days=1))
            yr, mo = prev.year, prev.month

        FinancialAgent().analyze_month(yr, mo)
        return jsonify({"message": f"Insights generated for {yr}-{mo:02d}", "month": f"{yr}-{mo:02d}"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def calculate_summary():
    try:
        from financial_agent import FinancialAgent
        from sqlalchemy import func, extract, case
        from transfer_matching import exclude_own_account_transfer_sql

        data      = request.get_json() or {}
        month_str = data.get("month", datetime.now().strftime("%Y-%m"))
        dt        = datetime.strptime(month_str, "%Y-%m")

        total_income = db.session.query(func.sum(Transaction.amount)).filter(
            extract('year',  Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            Transaction.type == 'credit',
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        total_expense = db.session.query(
            func.sum(case(
                (Transaction.type == 'debit',  Transaction.amount),
                (Transaction.type == 'credit', -Transaction.amount),
                else_=0,
            ))
        ).filter(
            extract('year',  Transaction.date) == dt.year,
            extract('month', Transaction.date) == dt.month,
            exclude_own_account_transfer_sql(),
        ).scalar() or 0.0

        summary = MonthlyBalance.query.filter_by(month=month_str).first()
        if not summary:
            summary = MonthlyBalance(month=month_str, opening_balance=0,
                                     closing_balance=0, source="combined")
            db.session.add(summary)

        summary.expense         = total_expense
        summary.savings         = total_income - total_expense
        summary.closing_balance = (summary.opening_balance or 0) + total_income - total_expense
        if not summary.source:
            summary.source = "combined"
        db.session.commit()

        try:
            FinancialAgent().analyze_month(dt.year, dt.month)
        except Exception as e:
            print(f"❌ Financial Agent Error: {e}")

        return jsonify({
            "message": f"Statement processed for {month_str}",
            "data": {
                "month": month_str,
                "summary": {"expense": summary.expense, "savings": summary.savings},
            },
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ── Private helpers ───────────────────────────────────────────────────────────

def _cached_response(analysis, month_str):
    try:
        ids    = json.loads(analysis.transaction_ids) if analysis.transaction_ids else []
        cached = Transaction.query.filter(Transaction.id.in_(ids)).order_by(Transaction.date.desc()).all()
    except Exception:
        cached = []

    return jsonify({
        "message": "Statement loaded from cache (Read).",
        "cached":  True, "month": month_str,
        "balances": {"opening": analysis.opening_balance, "closing": analysis.closing_balance},
        "data": [{
            "date":        t.date.strftime("%d/%m/%Y"),
            "amount":      t.amount,
            "description": t.notes or t.sender or "Transaction",
            "type":        t.type,
            "status":      "existing",
        } for t in cached],
        "stats": {"added": 0, "skipped": len(cached), "total": len(cached)},
        "processing_status": analysis.processing_status,
        "read_status":       "read",
        "balance_matches":   True,
    }), 200


def _import_transactions(extracted_txs):
    added = skipped = 0
    stmt_ids = []
    tx_data  = []

    for tx in extracted_txs:
        try:
            tx_date = datetime.strptime(tx["date"], "%d/%m/%Y")
        except Exception:
            continue

        clean_desc = re.sub(r'^\d{2}\/\d{2}\/\d{4}\s*', '', tx["description"]).strip()
        tx_hash    = Transaction.generate_deterministic_hash({
            "date": tx_date, "amount": tx["amount"],
            "type": tx["type"], "description": tx["description"],
        })
        exists = Transaction.query.filter_by(transaction_hash=tx_hash).first()

        if not exists:
            exists = Transaction.query.filter(
                Transaction.amount == tx["amount"],
                Transaction.type   == tx["type"],
                Transaction.date  >= tx_date - timedelta(days=2),
                Transaction.date  <= tx_date + timedelta(days=2),
            ).first()
            if exists and not exists.transaction_hash:
                exists.transaction_hash = tx_hash

        if exists:
            stmt_ids.append(exists.id)
            tx_data.append({"date": tx["date"], "amount": tx["amount"],
                             "description": clean_desc, "status": "skipped"})
            skipped += 1
        else:
            try:
                new_tx = Transaction(
                    source="bank_statement", date=tx_date,
                    amount=tx["amount"], type=tx["type"],
                    purpose="Uncategorized", sender="Bank Statement", receiver="Me",
                    notes=clean_desc[:250], transaction_hash=tx_hash,
                )
                db.session.add(new_tx)
                db.session.flush()
                stmt_ids.append(new_tx.id)
                tx_data.append({"date": tx["date"], "amount": tx["amount"],
                                 "description": clean_desc, "status": "added"})
                added += 1
            except Exception:
                db.session.rollback()
                existing_race = Transaction.query.filter_by(transaction_hash=tx_hash).first()
                if existing_race:
                    stmt_ids.append(existing_race.id)
                skipped += 1

    return added, skipped, stmt_ids, tx_data


def _update_monthly_balance(month_str, open_bal, close_bal):
    mb = MonthlyBalance.query.filter_by(month=month_str).first()
    if not mb:
        mb = MonthlyBalance(month=month_str, opening_balance=open_bal,
                            closing_balance=close_bal, source="bank_statement")
        db.session.add(mb)
    else:
        mb.opening_balance = open_bal
        mb.closing_balance = close_bal


def _update_statement_analysis(month_str, open_bal, close_bal, extracted_txs, stmt_ids):
    calc_income = calc_expense = 0.0
    for tx in extracted_txs:
        try:
            amt    = float(str(tx.get("amount", "0")).replace("Rs", "").replace(",", "").strip())
            t_type = str(tx.get("type", "")).lower().strip()
            if t_type in ('credit', 'cr', 'deposit'):
                calc_income  += abs(amt)
            elif t_type in ('debit', 'dr', 'withdrawal'):
                calc_expense += abs(amt)
        except Exception:
            pass

    surplus = calc_income - calc_expense
    stmt    = StatementAnalysis.query.filter_by(month=month_str).first()
    if not stmt:
        stmt = StatementAnalysis(month=month_str)
        db.session.add(stmt)

    stmt.opening_balance = open_bal
    stmt.closing_balance = close_bal
    stmt.total_income    = calc_income
    stmt.total_expense   = calc_expense
    stmt.net_result      = surplus
    stmt.status          = "Surplus" if surplus >= 0 else "Deficit"
    stmt.breakdown_json  = json.dumps({"income": {}, "expenses": {}})
    stmt.analysis_date   = datetime.utcnow()
    stmt.transaction_ids = json.dumps(stmt_ids)
    if not stmt.statement_id:
        stmt.statement_id = month_str
    return stmt


def _apply_balance(stmt, result, close_bal, target_account_number, resolver):
    wallet_info = {"reason": None, "detail": None,
                   "account_balance_source": stmt.account_balance_source}
    if stmt.reviewed_at:
        return "Statement already read — no balance update.", {**wallet_info, "reason": "already_reviewed"}

    ab_row, reason, detail = resolver(
        result.get("statement_detected_account_numbers"),
        result.get("statement_matching_text"),
        target_account_number,
    )
    wallet_info.update(reason=reason, detail=detail)

    if ab_row is None:
        stmt.processing_status = "partial"
        msg = f"Transactions saved; balance not written: {detail}"
        note = f"Balance mapping: {reason} — {detail}"
        stmt.processing_notes = ((stmt.processing_notes or "") + "\n" + note).strip()
    else:
        stmt.account_balance_source = ab_row.source
        wallet_info["account_balance_source"] = ab_row.source
        stmt.processing_status = "success"
        old = ab_row.current_balance
        ab_row.current_balance = close_bal
        ab_row.last_updated    = datetime.now()
        ab_row.is_manual       = False
        stmt.balance_applied   = True
        stmt.reviewed_at       = datetime.utcnow()
        msg = f"Balance SET on `{ab_row.source}` from {old:,.2f} to {close_bal:,.2f}"

    return msg, wallet_info


def _drive_backup(current_user, month_str, extracted_txs, balances, mb):
    if not current_user.google_refresh_token:
        return
    try:
        svc = get_drive_service(current_user)
        if svc:
            payload = {
                "month": month_str, "transactions": extracted_txs, "balances": balances,
                "summary": {
                    "opening": mb.opening_balance if hasattr(mb, 'opening_balance') else 0,
                    "closing": mb.closing_balance if hasattr(mb, 'closing_balance') else 0,
                },
            }
            fid = ensure_folder_path(svc, ["Aurestra Finance", month_str])
            if fid:
                upload_json(svc, fid, "statement.json", payload)
    except Exception as e:
        print(f"⚠️ Drive Backup Failed: {e}")
