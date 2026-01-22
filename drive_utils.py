import os
import io
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from datetime import datetime
from cryptography.fernet import Fernet

# SCOPES must match what we requested in frontend
SCOPES = ['https://www.googleapis.com/auth/drive.file']

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'encryption_key.key')

def load_or_generate_key():
    """Loads the encryption key or generates a new one if it doesn't exist."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'rb') as key_file:
            key = key_file.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_FILE, 'wb') as key_file:
            key_file.write(key)
        # print(f"🔑 New encryption key generated: {key.decode()}")
    return key

# Initialize Cipher Suite
try:
    CIPHER_SUITE = Fernet(load_or_generate_key())
except Exception as e:
    print(f"❌ Failed to initialize encryption: {e}")
    CIPHER_SUITE = None

def get_drive_service(user):
    """
    Returns an authenticated Drive API service for the given User model instance.
    Refreshes the token if necessary.
    """
    if not user.google_refresh_token:
        # print(f"⚠️ User {user.email} has no refresh token.")
        return None

    try:
        # Build Credentials object
        creds = Credentials(
            token=None, # We don't have a current access token, only refresh
            refresh_token=user.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv('GOOGLE_WEB_CLIENT_ID'),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
            scopes=SCOPES
        )

        # Refresh the access token
        creds.refresh(Request())
        
        # Build Service
        service = build('drive', 'v3', credentials=creds)
        return service

    except Exception as e:
        print(f"❌ Failed to build Drive service: {e}")
        return None

def find_folder(service, folder_name, parent_id=None):
    """Searches for a folder with the given name inside parent_id (or root)."""
    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    
    if parent_id:
        query += f" and '{parent_id}' in parents"
        
    try:
        results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        
        if files:
            return files[0]['id'] # Return ID of first match
        return None
    except Exception as e:
        print(f"❌ Find folder error: {e}")
        return None

def create_folder(service, folder_name, parent_id=None):
    """Creates a new folder."""
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    
    if parent_id:
        file_metadata['parents'] = [parent_id]
        
    try:
        file = service.files().create(body=file_metadata, fields='id').execute()
        # print(f"✅ Created folder '{folder_name}' (ID: {file.get('id')})")
        return file.get('id')
    except Exception as e:
        print(f"❌ Create folder error: {e}")
        return None

def ensure_folder_path(service, path_parts):
    """
    Ensures a nested folder structure exists.
    path_parts: list e.g. ["Aurestra Finance", "2026-01"]
    Returns the ID of the final folder.
    """
    parent_id = None
    
    for folder_name in path_parts:
        folder_id = find_folder(service, folder_name, parent_id)
        
        if not folder_id:
            folder_id = create_folder(service, folder_name, parent_id)
            
        if not folder_id:
            return None # Failed to find/create
            
        parent_id = folder_id
        
    return parent_id

def upload_json(service, folder_id, filename, data):
    """Uploads a dictionary as an ENCRYPTED JSON file to the specified folder."""
    try:
        # Convert dict to JSON string
        json_str = json.dumps(data)
        
        # Encrypt
        if CIPHER_SUITE:
            encrypted_data = CIPHER_SUITE.encrypt(json_str.encode('utf-8'))
            # print(f"🔒 Encrypted backup size: {len(encrypted_data)} bytes")
        else:
            print("⚠️ Encryption not active, uploading plaintext (NOT RECOMMENDED)")
            encrypted_data = json_str.encode('utf-8')

        fh = io.BytesIO(encrypted_data)
        
        media = MediaIoBaseUpload(fh, mimetype='application/octet-stream', resumable=True)
        
        # Check if file exists to update or create
        existing_file_query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        results = service.files().list(q=existing_file_query, fields='files(id)').execute()
        files = results.get('files', [])

        file_metadata = {'name': filename}

        if files:
            # Update
            file_id = files[0]['id']
            # Remove parents from metadata for update
            service.files().update(
                fileId=file_id, 
                media_body=media
            ).execute()
            # print(f"✅ Updated file '{filename}' in Drive (Encrypted).")
        else:
            # Create
            file_metadata['parents'] = [folder_id]
            service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields='id'
            ).execute()
            # print(f"✅ Uploaded new file '{filename}' to Drive (Encrypted).")

        return True

    except Exception as e:
        print(f"❌ Upload JSON error: {e}")
        return False

def find_file(service, folder_id, filename):
    """Finds a file ID by name in a specific folder."""
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    try:
        results = service.files().list(q=query, fields='files(id)').execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']
        return None
    except Exception as e:
        print(f"❌ Find file error: {e}")
        return None

def download_json(service, file_id):
    """Downloads a file from Drive and decrypts it."""
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
        # Parse JSON
        fh.seek(0)
        content_bytes = fh.read()
        
        try:
            # Try to decrypt
            if CIPHER_SUITE:
                decrypted_bytes = CIPHER_SUITE.decrypt(content_bytes)
                content_str = decrypted_bytes.decode('utf-8')
                # print("🔓 Successfully decrypted file.")
            else:
                 content_str = content_bytes.decode('utf-8')
        except Exception as e:
            print(f"⚠️ Decryption failed (File might be old/plaintext or bad key): {e}")
            # Fallback for old plaintext files
            content_str = content_bytes.decode('utf-8')

        return json.loads(content_str)
        
    except Exception as e:
        print(f"❌ Download JSON error: {e}")
        return None
