import os
import io
import json
import base64
import requests
from datetime import datetime
from cryptography.fernet import Fernet
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

SCOPES = ['https://www.googleapis.com/auth/drive.file']
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.send']
GMAIL_READONLY_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly'
]

# Must match Aurestra/index.js GoogleSignin.configure({ scopes }) exactly (order + strings).
# Used for server auth code exchange and refresh; mismatch causes "Scope has changed" errors.
GOOGLE_SIGNIN_OAUTH_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]
GMAIL_MODIFY_SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly'
]

def _get_google_client_id():
    """Return Google OAuth client ID from supported env names."""
    return os.getenv('GOOGLE_WEB_CLIENT_ID') or os.getenv('GOOGLE_CLIENT_ID')

def _get_google_client_secret():
    """Return Google OAuth client secret from supported env names."""
    return os.getenv('GOOGLE_CLIENT_SECRET') or os.getenv('GOOGLE_WEB_CLIENT_SECRET')

def _clear_google_refresh_token_if_invalid_grant(user, error):
    """
    If Google returns invalid_grant, the stored refresh token is unusable (revoked, expired,
    wrong OAuth client, or password events). Clear it so the next login can store a new one.
    """
    err = str(error).lower()
    if 'invalid_grant' not in err:
        return False
    try:
        from database import db
        user.google_refresh_token = None
        db.session.commit()
        email = getattr(user, 'email', None) or '?'
        print(
            f"⚠️ Google OAuth invalid_grant for {email}: refresh token cleared. "
            "User must sign in with Google again (Settings → sign out, or reinstall session)."
        )
        return True
    except Exception as cleanup_err:
        print(f"⚠️ Could not clear google_refresh_token after invalid_grant: {cleanup_err}")
        return False

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'encryption_key.key')

def load_or_generate_key():
    """Loads the encryption key from Env, file, or generates a new one."""
    # 1. Environment Variable (Best for Render/Production)
    env_key = os.getenv("ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()

    # 2. File System (Local Dev)
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'rb') as key_file:
            key = key_file.read()
            return key
            
    # 3. Generate New (Fallback - Warning: Ephemeral on Render)
    key = Fernet.generate_key()
    try:
        with open(KEY_FILE, 'wb') as key_file:
            key_file.write(key)
        # print(f"🔑 New encryption key generated: {key.decode()}")
    except Exception:
        pass # Might be read-only filesystem
        
    return key

# Initialize Cipher Suite
try:
    CIPHER_SUITE = Fernet(load_or_generate_key())
except Exception as e:
    print(f"❌ Failed to initialize encryption: {e}")
    CIPHER_SUITE = None

def _auth_api_url():
    """Return Auth-api base URL from env, consistent with app.py."""
    return os.getenv('AUTH_API_URL', 'https://9938-119-73-101-66.ngrok-free.app').rstrip('/')

def _auth_api_owner_email():
    return os.getenv('AUTH_API_OWNER_EMAIL', 'ezaan.amin@gmail.com')

def get_auth_service_token(email=None):
    """
    Fetch a fresh Google access token from the centralized Auth-api.
    Uses AUTH_API_OWNER_EMAIL by default (the single authorised account).
    """
    target = email or _auth_api_owner_email()
    auth_url = _auth_api_url()
    try:
        response = requests.get(f"{auth_url}/auth/token", params={"email": target}, timeout=8)
        if response.status_code == 200:
            data = response.json()
            if "access_token" in data:
                return data["access_token"]
            print(f"⚠️ Auth-api /auth/token: {data}")
        else:
            print(f"⚠️ Auth-api /auth/token returned {response.status_code}")
    except Exception as e:
        print(f"⚠️ Cannot reach Auth-api at {auth_url}: {e}")
    return None


def auth_api_drive_proxy(method, path, email=None, **kwargs):
    """
    Forward a Drive API call through Auth-api's /google/proxy/ endpoint.
    `path` should be relative to https://www.googleapis.com/ (e.g. 'drive/v3/files').
    """
    auth_url = _auth_api_url()
    owner = email or _auth_api_owner_email()
    url = f"{auth_url}/google/proxy/{path}"
    params = kwargs.pop('params', {})
    params['email'] = owner
    try:
        resp = requests.request(method, url, params=params, timeout=30, **kwargs)
        if resp.headers.get('content-type', '').startswith('application/json'):
            return resp.json()
        return resp.content
    except Exception as e:
        print(f"⚠️ Auth-api Drive proxy error ({method} {path}): {e}")
        return None

def get_drive_service(user=None):
    """
    Returns an authenticated Drive API service.
    Tries the centralized Auth-api first; falls back to local refresh token.
    `user` may be None — Auth-api will use the owner email by default.
    """
    email = getattr(user, 'email', None) if user else None
    token = get_auth_service_token(email)
    if token:
        creds = Credentials(token=token)
        return build('drive', 'v3', credentials=creds)

    # Fallback to local refresh token logic
    if not user or not user.google_refresh_token:
        # print(f"⚠️ User {getattr(user, 'email', '?')} has no refresh token.")
        return None

    try:
        client_id = _get_google_client_id()
        client_secret = _get_google_client_secret()
        if not client_id or not client_secret:
            print("❌ Drive auth not configured: missing client ID/secret.")
            return None

        # Build Credentials object
        creds = Credentials(
            token=None, # We don't have a current access token, only refresh
            refresh_token=user.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=GOOGLE_SIGNIN_OAUTH_SCOPES
        )

        # Refresh the access token
        creds.refresh(Request())
        
        # Build Service
        service = build('drive', 'v3', credentials=creds)
        return service

    except RefreshError as e:
        _clear_google_refresh_token_if_invalid_grant(user, e)
        print(f"❌ Failed to build Drive service: {e}")
        return None
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
            file_id = files[0]['id']
            service.files().update(
                fileId=file_id, 
                media_body=media
            ).execute()
        else:
            file_metadata['parents'] = [folder_id]
            service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields='id'
            ).execute()

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
            else:
                 content_str = content_bytes.decode('utf-8')
        except Exception as e:
            print(f"⚠️ Decryption failed: {e}")
            content_str = content_bytes.decode('utf-8')

        return json.loads(content_str)
        
    except Exception as e:
        print(f"❌ Download JSON error: {e}")
        return None

