import sys
import os
import argparse
import getpass
from cryptography.fernet import Fernet 
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

def decrypt_backup(file_path, password):
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return

    print("🔓 Reading encrypted file...")
    with open(file_path, 'rb') as f:
        # Read Salt (16 bytes) and IV (16 bytes)
        salt = f.read(16)
        iv = f.read(16)
        encrypted_data = f.read()
    
    # Derive Key
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    try:
        key = kdf.derive(password.encode())
    except Exception as e:
        print(f"❌ Key derivation failed: {e}")
        return

    # Decrypt
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        
        padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
        
        # Unpad
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(padded_data) + unpadder.finalize()
        
        # Save
        if file_path.endswith(".enc"):
            output_path = file_path[:-4]
        else:
            output_path = file_path + ".restored.sql"
            
        with open(output_path, 'wb') as df:
            df.write(data)
            
        print(f"✅ Decrypted successfully! Saved to: {output_path}")

    except ValueError:
        print("❌ Decryption failed: Incorrect Password or Corrupted File.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Decrypt Aurestra Database Backup (AES-256).")
    parser.add_argument("file", help="Path to the encrypted backup file (.enc)")
    
    args = parser.parse_args()
    
    pwd = getpass.getpass("🔑 Enter Backup Password: ")
    decrypt_backup(args.file, pwd)
