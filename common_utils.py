import re
import email
import imaplib
from PyPDF2 import PdfReader
from email.header import decode_header

def decode_mime_words(s):
    if not s:
        return ""
    parts = decode_header(s)
    return "".join(
        t[0].decode(t[1] or "utf-8", errors="replace") if isinstance(t[0], bytes) else t[0]
        for t in parts
    )

def extract_text_from_pdf(path, password=None):
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and password:
            if reader.decrypt(password) == 0:
                print("❌ Wrong PDF password.")
                return None

        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return text.strip()
    except Exception as e:
        print("❌ PDF read error:", e)
        return None
