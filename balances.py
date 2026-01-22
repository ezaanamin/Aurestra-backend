import re

def extract_balances_from_bank(text):
    # Normalize: remove extra spaces
    clean_text = re.sub(r'\s+', ' ', text)
    
    # Patterns for Opening
    # Matches: "Opening Balance 1,234.56", "Opening Balance: 1,234.56", "B/F 1,234.56", "Brought Forward 35,239.00"
    opening_pattern = r"(?:Opening Balance|Balance B/F|B\/F|Brought Forward)[\s:]*([0-9,]+\.\d+)"
    opening = re.search(opening_pattern, clean_text, re.IGNORECASE)
    
    # Find ALL matches for Closing Balance (Standard and Reverse)
    closing_candidates = []
    
    # 1. Standard: "Closing Balance 2,128.00"
    matches_std = re.findall(r"(?:Closing Balance|Balance C/F|C\/F|Carried Forward)[\s:]*([0-9,]+\.\d+)", clean_text, re.IGNORECASE)
    closing_candidates.extend(matches_std)
    
    # 2. Reverse: "2,128.00 Closing Balance"
    matches_rev = re.findall(r"([0-9,]+\.\d+)[\s:]*(?:Closing Balance|Balance C/F|C\/F|Carried Forward)", clean_text, re.IGNORECASE)
    closing_candidates.extend(matches_rev)
    
    # Process candidates
    best_closing = 0.0
    for c in closing_candidates:
        val = float(c.replace(',', ''))
        if val > best_closing:
            best_closing = val
            
    # Do same for Opening (simple single match is usually okay, but let's be safe or just take the one corresponding to Closing? 
    # Hard to link disparate regex matches. For now, max Opening is safe guess).
    opening_candidates = re.findall(r"(?:Opening Balance|Balance B/F|B\/F|Brought Forward)[\s:]*([0-9,]+\.\d+)", clean_text, re.IGNORECASE)
    # Reverse Opening
    opening_candidates.extend(re.findall(r"([0-9,]+\.\d+)[\s:]*(?:Opening Balance|Balance B/F|B\/F|Brought Forward)", clean_text, re.IGNORECASE))
    
    best_opening = 0.0
    for c in opening_candidates:
         val = float(c.replace(',', ''))
         # If multiple accounts, sum? No, usually distinct. Max is safest for "Main Account".
         if val > best_opening:
             best_opening = val

    print(f"💰 Balance Candidates - Closing: {closing_candidates}, Opening: {opening_candidates}")
    print(f"💰 Selected: Open {best_opening}, Close {best_closing}")
    
    return {
        "opening_balance": best_opening,
        "closing_balance": best_closing,
    }

    return {
        "opening_balance": best_opening,
        "closing_balance": best_closing,
    }

