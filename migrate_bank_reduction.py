from database import db
from model import Transaction, Category

MIGRATION_MAPPING = {
    'tax': 'Tax / FED',
    'fed': 'Tax / FED',
    'bank fee': 'Bank Fee / Service Charge',
    'fee': 'Bank Fee / Service Charge',
    'service charge': 'Bank Fee / Service Charge',
    'sms': 'SMS Alert Fee',
    'atm': 'ATM Cash Withdrawal Fee',
    'card': 'Card Maintenance Fee',
    'annual': 'Card Maintenance Fee',
    'profit': 'Profit Withholding Tax',
}

PREDEFINED_REASONS = {
    'Tax / FED',
    'Bank Fee / Service Charge',
    'SMS Alert Fee',
    'ATM Cash Withdrawal Fee',
    'Card Maintenance Fee',
    'Profit Withholding Tax',
    'Other'
}

def migrate_bank_reduction_data():
    """
    Safely migrates historical 'Bank Reduction' transactions to the new structured
    bank_reduction_reason field without data loss.
    """
    bank_cat = Category.query.filter(Category.name.ilike('bank reduction')).first()
    
    # Find all transactions where purpose is Bank Reduction OR notes/purpose contain bank reduction
    txns = Transaction.query.filter(
        (Transaction.purpose.ilike('%bank reduction%')) | 
        (Transaction.category_id == (bank_cat.id if bank_cat else -1))
    ).all()

    updated = 0
    for t in txns:
        # Ensure purpose is canonical "Bank Reduction"
        if bank_cat:
            t.category_id = bank_cat.id
        t.purpose = "Bank Reduction"

        if not t.bank_reduction_reason or not str(t.bank_reduction_reason).strip():
            # Attempt to infer from notes or old purpose
            text_to_search = f"{t.notes or ''} {t.purpose or ''}".lower()
            matched_reason = None
            for keyword, mapped_reason in MIGRATION_MAPPING.items():
                if keyword in text_to_search:
                    matched_reason = mapped_reason
                    break
            
            if matched_reason:
                t.bank_reduction_reason = matched_reason
            else:
                # If notes exist, use "Other: <notes>", otherwise default to "Other"
                if t.notes and str(t.notes).strip():
                    t.bank_reduction_reason = f"Other: {str(t.notes).strip()}"
                else:
                    t.bank_reduction_reason = "Other"
            updated += 1

    db.session.commit()
    print(f"✅ Migrated {updated} legacy Bank Reduction transactions to structured reasons.")
