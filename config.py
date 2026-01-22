import os
from dotenv import load_dotenv

load_dotenv()

# === BANK CONFIG ===
# === BANK CONFIG ===
BANK_EMAIL_ACCOUNT = os.getenv("BANK_EMAIL_ACCOUNT") or "ezaan.amin@gmail.com"
APP_PASSWORD = os.getenv("APP_PASSWORD")
# Fallback to APP_PASSWORD if specific ones are missing
BANK_APP_PASSWORD = os.getenv("BANK_APP_PASSWORD") or APP_PASSWORD
BANK_PDF_PASSWORD = os.getenv("BANK_PDF_PASSWORD")
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
BANK_SENDER = os.getenv("BANK_SENDER", "estatement@bankalhabib.com")
TARGET_ACCOUNT_NUMBER = os.getenv("TARGET_ACCOUNT_NUMBER", "")
IGNORE_ACCOUNT_NUMBER = os.getenv("IGNORE_ACCOUNT_NUMBER", "")

# === WALLET CONFIG ===
WALLET_APP_PASSWORD = os.getenv("WALLET_APP_PASSWORD") or APP_PASSWORD
WALLET_SUBJECT_KEYWORD = os.getenv("WALLET_SUBJECT_KEYWORD", "easypaisa e-statement")
TEMP_FILE = os.getenv("TEMP_FILE", "temp_statement.pdf")

ATTACHMENTS_DIR = "attachments"