def extract_transactions_from_bank(text):
    """
    Parses transaction rows from Bank Statement text.
    Handles multi-line transactions where details wrap.
    Looks for block starting with Date and ending with [Amount] [Balance].
    """
    transactions = []
    
    # Normalize text to fix some spacing but keep newlines for structure
    # PyMuPDF often yields lines.
    lines = text.split('\n')
    
    current_balance = 0.0
    
    # 1. Find STARTING Balance
    normalized_text = re.sub(r'\s+', ' ', text)
    opening_pattern = r"(?:Opening Balance|Balance B/F|B\/F|Brought Forward)[\s:]*([0-9,]+\.\d+)"
    match = re.search(opening_pattern, normalized_text, re.IGNORECASE)
    if not match:
         match = re.search(r"([0-9,]+\.\d+)[\s:]*(?:Opening Balance|Balance B/F|B\/F|Brought Forward)", normalized_text, re.IGNORECASE)
    if match:
        current_balance = float(match.group(1).replace(',', ''))
    
    # Regex for Date "DD/MM/YYYY" at start of line
    date_regex = re.compile(r'^(\d{2}/\d{2}/\d{4})')
    # Regex to find ANY amount-like string: 1,234.56
    amount_regex = re.compile(r'([0-9]{1,3}(?:,[0-9]{3})*\.\d{2})')

    pending_tx = None # {date, desc_lines: []}

    def process_pending(tx, running_bal):
        # Helper to finalize a transaction
        full_desc = " ".join(tx["desc_lines"])
        
        # FILTER: Abort if this "transaction" is actually a Header/Footer/Summary
        if any(bad in full_desc for bad in ["Print Date", "Total", "Statement of Account", "Carried Forward", "Brought Forward"]):
             return None, running_bal
        
        # Search for amounts in the FULL text block (often at the end)
        amount_matches = list(amount_regex.finditer(full_desc))
        
        if len(amount_matches) >= 2:
            # Last is Balance, Second-Last is Tx Amount
            try:
                bal_match = amount_matches[-1]
                amt_match = amount_matches[-2]
                
                line_balance = float(bal_match.group(1).replace(',', ''))
                tx_amount = float(amt_match.group(1).replace(',', ''))
                
                # Check for "clean" amount match (heuristic for confidence)
                # A clean match has whitespace (or start of string) immediately before it.
                start_idx = amt_match.start()
                is_clean_amount = False
                if start_idx == 0 or full_desc[start_idx-1].isspace():
                    is_clean_amount = True
                
                # Determine type based on scraped balance difference
                diff = line_balance - running_bal
                
                # Default type from diff
                tx_type = "debit"
                if diff > 0.001:
                    tx_type = "credit"
                elif diff < -0.001:
                    tx_type = "debit"
                
                # HELPER: Text-based Type Detection overrides Diff if reliable
                # 'CR' often standalone or ' CR'. 'DR' same.
                # 'FT' is Fund Transfer (usually Debit out, unless IBFT CR)
                upper_desc = full_desc.upper()
                
                text_type = None
                if re.search(r'\bCR\b|CREDIT|DEPOSIT|INWARD', upper_desc):
                    text_type = "credit"
                elif re.search(r'\bDR\b|DEBIT|TAX|CHARGES|FT\b|TRANSFER|PAYMENT', upper_desc):
                    text_type = "debit"
                
                # Logic:
                # If we have a clean amount, we trust the Text + Heuristics more than the Balance Diff
                # especially if Balance Diff is suspect (e.g. huge jump not matching amount).
                
                final_amount = tx_amount
                final_balance = line_balance
                
                if is_clean_amount:
                   # Use Text Type if distinct, otherwise fallback to diff (if reasonable)
                   # If diff is tiny (0), we default to text_type or debit.
                   
                   if text_type:
                       tx_type = text_type
                   else:
                       # If no text hint, trust diff? 
                       # But if diff is huge and mismatching amount, diff is junk.
                       # If abs(diff) ~~ tx_amount, diff is good.
                       pass # keep diff-based tx_type
                       
                   # Recalculate Balance
                   final_amount = tx_amount
                   if tx_type == "debit":
                       final_balance = running_bal - final_amount
                   else:
                       final_balance = running_bal + final_amount

                else:
                    # DIRTY MATCH (Merged text)
                    if abs(diff) > 0.001:
                        # If scraped amount is wildly different from diff, use diff
                        if abs(tx_amount - abs(diff)) > 1.0:
                             final_amount = abs(diff)
                             # Since we used diff, the type from diff is definitionally correct for the math
                             # even if physically wrong (e.g. reversed). 
                             # But we want math consistency.
                    
                    final_balance = line_balance

                # Clean Description
                clean_desc = full_desc.replace(bal_match.group(0), "").replace(amt_match.group(0), "")

                return {
                    "date": tx["date"],
                    "description": clean_desc.strip(),
                    "amount": final_amount,
                    "type": tx_type,
                    "running_balance": final_balance
                }, final_balance
            except Exception as e:
                pass
        
        return None, running_bal


    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Check if line STARTS with Date (New Transaction)
        date_match = date_regex.match(line)
        
        # Note: Some lines might START with Date but be "Value Date" or part of desc?
        # Usually Tx row starts with Date.
        
        if date_match and "From" not in line and "Statement" not in line and "Print Date" not in line and "Total" not in line:
            # If we had a pending transaction, we must force-process it (though usually it finishes on its own)
            # Actually, standard flow: finish previous tx when new one starts?
            # PROBLEM: We don't know the Amounts of previous tx until we read all its lines.
            # So: YES, process previous pending tx now.
            
            if pending_tx:
                # Process the previous one
                finalized, new_bal = process_pending(pending_tx, current_balance)
                if finalized:
                    transactions.append(finalized)
                    # Note: process_pending returns the calculated new_bal.
                    # We accept it. But if we hit a Brought Forward later, it will correct us.
                    current_balance = new_bal
            
            # Start New Tx
            pending_tx = {
                "date": date_match.group(1),
                "desc_lines": [line]
            }
        else:
            # Not a new date line.
            # FILTER JUNK: Check for Header/Footer content
            low_line = line.lower()
            if any(k in low_line for k in ["page ", "account", "currency", "bank al habib", "statement", "check", "carried forward", "brought forward"]):
                continue

            # If we have a pending tx, append this line to it (it's wrapped text)
            if pending_tx:
                pending_tx["desc_lines"].append(line)
                
                # OPTIONAL: Check if this line contains the Balance closing pattern?
                # If so, we could finalize early.
                # But waiting for next Date is safer to ensure we got full text?
                # EXCEPT for the LAST transaction in the file.
            else:
                pass # Header/Junk lines before first tx
                
    # End of file: Process the very last pending transaction
    if pending_tx:
        finalized, new_bal = process_pending(pending_tx, current_balance)
        if finalized:
            transactions.append(finalized)

    return transactions

def extract_balances_from_wallet(text):
    clean_text = re.sub(r'\s+', ' ', text)
    
    # Wallet might capture Easypaisa/JazzCash formats
    # Often "Balance: Rs. 500" or similar
    
    # Try generic "Balance" if specific "Opening" not found, but be careful
    opening_pattern = r"(?:Opening Balance|Balance B/F|Previous Balance)[\s:]*(?:Rs\.?)?[\s]*([0-9,]+\.\d+)"
    opening = re.search(opening_pattern, clean_text, re.IGNORECASE)
    
    closing_pattern = r"(?:Closing Balance|Current Balance|New Balance)[\s:]*(?:Rs\.?)?[\s]*([0-9,]+\.\d+)"
    closing = re.search(closing_pattern, clean_text, re.IGNORECASE)
    
    return {
        "opening_balance": opening.group(1).replace(',', '') if opening else "0.0",
        "closing_balance": closing.group(1).replace(',', '') if closing else "0.0",
    }
