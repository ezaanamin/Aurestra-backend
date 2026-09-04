# controllers/export_controller.py — Secure export generation with profile password verification

import io
import csv
from datetime import datetime
from flask import request, jsonify, make_response
from sqlalchemy import func, extract

from database import db
from model import Transaction, Category, User, AccountBalance
from services.auth_service import verify_password
from utils.auth import token_required
from database import limiter


# Helper for field normalization
def _normalize_type(t_str):
    if not t_str:
        return 'Debit'
    s = str(t_str).lower().strip()
    if s == 'credit':
        return 'Income'
    elif s == 'debit':
        return 'Expense'
    elif 'transfer' in s:
        return 'Self Transfer'
    return s.capitalize()


def _normalize_source(s_str):
    if not s_str:
        return 'Manual'
    s = str(s_str).lower().strip()
    if s == 'manual':
        return 'Manual'
    elif s in ('notification', 'bank_sms', 'sms'):
        return 'Bank SMS / Notification'
    elif s == 'ocr':
        return 'Receipt Scan'
    return s.capitalize()


def _normalize_account_source(acc_str, account_map=None):
    if not acc_str or acc_str == 'all':
        return 'All Accounts'
    if account_map and acc_str in account_map:
        return account_map[acc_str]
    return str(acc_str).replace('_', ' ').title()


def _format_date(dt):
    if isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%d %H:%M')
    elif dt:
        return str(dt)
    return ''


def _format_amount(val):
    try:
        f = float(val or 0)
        return f"{f:,.2f}"
    except (ValueError, TypeError):
        return "0.00"


@limiter.limit("5 per minute")
def export_transactions(current_user):
    """
    POST /api/export/transactions
    Requires:
      - password (JSON body) — verified against current_user.password_hash
      - format: 'csv' or 'pdf'
      - optional filters: account_source, tx_type, period, q
    """
    data = request.get_json() or {}
    password = data.get('password') or ''
    export_format = (data.get('format') or 'csv').lower().strip()

    if not password:
        return jsonify({'message': 'Password is required to export transactions.'}), 400

    # Verification against account password OR decryption key (for OAuth users)
    is_valid = False
    if current_user.password_hash and verify_password(password, current_user.password_hash):
        is_valid = True
    elif current_user.decryption_key_hash:
        from utils.crypto_helpers import verify_decryption_key
        if verify_decryption_key(password, current_user.decryption_key_hash):
            is_valid = True

    if not is_valid:
        return jsonify({'message': 'Invalid password or decryption key. Export authorization failed.'}), 400

    # Verification-only check (pre-flight password check)
    if data.get('verify_only'):
        return jsonify({'message': 'Password verified successfully.', 'status': 'ok'}), 200

    # Build account lookup dictionary for current_user
    user_accounts = AccountBalance.query.filter_by(user_id=current_user.id).all()
    account_map = {acc.source: (acc.display_name or acc.source) for acc in user_accounts}

    # Fetch transactions belonging ONLY to current_user
    query = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.is_deleted.isnot(True),
        Transaction.is_spam.isnot(True),
    )

    account_source = data.get('account_source') or data.get('account_id')
    if account_source and str(account_source).lower() != 'all':
        query = query.filter(Transaction.account_balance_source == str(account_source))

    tx_type = data.get('tx_type')
    if tx_type and tx_type.lower() in ('credit', 'debit'):
        query = query.filter(Transaction.type == tx_type.lower())

    q = data.get('q')
    if q and str(q).strip():
        search_pattern = f"%{str(q).strip().lower()}%"
        query = query.filter(
            func.lower(Transaction.purpose).like(search_pattern) |
            func.lower(Transaction.sender).like(search_pattern) |
            func.lower(Transaction.receiver).like(search_pattern) |
            func.lower(Transaction.notes).like(search_pattern)
        )

    period = data.get('period')
    if period:
        p_str = str(period).lower().strip()
        now = datetime.now()
        if p_str in ('this_week', 'week'):
            from datetime import timedelta
            query = query.filter(Transaction.date >= now - timedelta(days=7))
        elif p_str in ('this_month', 'month'):
            query = query.filter(
                extract('year', Transaction.date) == now.year,
                extract('month', Transaction.date) == now.month
            )
        elif p_str in ('this_year', 'year'):
            query = query.filter(extract('year', Transaction.date) == now.year)
        elif len(p_str) == 7 and p_str[4] == '-':
            try:
                dt_p = datetime.strptime(p_str, "%Y-%m")
                query = query.filter(
                    extract('year', Transaction.date) == dt_p.year,
                    extract('month', Transaction.date) == dt_p.month
                )
            except ValueError:
                pass

    transactions = query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()

    if not transactions:
        return jsonify({'message': 'No transactions found for the selected export criteria.', 'count': 0}), 404

    # Build Filename
    date_str = datetime.now().strftime('%Y-%m-%d')
    acc_display = _normalize_account_source(account_source, account_map)
    acc_clean = acc_display.replace(' ', '_').replace('/', '_')
    if acc_clean.lower() == 'all_accounts':
        filename = f"Aurestra_Transactions_{date_str}"
    else:
        filename = f"Aurestra_Transactions_{acc_clean}_{date_str}"

    if export_format == 'pdf':
        return _generate_pdf_response(current_user, transactions, filename, account_source, account_map)
    else:
        return _generate_csv_response(transactions, filename, account_map)


