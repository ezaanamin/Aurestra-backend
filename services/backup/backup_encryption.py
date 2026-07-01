import os
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── User Backup Constants (AES-256-GCM) ──────────────────────────────
MAGIC        = b"AUBE"
FILE_VERSION = 1
APP_VERSION  = "1.0.0"
DB_VERSION   = 1
ENC_VERSION  = "AES256GCM-v1"
PBKDF2_ITER  = 310_000

# ── System Backup Constants (AES-256-CBC) ────────────────────────────
SYS_PBKDF2_ITER = 100_000

class BackupEncryption:
    """Handles both User (AES-GCM) and System (AES-CBC) encryption schemes."""
    
    @staticmethod
    def _derive_user_key(decryption_key: str, salt: bytes) -> bytes:
        """Derive a 32-byte AES key for user backups."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=PBKDF2_ITER,
            backend=default_backend(),
        )
        return kdf.derive(decryption_key.encode("utf-8"))

    @staticmethod
    def encrypt_user_payload(plaintext_bytes: bytes, decryption_key: str) -> bytes:
        """Encrypt user data using AES-256-GCM. Returns format: [4B magic][1B version][16B salt][12B nonce][ciphertext+16B GCM tag]"""
        salt  = os.urandom(16)
        nonce = os.urandom(12)
        key   = BackupEncryption._derive_user_key(decryption_key, salt)

        aesgcm     = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)  # includes 16B GCM tag

        return MAGIC + struct.pack("B", FILE_VERSION) + salt + nonce + ciphertext

    @staticmethod
    def decrypt_user_payload(file_bytes: bytes, decryption_key: str) -> bytes:
        """Decrypt user data. Raises ValueError on bad key/format."""
        if len(file_bytes) < 4 + 1 + 16 + 12 + 16:
            raise ValueError("File too short to be a valid AUBE backup.")
        if file_bytes[:4] != MAGIC:
            raise ValueError("Not a valid Aurestra backup file (bad magic bytes).")

        version = struct.unpack("B", file_bytes[4:5])[0]
        if version != FILE_VERSION:
            raise ValueError(f"Unsupported backup version {version}.")

        salt       = file_bytes[5:21]
        nonce      = file_bytes[21:33]
        ciphertext = file_bytes[33:]

        key = BackupEncryption._derive_user_key(decryption_key, salt)
        try:
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ciphertext, None)
        except Exception:
            raise ValueError("Decryption failed — wrong decryption key or corrupted backup.")

    # ─────────────────────────────────────────────────────────────────
    # System Encryption (Legacy AES-256-CBC)
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _derive_system_key(password: str, salt: bytes) -> bytes:
        """Derive key for system backups."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=SYS_PBKDF2_ITER,
            backend=default_backend(),
        )
        return kdf.derive(password.encode())

    @staticmethod
    def encrypt_system_file(src: str, dst: str, password: str) -> None:
        """AES-256-CBC encrypt a system file. Format: [16B salt][16B IV][ciphertext]."""
        salt = os.urandom(16)
        iv   = os.urandom(16)
        key  = BackupEncryption._derive_system_key(password, salt)

        cipher    = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()

        with open(src, "rb") as f:
            data = f.read()

        padder     = sym_padding.PKCS7(128).padder()
        padded     = padder.update(data) + padder.finalize()
        ciphertext = encryptor.update(padded) + encryptor.finalize()

        with open(dst, "wb") as f:
            f.write(salt + iv + ciphertext)
