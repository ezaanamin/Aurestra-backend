# services/llm_client.py
"""
Centralized LLM client for Aurestra backend.
Manages server-side internal JWT token acquisition from https://auth.elyestra.org
and handles requests to https://llm.elyestra.org/api/generate.
"""

import os
import time
import requests
import jwt

AUTH_API_URL = os.getenv('AUTH_API_URL', 'https://auth.elyestra.org').rstrip('/')
LLM_API_URL = os.getenv('LLM_API_URL', 'https://llm.elyestra.org/api/generate')
INTERNAL_AUTH_SECRET = os.getenv('INTERNAL_AUTH_SECRET') or os.getenv('INTERNAL_AUTH_KEY') or ''
DEFAULT_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:3b')

_TOKEN_CACHE = {
    "token": None,
    "expires_at": 0
}

def get_internal_jwt() -> str:
    """
    Obtains a server-side internal JWT token from https://auth.elyestra.org/auth/internal-token
    using X-Internal-Auth header secret. Caches the token in memory and refreshes it before expiration.
    """
    now = time.time()
    # If token exists and is valid for at least another 60 seconds, reuse it
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["token"]

    secret = (os.getenv('INTERNAL_AUTH_SECRET') or os.getenv('INTERNAL_AUTH_KEY') or '').strip()
    auth_url = os.getenv('AUTH_API_URL', 'https://auth.elyestra.org').rstrip('/')
    token_endpoint = f"{auth_url}/auth/internal-token"

    headers = {}
    if secret:
        headers["X-Internal-Auth"] = secret

    try:
        resp = requests.post(token_endpoint, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token") or data.get("jwt") or data.get("access_token")
            if token:
                # Try decoding exp claim to know exact expiration, fallback to 1 hour
                exp_ts = now + 3600
                try:
                    decoded = jwt.decode(token, options={"verify_signature": False})
                    if "exp" in decoded:
                        exp_ts = float(decoded["exp"])
                except Exception:
                    pass

                _TOKEN_CACHE["token"] = token
                _TOKEN_CACHE["expires_at"] = exp_ts
                print(f"✅ [LLMClient] Acquired internal JWT token (expires in {int(exp_ts - now)}s)")
                return token
            else:
                print(f"⚠️ [LLMClient] Internal token endpoint succeeded but returned no token field: {resp.text}")
        else:
            print(f"⚠️ [LLMClient] Failed to acquire internal JWT token: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ [LLMClient] Exception acquiring internal JWT token: {e}")

    # Fallback to cached token if present even if near expiry
    if _TOKEN_CACHE["token"]:
        return _TOKEN_CACHE["token"]
    return None


def generate_llm(prompt: str, system: str = None, model: str = None, format_json: bool = False, timeout: int = 60, options: dict = None) -> dict:
    """
    Calls https://llm.elyestra.org/api/generate with Bearer token authentication.
    Payload format:
    {
        "model": "qwen2.5:3b",
        "prompt": "<prompt>",
        "stream": false
    }
    Returns raw response JSON dict or {"response": ""} on failure.
    """
    token = get_internal_jwt()
    headers = {
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    llm_url = os.getenv('LLM_API_URL', 'https://llm.elyestra.org/api/generate')
    target_model = model or os.getenv('LLM_MODEL', DEFAULT_MODEL)

    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": False
    }

    if system:
        payload["system"] = system
    if format_json:
        payload["format"] = "json"
    if options:
        payload["options"] = options

    try:
        res = requests.post(llm_url, json=payload, headers=headers, timeout=timeout)
        if res.status_code == 200:
            return res.json()
        elif res.status_code == 401:
            # Token might be expired, clear cache and retry once
            print("⚠️ [LLMClient] Received 401 Unauthorized from LLM service. Clearing token cache and retrying...")
            _TOKEN_CACHE["token"] = None
            _TOKEN_CACHE["expires_at"] = 0
            new_token = get_internal_jwt()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                res_retry = requests.post(llm_url, json=payload, headers=headers, timeout=timeout)
                if res_retry.status_code == 200:
                    return res_retry.json()
                print(f"❌ [LLMClient] Retry failed with status {res_retry.status_code}: {res_retry.text}")
            else:
                print(f"❌ [LLMClient] Failed to acquire new token during retry.")
        else:
            print(f"❌ [LLMClient] LLM request failed status {res.status_code}: {res.text}")
    except Exception as e:
        print(f"❌ [LLMClient] Error connecting to LLM service: {e}")

    return {"response": ""}
