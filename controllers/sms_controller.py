# controllers/sms_controller.py

from flask import request, jsonify
from services import sms_service
from sms_parser import BankAlhabibSMSParser


def process_sms():
    data = request.get_json() or {}
    message = data.get('message')
    sender  = data.get('sender', 'BAHL')

    if not message or len(message.strip()) < 10:
        return jsonify({"error": "SMS message required"}), 400

    transaction, is_new = sms_service.process_single_sms(message, sender, meta=data)

    if not transaction:
        if not BankAlhabibSMSParser.is_transaction_sms(message):
            return jsonify({"status": "skipped", "message": "SMS is not a transaction"}), 200
        return jsonify({"status": "failed", "message": "Could not parse SMS"}), 400

    from model import AccountBalance
    accounts = [a.to_dict() for a in AccountBalance.query.all()]
    return jsonify({
        "status":      "success",
        "message":     "Transaction created from SMS",
        "transaction": transaction.to_dict(),
        "accounts":    accounts,
    }), 201


def test_sms():
    data    = request.get_json() or {}
    message = data.get('message')
    sender  = data.get('sender', 'BAHL')

    if not message:
        return jsonify({"error": "SMS message required"}), 400

    parsed = BankAlhabibSMSParser.parse_sms(message, sender)
    if not parsed:
        if not BankAlhabibSMSParser.is_transaction_sms(message):
            return jsonify({"status": "skipped", "message": "Not a transaction SMS"}), 200
        return jsonify({"status": "failed", "message": "Could not parse SMS"}), 400

    resp = {
        "status":  "success",
        "message": "SMS parsed successfully",
        "parsed_data": {
            "type":   parsed['type'],
            "amount": parsed['amount'],
            "purpose": parsed['purpose'],
            "date":   parsed['date'].isoformat(),
            "notes":  parsed['notes'],
        },
    }
    if parsed['type'] == 'credit':
        resp['parsed_data']['sender']   = parsed['sender']
    else:
        resp['parsed_data']['receiver'] = parsed['receiver']
    return jsonify(resp), 200


def process_batch(current_user):
    data     = request.get_json() or {}
    messages = data.get('messages', [])
    if not messages:
        return jsonify({"error": "Messages array required"}), 400
    if len(messages) > 1000:
        return jsonify({"error": "Maximum 1000 messages per batch"}), 400

    stats = sms_service.process_sms_batch(messages)
    return jsonify(stats), 200


def last_sync(current_user):
    return jsonify(sms_service.get_last_sms_sync()), 200
