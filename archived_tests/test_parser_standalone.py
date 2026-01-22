import re
from datetime import datetime

# Regexes from updated sms_parser.py
SENT_TO_PATTERN = re.compile(
    r'(?:PKR|Rs\.)\s*([0-9,]+\.?\d*)\s+sent\s+to\s+(.+?)\s+from\s+your\s+BAHL\s+A/C.*?on\s+(\d{1,2}-[a-z]{3}-\d{4}\s+\d{1,2}:\d{2})',
    re.IGNORECASE
)

def parse_datetime(date_str, time_str="00:00:00"):
    """Parse date and time strings to datetime object (supports month names)"""
    if not date_str:
        return datetime.utcnow()
        
    try:
        # Clean strings
        date_str = date_str.strip()
        time_str = time_str.strip() if time_str else "00:00:00"
        
        # Handle formats like 21-Jan-2026 13:33
        if '-' in date_str and any(m in date_str.lower() for m in ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']):
            full_str = f"{date_str} {time_str}" if time_str != "00:00:00" else date_str
            for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
                try:
                    return datetime.strptime(full_str, fmt)
                except ValueError:
                    continue

        # Legacy numeric formats (10/12/2025 or 10-12-2025)
        date_clean = date_str.replace('-', '/')
        datetime_str = f"{date_clean} {time_str}"
        
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(datetime_str, fmt)
            except ValueError:
                continue
        
        return datetime.utcnow()
    except Exception as e:
        return datetime.utcnow()

test_messages = [
    "PKR 720.00 sent to AZAN AMIN RAAST ID *7444 from your BAHL A/C *6801 on 21-Jan-2026 13:33 Tx ID BAHL2601211333049293907559343",
    "PKR 600.00 sent to AZAN AMIN TMBL from your BAHL A/C *6801 on 21-Jan-2026 17:11 via RAAST Tx ID BAHL2601211711199487063588928",
]

print("🔍 Testing REFINED SMS Regex Patterns (v7)...")
for msg in test_messages:
    match = SENT_TO_PATTERN.search(msg)
    print(f"\nMessage: {msg[:100]}...")
    if match:
        print(f"✅ SENT_TO Match Found!")
        print(f"  Amount: {match.group(1)}")
        print(f"  Receiver: {match.group(2)}")
        print(f"  Date: {match.group(3)}")
        dt = parse_datetime(match.group(3))
        print(f"  Parsed DT: {dt}")
    else:
        print(f"❌ NO MATCH")
