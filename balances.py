import re
import pandas as pd
from tabulate import tabulate

def extract_balances_from_bank(text, target_account=None):
    """
    Extracts opening and closing balances from bank statement text.
    Uses table-based parsing for accuracy.
    Prints table and returns all closing balances found.
    """
    print(f"\n🔍 BALANCE EXTRACTION DEBUG:")
    print(f"   Text length: {len(text)} chars")
    print(f"   Target account: {target_account}")
    
    # Normalize text
    clean_text = re.sub(r'\s+', ' ', text)
    
    # 1. ISOLATE THE SECTION (if not already done by caller)
    search_context = text  # Use original text with newlines
    if target_account:
        acct_indices = [m.start() for m in re.finditer(re.escape(target_account), clean_text)]
        
        if acct_indices:
            print(f"   Found {len(acct_indices)} occurrences of account number")
            
            best_start = acct_indices[0]
            for idx in acct_indices:
                nearby_text = clean_text[idx:idx+300].upper()
                if "DETAILS" in nearby_text or "TYPE" in nearby_text or "CURRENCY" in nearby_text:
                    best_start = idx
                    print(f"   ✅ Found Detail Section for {target_account} at pos {idx}")
                    break
            
            # Map back to original text position
            original_start = text.find(target_account)
            search_context = text[original_start:]
            
            # Find end of section
            next_acct = re.search(r'Account No\s*:\s*\d+|SUMMARY OF ALL ACCOUNTS', search_context[len(target_account):])
            if next_acct:
                search_context = search_context[:len(target_account) + next_acct.start()]
                print(f"   ✂️ Isolated section")

    print(f"   Final search context: {len(search_context)} chars")
    
    # 2. PARSE INTO TABLE STRUCTURE
    lines = search_context.split('\n')
    
    # Find all lines with balance information
    balance_data = []
    
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
            
        line_upper = line_clean.upper()
        
        # Skip totals and headers
        if any(skip in line_upper for skip in ['TOTAL', 'HEADER', 'PAGE', 'STATEMENT OF']):
            continue
        
        # Look for balance keywords
        is_opening = any(kw in line_upper for kw in ['OPENING BALANCE', 'BALANCE B/F', 'BROUGHT FORWARD'])
        is_closing = any(kw in line_upper for kw in ['CLOSING BALANCE', 'BALANCE C/F', 'CARRIED FORWARD'])
        
        if is_opening or is_closing:
            # Extract all amounts from this line (and next line if needed)
            context = line_clean
            if i + 1 < len(lines):
                context += ' ' + lines[i + 1].strip()
            
            amounts = re.findall(r'([0-9,]+\.\d{2})', context)
            
            if amounts:
                balance_type = 'opening' if is_opening else 'closing'
                
                # CRITICAL FIX: Find the amount closest to the balance keyword
                # For "31/01/2026 1,104.80 Closing Balance" we want 1,104.80 NOT 31
                
                # Strategy: Find which amount is closest to the keyword
                keyword = None
                if is_opening:
                    if 'OPENING BALANCE' in line_upper:
                        keyword = 'OPENING BALANCE'
                    elif 'BALANCE B/F' in line_upper or 'B/F' in line_upper:
                        keyword = 'B/F'
                    elif 'BROUGHT FORWARD' in line_upper:
                        keyword = 'BROUGHT FORWARD'
                else:  # closing
                    if 'CLOSING BALANCE' in line_upper:
                        keyword = 'CLOSING BALANCE'
                    elif 'BALANCE C/F' in line_upper or 'C/F' in line_upper:
                        keyword = 'C/F'
                    elif 'CARRIED FORWARD' in line_upper:
                        keyword = 'CARRIED FORWARD'
                
                # Find position of keyword in line
                keyword_pos = line_upper.find(keyword) if keyword else -1
                
                # Find which amount is closest to the keyword
                selected_amount = amounts[-1]  # Default: last amount
                
                if keyword_pos != -1 and len(amounts) > 1:
                    # Multiple amounts found - pick the one nearest to keyword
                    min_distance = float('inf')
                    
                    for amt in amounts:
                        amt_pos = line_clean.upper().find(amt.replace(',', ''))
                        if amt_pos == -1:
                            # Try finding with commas
                            amt_pos = line_clean.find(amt)
                        
                        if amt_pos != -1:
                            distance = abs(amt_pos - keyword_pos)
                            if distance < min_distance:
                                min_distance = distance
                                selected_amount = amt
                
                # Clean amounts - remove commas and convert to float
                try:
                    balance_value = float(selected_amount.replace(',', ''))
                except:
                    continue
                
                balance_data.append({
                    'type': balance_type.upper(),
                    'line_number': i + 1,
                    'description': line_clean[:80],  # First 80 chars
                    'amounts_found': amounts,
                    'selected_amount': selected_amount,
                    'balance_value': balance_value,
                })
    
    # 3. PRINT TABLE
    if balance_data:
        print(f"\n📊 BALANCE TABLE EXTRACTED:")
        print("=" * 140)
        
        # Create DataFrame for nice display
        df = pd.DataFrame(balance_data)
        
        # Print using tabulate for better formatting
        table = tabulate(
            df[['type', 'line_number', 'balance_value', 'selected_amount', 'description']],
            headers=['Type', 'Line#', 'Balance', 'Selected', 'Description'],
            tablefmt='grid',
            floatfmt='.2f'
        )
        print(table)
        print("=" * 140)
        
        # Show details for lines with multiple amounts
        multi_amount_lines = [b for b in balance_data if len(b['amounts_found']) > 1]
        if multi_amount_lines:
            print(f"\n⚠️ Lines with MULTIPLE amounts (showing selection logic):")
            for entry in multi_amount_lines:
                print(f"   Line {entry['line_number']}: Found {entry['amounts_found']} → Selected '{entry['selected_amount']}' (closest to keyword)")
    else:
        print("\n⚠️ No balance data found!")
    
    # 4. EXTRACT BALANCES
    opening_balance = 0.0
    closing_balance = 0.0
    all_closing_balances = []
    
    # Get FIRST opening balance
    opening_entries = [b for b in balance_data if b['type'] == 'OPENING']
    if opening_entries:
        opening_balance = opening_entries[0]['balance_value']
        print(f"\n✅ Selected Opening Balance: {opening_balance:,.2f} (from line {opening_entries[0]['line_number']})")
    else:
        print(f"\n⚠️ WARNING: No opening balance found!")
    
    # Get ALL closing balances
    closing_entries = [b for b in balance_data if b['type'] == 'CLOSING']
    if closing_entries:
        all_closing_balances = [b['balance_value'] for b in closing_entries]
        closing_balance = closing_entries[-1]['balance_value']  # LAST one
        
        print(f"\n💰 ALL CLOSING BALANCES FOUND: {len(closing_entries)}")
        for idx, entry in enumerate(closing_entries, 1):
            marker = "👉 SELECTED" if idx == len(closing_entries) else ""
            print(f"   {idx}. Line {entry['line_number']:4d}: {entry['balance_value']:12,.2f} {marker}")
        
        print(f"\n✅ Selected Closing Balance: {closing_balance:,.2f} (LAST closing - from line {closing_entries[-1]['line_number']})")
    else:
        print(f"\n⚠️ WARNING: No closing balance found!")
    
    return {
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "all_closing_balances": all_closing_balances,
        "balance_table": balance_data,
    }


