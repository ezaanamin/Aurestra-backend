import os
import imaplib
from dotenv import load_dotenv

def test_imap():
    load_dotenv(override=True)
    
    email = os.getenv("BANK_EMAIL_ACCOUNT", "amin.ezaan@gmail.com").strip()
    raw_pass = os.getenv("APP_PASSWORD", "")
    clean_pass = raw_pass.replace(" ", "").strip()
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    
    print(f"--- IMAP Diagnostic ---")
    print(f"Account: [{email}]")
    print(f"Raw Pass: [{raw_pass}]")
    print(f"Clean Pass: [{clean_pass}]")
    print(f"Host: [{host}]")
    print("-------------------------")
    
    try:
        print(f"Connecting to {host}...")
        mail = imaplib.IMAP4_SSL(host)
        print("Logging in...")
        mail.login(email, clean_pass)
        print("✅ Login SUCCESSFUL!")
        mail.select("inbox")
        print("✅ Inbox selected.")
        mail.logout()
    except Exception as e:
        print(f"❌ Login FAILED: {str(e)}")

if __name__ == "__main__":
    test_imap()
