import os
import sys
import shutil
import argparse
import getpass
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

def derive_key(password, salt):
    """Derive a 32-byte (256-bit) key from the password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    return kdf.derive(password.encode())

def encrypt_file(input_path, output_path, password):
    """
    Encrypts a file with AES-256 (CBC mode).
    Prepends a 16-byte random salt and 16-byte IV to the file.
    """
    if not os.path.exists(input_path):
        print(f"❌ Error: Input path '{input_path}' does not exist.")
        return False

    # If directory, zip it first
    is_temp_zip = False
    target_file = input_path
    
    if os.path.isdir(input_path):
        print(f"📦 Input is a directory. Zipping '{input_path}'...")
        shutil.make_archive("temp_encrypt_archive", 'zip', input_path)
        target_file = "temp_encrypt_archive.zip"
        is_temp_zip = True

    try:
        salt = os.urandom(16)
        iv = os.urandom(16)
        key = derive_key(password, salt)

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()

        # Add .enc extension if not provided
        if not output_path.endswith('.enc'):
            output_path += '.enc'

        print(f"🔒 Encrypting '{target_file}' to '{output_path}'...")
        
        file_size = os.path.getsize(target_file)
        
        with open(target_file, 'rb') as f_in, open(output_path, 'wb') as f_out:
            # Write Salt and IV first
            f_out.write(salt)
            f_out.write(iv)
            
            # Read all and pad (Simple approach for script)
            data = f_in.read()
            padder = padding.PKCS7(128).padder()
            padded_data = padder.update(data) + padder.finalize()
            encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
            f_out.write(encrypted_data)

        print(f"✅ Encryption Complete: {output_path}")
        return True

    except Exception as e:
        print(f"❌ Encryption Failed: {e}")
        return False
    finally:
        if is_temp_zip and os.path.exists(target_file):
            os.remove(target_file)
            print("🧹 Cleanup temp zip.")

def decrypt_file(input_path, output_path, password):
    """
    Decrypts a file encrypted by this tool.
    """
    if not os.path.exists(input_path):
        print(f"❌ Error: Input path '{input_path}' does not exist.")
        return False

    try:
        print(f"🔓 Decrypting '{input_path}'...")
        
        with open(input_path, 'rb') as f_in:
            # Read Salt and IV
            salt = f_in.read(16)
            iv = f_in.read(16)
            encrypted_data = f_in.read()

        key = derive_key(password, salt)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()

        decrypted_padded = decryptor.update(encrypted_data) + decryptor.finalize()
        
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(decrypted_padded) + unpadder.finalize()

        # Save decrypted file
        with open(output_path, 'wb') as f_out:
            f_out.write(data)

        print(f"✅ Decryption Complete: {output_path}")
        
        # Auto-Extract if Zip (Structured Backups)
        # Check ZIP signature (PK..) or extension
        is_zip = data.startswith(b'PK\x03\x04')
        if is_zip or output_path.endswith('.zip'):
            try:
                import zipfile
                extract_dir = output_path.replace('.zip', '') + '_extracted'
                if not extract_dir: # Fallback if strange name
                    extract_dir = "restored_backup"
                    
                print(f"📦 Detected ZIP. extracting to '{extract_dir}'...")
                with zipfile.ZipFile(output_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                print(f"📂 Backup Organization Restored in: {extract_dir}/")
                print(f"   |-- migrations/ (Table Schemas)")
                print(f"   |-- seeders/    (Readable INSERTs)")
            except Exception as e:
                print(f"⚠️ Auto-extraction failed: {e}")

        return True

    except Exception as e:
        print(f"❌ Decryption Failed (Wrong Password?): {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual File Encryption Tool (AES-256)")
    parser.add_argument("mode", choices=["encrypt", "decrypt"], help="Mode of operation")
    parser.add_argument("--input", "-i", required=True, help="Input file or folder path")
    parser.add_argument("--output", "-o", required=True, help="Output file path")
    parser.add_argument("--key", "-k", help="Password/Key (Prompted if not provided)")

    args = parser.parse_args()

    password = args.key
    
    # Try to load from .env if not provided
    if not password:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.strip().startswith('BACKUP_PASSWORD='):
                        password = line.strip().split('=', 1)[1]
                        print(f"🔑 Loaded password from .env: {password[:4]}...{password[-2:]}")
                        break
    
    if not password:
        password = getpass.getpass(prompt="Enter Encryption Password: ")

    if args.mode == "encrypt":
        encrypt_file(args.input, args.output, password)
    elif args.mode == "decrypt":
        decrypt_file(args.input, args.output, password)
