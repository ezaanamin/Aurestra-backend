import sys, os
sys.path.insert(0, os.path.abspath('.'))

from services.receipt_parser import parse_receipt_text

raw_text = '9:350 & = vee B wall tl BS\n\neasypaisa\n\nYou have received money.\n\n12-Jun-2026 5:29 PM\nID#51212738700\n\nBank Account\n\nHiba Dawood\n\nStandard Chartered Bank\n0114*****01\n\nSent by\nAzan Amin\n03333977444\n\nAmount\n4,000.00\n\nFee / Charge\nRs 1.55\n\nRs. 4,001.55'

result = parse_receipt_text(raw_text)
print("PARSED RESULT:")
for k, v in result.items():
    print(f"  {k}: {v}")