def _generate_csv_response(transactions, filename, account_map=None):
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header Row
    writer.writerow([
        'Date',
        'Merchant / Counterparty',
        'Category',
        'Account',
        'Transaction Type',
        'Amount',
        'Currency',
        'Entry Source',
        'Notes'
    ])

    for t in transactions:
        # Determine clean counterparty
        is_credit = (t.type or '').lower() == 'credit'
        counterparty = t.sender if is_credit else t.receiver
        if not counterparty or counterparty.lower() in ('me', 'merchant', 'bank'):
            counterparty = t.purpose or 'General'

        writer.writerow([
            _format_date(t.date),
            counterparty,
            t.purpose or 'Uncategorized',
            _normalize_account_source(t.account_balance_source, account_map),
            _normalize_type(t.type),
            f"{float(t.amount or 0):.2f}",
            'PKR',
            _normalize_source(t.source),
            (t.notes or '').replace('\r\n', ' ').replace('\n', ' ')
        ])

    csv_data = output.getvalue()
    output.close()

    response = make_response(csv_data)
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
    return response


def _generate_pdf_response(user, transactions, filename, account_source, account_map=None):
    total_count = len(transactions)
    total_income = sum(float(t.amount or 0) for t in transactions if (t.type or '').lower() == 'credit')
    total_expense = sum(float(t.amount or 0) for t in transactions if (t.type or '').lower() == 'debit')
    net_change = total_income - total_expense

    user_name = user.full_name or user.email or 'Aurestra User'
    period_str = datetime.now().strftime('%B %Y')
    gen_date = datetime.now().strftime('%Y-%m-%d %H:%M PKT')

    # Build rows HTML
    rows_html = ""
    for idx, t in enumerate(transactions):
        is_credit = (t.type or '').lower() == 'credit'
        counterparty = t.sender if is_credit else t.receiver
        if not counterparty or counterparty.lower() in ('me', 'merchant', 'bank'):
            counterparty = t.purpose or 'General'

        amt_prefix = "+" if is_credit else "-"
        amt_color = "#10B981" if is_credit else "#EF4444"
        bg_style = 'background-color: #0F172A;' if idx % 2 == 1 else 'background-color: #1E293B;'

        dt_formatted = _format_date(t.date).split(' ')[0]

        rows_html += f"""
        <tr style="{bg_style}">
            <td style="padding: 8px 10px; border-bottom: 1px solid #334155; font-size: 11px;">{dt_formatted}</td>
            <td style="padding: 8px 10px; border-bottom: 1px solid #334155; font-size: 11px; font-weight: 600; max-width: 140px; overflow: hidden;">{counterparty}</td>
            <td style="padding: 8px 10px; border-bottom: 1px solid #334155; font-size: 11px; color: #94A3B8;">{t.purpose or 'General'}</td>
            <td style="padding: 8px 10px; border-bottom: 1px solid #334155; font-size: 11px; color: #94A3B8;">{_normalize_account_source(t.account_balance_source, account_map)}</td>
            <td style="padding: 8px 10px; border-bottom: 1px solid #334155; font-size: 11px; text-align: right; font-weight: 700; color: {amt_color};">
                {amt_prefix}PKR {_format_amount(t.amount)}
            </td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Aurestra Financial Report</title>
        <style>
            @page {{
                size: A4;
                margin: 15mm;
            }}
            body {{
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                background-color: #0B0F19;
                color: #F8FAFC;
                margin: 0;
                padding: 20px;
                -webkit-print-color-adjust: exact;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 2px solid #00C9A7;
                padding-bottom: 15px;
                margin-bottom: 20px;
            }}
            .brand {{
                font-size: 24px;
                font-weight: 900;
                letter-spacing: 2px;
                color: #00C9A7;
            }}
            .title {{
                font-size: 16px;
                color: #94A3B8;
                text-align: right;
            }}
            .meta-box {{
                background-color: #1E293B;
                border-radius: 10px;
                padding: 12px 16px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                font-size: 12px;
                color: #CBD5E1;
            }}
            .summary-cards {{
                display: flex;
                gap: 10px;
                margin-bottom: 25px;
            }}
            .card {{
                flex: 1;
                background: #1E293B;
                border-radius: 10px;
                padding: 12px;
                border: 1px solid #334155;
            }}
            .card-label {{
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: #94A3B8;
                margin-bottom: 4px;
            }}
            .card-val {{
                font-size: 16px;
                font-weight: 700;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }}
            th {{
                background-color: #0F172A;
                color: #00C9A7;
                text-align: left;
                padding: 10px;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
                border-bottom: 2px solid #334155;
            }}
            .footer {{
                margin-top: 30px;
                text-align: center;
                font-size: 10px;
                color: #64748B;
                border-top: 1px solid #334155;
                padding-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <div class="brand">AURESTRA</div>
                <div style="font-size: 12px; color: #94A3B8; margin-top: 2px;">Financial Intelligence System</div>
            </div>
            <div class="title">
                <div style="font-weight: bold; color: #F8FAFC;">TRANSACTION STATEMENT</div>
                <div style="font-size: 11px;">{period_str}</div>
            </div>
        </div>

        <div class="meta-box">
            <div><strong>User:</strong> {user_name}</div>
            <div><strong>Account Scope:</strong> {_normalize_account_source(account_source)}</div>
            <div><strong>Generated:</strong> {gen_date}</div>
        </div>

        <div class="summary-cards">
            <div class="card">
                <div class="card-label">Total Volume</div>
                <div class="card-val">{total_count} Txns</div>
            </div>
            <div class="card">
                <div class="card-label">Total Income</div>
                <div class="card-val" style="color: #10B981;">PKR {_format_amount(total_income)}</div>
            </div>
            <div class="card">
                <div class="card-label">Total Expenses</div>
                <div class="card-val" style="color: #EF4444;">PKR {_format_amount(total_expense)}</div>
            </div>
            <div class="card">
                <div class="card-label">Net Cashflow</div>
                <div class="card-val" style="color: {'#10B981' if net_change >= 0 else '#EF4444'};">PKR {_format_amount(net_change)}</div>
            </div>
        </div>

        <h3 style="font-size: 13px; letter-spacing: 1px; color: #F8FAFC; margin-bottom: 8px;">DETAILED TRANSACTIONS</h3>
        <table>
            <thead>
                <tr>
                    <th style="width: 15%;">Date</th>
                    <th style="width: 30%;">Merchant / Party</th>
                    <th style="width: 25%;">Category</th>
                    <th style="width: 15%;">Account</th>
                    <th style="width: 15%; text-align: right;">Amount</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="footer">
            This statement was securely generated by Aurestra. Confidential financial data.
        </div>
    </body>
    </html>
    """

    response = make_response(html_content)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    response.headers['Content-Disposition'] = f'inline; filename="{filename}.html"'
    return response
