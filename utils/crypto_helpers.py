import os
import base64
import hashlib
import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENC_PREFIX = "__enc__:"

def generate_crypto_salt() -> str:
    """Generates a random 16-byte hex-encoded salt."""
    return os.urandom(16).hex()

def hash_decryption_key(key: str) -> str:
    """Hashes the user's decryption key using bcrypt."""
    if not key:
        raise ValueError("Decryption key cannot be empty")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(key.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_decryption_key(key: str, hashed: str) -> bool:
    """Verifies a decryption key against its bcrypt hash."""
    if not key or not hashed:
        return False
    try:
        return bcrypt.checkpw(key.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def derive_encryption_key(decryption_key: str, salt_hex: str) -> bytes:
    """Derives a 256-bit symmetric encryption key using PBKDF2-SHA256."""
    if not decryption_key or not salt_hex:
        raise ValueError("Decryption key and salt are required for key derivation")
    try:
        salt = bytes.fromhex(salt_hex)
    except Exception as e:
        raise ValueError(f"Invalid salt format: {e}")
    
    # 256-bit key (32 bytes)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        decryption_key.encode('utf-8'),
        salt,
        100000
    )
    return key

def encrypt_field(val: str, key: bytes) -> str:
    """Encrypts a string value using AES-256-GCM and prepends a prefix."""
    if val is None:
        return None
    if not isinstance(val, str):
        val = str(val)
    if not val:
        return val
    if not key:
        raise ValueError("Encryption key required")
        
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit GCM nonce
    ciphertext = aesgcm.encrypt(nonce, val.encode('utf-8'), None)
    encoded = base64.b64encode(nonce + ciphertext).decode('utf-8')
    return f"{ENC_PREFIX}{encoded}"

def decrypt_field(val: str, key: bytes) -> str:
    """Decrypts a value with GCM or returns it as-is if unencrypted or decryption fails."""
    if val is None:
        return None
    if not isinstance(val, str):
        val = str(val)
    if not val.startswith(ENC_PREFIX):
        # Graceful fallback for legacy plaintext data
        return val
    if not key:
        return "[Encrypted]"
        
    try:
        encoded_body = val[len(ENC_PREFIX):]
        raw_data = base64.b64decode(encoded_body)
        if len(raw_data) < 12:
            return val
            
        nonce = raw_data[:12]
        ciphertext = raw_data[12:]
        aesgcm = AESGCM(key)
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted_bytes.decode('utf-8')
    except Exception:
        # Decryption failure (wrong key or corrupted data)
        return "[Decryption Failed]"