def upload_file_from_path(service, folder_id, filename, filepath, mimetype='application/octet-stream'):
    """Uploads a local file to Drive."""
    try:
        media = MediaIoBaseUpload(io.FileIO(filepath, 'rb'), mimetype=mimetype, resumable=True)
        
        # Check if file exists to update
        existing = find_file(service, folder_id, filename)
        
        file_metadata = {'name': filename}
        
        if existing:
            service.files().update(
                fileId=existing, 
                media_body=media
            ).execute()
            print(f"✅ Updated existing backup '{filename}' in Drive.")
        else:
            file_metadata['parents'] = [folder_id]
            service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields='id'
            ).execute()
            print(f"✅ Uploaded new backup '{filename}' to Drive.")
            
        return True
    except Exception as e:
        print(f"❌ Upload File error: {e}")
        return False

def get_gmail_service(user=None, scopes=None):
    """
    Returns an authenticated Gmail API service.
    Tries the centralized Auth-api first; falls back to local refresh token.
    """
    email = getattr(user, 'email', None) if user else None
    token = get_auth_service_token(email)
    if token:
        creds = Credentials(token=token)
        return build('gmail', 'v1', credentials=creds)

    if not user or not user.google_refresh_token:
        return None

    if scopes is None:
        oauth_scopes = GOOGLE_SIGNIN_OAUTH_SCOPES
    else:
        oauth_scopes = scopes

    try:
        client_id = _get_google_client_id()
        client_secret = _get_google_client_secret()
        if not client_id or not client_secret:
            print("❌ Gmail auth not configured: missing client ID/secret.")
            return None

        creds = Credentials(
            token=None,
            refresh_token=user.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=oauth_scopes
        )

        creds.refresh(Request())
        service = build('gmail', 'v1', credentials=creds)
        return service

    except RefreshError as e:
        _clear_google_refresh_token_if_invalid_grant(user, e)
        print(f"❌ Failed to build Gmail service: {e}")
        return None
    except Exception as e:
        print(f"❌ Failed to build Gmail service: {e}")
        return None

def create_message(sender, to, subject, message_text, message_html=None):
    """Create a message for an email."""
    message = MIMEMultipart('alternative')
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject

    part1 = MIMEText(message_text, 'plain')
    message.attach(part1)

    if message_html:
        part2 = MIMEText(message_html, 'html')
        message.attach(part2)

    return {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}

def send_gmail_message(service, user_id, message):
    """Send an email message."""
    try:
        message = (service.users().messages().send(userId=user_id, body=message)
                   .execute())
        return message
    except Exception as error:
        print(f'❌ An error occurred sending email: {error}')
        return None

def list_gmail_messages(service, query=''):
    """List all Messages of the user's mailbox matching the query."""
    try:
        response = service.users().messages().list(userId='me', q=query).execute()
        messages = []
        if 'messages' in response:
            messages.extend(response['messages'])

        while 'nextPageToken' in response:
            page_token = response['nextPageToken']
            response = service.users().messages().list(userId='me', q=query, pageToken=page_token).execute()
            messages.extend(response['messages'])

        return messages
    except Exception as error:
        print(f'❌ An error occurred listing emails: {error}')
        return []

def delete_gmail_messages(service, message_ids):
    """Batch delete messages by IDs."""
    if not message_ids:
        return True
    try:
        for i in range(0, len(message_ids), 1000):
            batch = message_ids[i:i+1000]
            service.users().messages().batchDelete(
                userId='me',
                body={'ids': batch}
            ).execute()
        return True
    except Exception as error:
        print(f'❌ An error occurred deleting emails: {error}')
        return False

def list_drive_files_in_folder(service, folder_id):
    """Lists all files in a specific Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    try:
        results = service.files().list(q=query, fields='files(id, name, createdTime)').execute()
        return results.get('files', [])
    except Exception as e:
        print(f"❌ List Drive files error: {e}")
        return []

def delete_drive_file(service, file_id):
    """Deletes a file from Drive."""
    try:
        service.files().delete(fileId=file_id).execute()
        return True
    except Exception as e:
        print(f"❌ Delete Drive file error: {e}")
        return False

def get_gmail_message(service, message_id):
    """Retrieves a specific message by ID."""
    try:
        return service.users().messages().get(userId='me', id=message_id).execute()
    except Exception as e:
        print(f"❌ Get Gmail message error: {e}")
        return None

def get_gmail_attachment(service, message_id, attachment_id):
    """Retrieves an attachment by ID."""
    try:
        attachment = service.users().messages().attachments().get(
            userId='me', messageId=message_id, id=attachment_id
        ).execute()
        import base64
        return base64.urlsafe_b64decode(attachment['data'])
    except Exception as e:
        print(f"❌ Get Gmail attachment error: {e}")
        return None