def extract_transactions_from_bank(text):
    """
    Parses transaction rows from Bank Statement text.
    Handles multi-line transactions where details wrap.
    Looks for block starting with Date and ending with [Amount] [Balance].
    
    CRITICAL: This must handle the COMPLETE account section to extract all transactions.
    """
    transactions = []
    
    print(f"\n📝 TRANSACTION EXTRACTION DEBUG:")
    print(f"   Input text length: {len(text)} chars")
    
    # Pre-process: some PDF extractors mash everything into one line or weird splits.
    # Force a newline before every date pattern to ensure they are at the start of lines.
    text = re.sub(r'(\d{2}/\d{2}/\d{4})', r'\n\1', text)
    
    lines = text.split('\n')
    print(f"   Total lines after split: {len(lines)}")
    
    current_balance = 0.0
    
    # 1. Find STARTING Balance
    normalized_text = re.sub(r'\s+', ' ', text)
    # Flexible pattern for starting balance
    opening_patterns = [
        r"(?:Opening Balance|Balance B/F|B\/F|Brought Forward)\s*:?\s*(?:Rs\.?|PKR)?\s*([0-9,]+\.\d{1,2})",
        r"([0-9,]+\.\d{1,2})\s+(?:Opening Balance|Balance B/F|B\/F|Brought Forward)",
    ]
    
    for pattern in opening_patterns:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match:
            current_balance = float(match.group(1).replace(',', ''))
            print(f"   Starting balance: {current_balance}")
            break
    
    # Regex for Date "DD/MM/YYYY" at start of line
    date_regex = re.compile(r'^(\d{2}/\d{2}/\d{4})')
    # Regex to find ANY amount-like string with decimals
    amount_regex = re.compile(r'([0-9]{1,3}(?:,[0-9]{3})*\.\d{1,2})')

    pending_tx = None # {date, desc_lines: []}
    valid_transactions = 0

    def process_pending(tx, running_bal):
        """Process a pending transaction and return (transaction_dict, new_balance)"""
        full_desc = " ".join(tx["desc_lines"])
        
        # FILTER: Abort if this row is actually a Summary/Header row
        filter_keywords = [
            "PRINT DATE", "TOTAL", "STATEMENT OF ACCOUNT", 
            "CARRIED FORWARD", "BROUGHT FORWARD", "OPENING BALANCE", "CLOSING BALANCE",
            "PREVIOUS BALANCE", "B/F", "C/F", "PAGE", "ACCOUNT NO", "ACCOUNT NUMBER"
        ]
        
        upper_desc = full_desc.upper()
        for keyword in filter_keywords:
            if keyword in upper_desc:
                return None, running_bal
        
        amount_matches = list(amount_regex.finditer(full_desc))
        
        if len(amount_matches) >= 2:
            # Last is Balance, Second-Last is Tx Amount
            try:
                bal_match = amount_matches[-1]
                amt_match = amount_matches[-2]
                
                line_balance = float(bal_match.group(1).replace(',', ''))
                tx_amount = float(amt_match.group(1).replace(',', ''))
                
                # Determine type based on scraped balance difference
                diff = line_balance - running_bal
                
                # Default type from diff
                tx_type = "debit"
                if diff > 0.001:
                    tx_type = "credit"
                elif diff < -0.001:
                    tx_type = "debit"
                else:
                    # If diff is 0, maybe first transaction or mashing error.
                    # Look for text clues.
                    if re.search(r'\bCR\b|CREDIT|DEPOSIT|INWARD|REVERSAL', upper_desc):
                        tx_type = "credit"
                    else:
                        tx_type = "debit"
                
                final_balance = line_balance
                final_amount = tx_amount
                
                # Fallback: if amount doesn't match diff reasonably, use diff for math
                if abs(abs(diff) - tx_amount) > 1.0 and abs(diff) > 0.001:
                    final_amount = abs(diff)

                # Clean Description - remove the amount and balance from description
                clean_desc = full_desc.replace(bal_match.group(0), "").replace(amt_match.group(0), "")
                clean_desc = clean_desc.strip()

                return {
                    "date": tx["date"],
                    "description": clean_desc,
                    "amount": final_amount,
                    "type": tx_type,
                    "running_balance": final_balance
                }, final_balance
            except Exception as e:
                print(f"   ⚠️ Error processing transaction: {e}")
                pass
        
        return None, running_bal


    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Check if line STARTS with Date (New Transaction)
        date_match = date_regex.match(line)
        
        # Additional filters to avoid false positives
        line_upper = line.upper()
        is_header = any(k in line_upper for k in ["FROM", "STATEMENT", "PRINT DATE", "TOTAL", "PAGE"])
        
        if date_match and not is_header:
            # Process previous pending transaction
            if pending_tx:
                finalized, new_bal = process_pending(pending_tx, current_balance)
                if finalized:
                    transactions.append(finalized)
                    current_balance = new_bal
                    valid_transactions += 1
            
            # Start new pending transaction
            pending_tx = {
                "date": date_match.group(1),
                "desc_lines": [line]
            }
        else:
            # This is a continuation line
            if pending_tx:
                # Junk filter for sub-lines
                low_line = line.lower()
                junk_keywords = ["page ", "account", "currency", "bank al habib", "statement", "check"]
                if any(k in low_line for k in junk_keywords):
                    continue
                pending_tx["desc_lines"].append(line)
                
    # Process last pending transaction
    if pending_tx:
        finalized, new_bal = process_pending(pending_tx, current_balance)
        if finalized:
            transactions.append(finalized)
            valid_transactions += 1

    print(f"   ✅ Extracted {valid_transactions} valid transactions")
    
    return transactions

def extract_balances_from_wallet(text):
    """Extract balances from wallet statement (Easypaisa/JazzCash format)"""
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