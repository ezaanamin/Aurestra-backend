# fcm_utils.py — Firebase Admin (FCM) for device push
from __future__ import annotations

import json
import logging
import os

import firebase_admin
from firebase_admin import credentials, messaging

log = logging.getLogger("aurestra.push")

_firebase_init_error: str | None = None
_firebase_cred_source: str | None = None

if not firebase_admin._apps:
    cred = None

    # Priority 1: JSON String from Env (Production / remote server)
    firebase_json = os.getenv("FIREBASE_CREDENTIALS")
    if firebase_json:
        try:
            cred_dict = json.loads(firebase_json)
            cred = credentials.Certificate(cred_dict)
            _firebase_cred_source = "FIREBASE_CREDENTIALS"
            log.info("Firebase initialized via FIREBASE_CREDENTIALS env var")
        except Exception as e:
            _firebase_init_error = f"FIREBASE_CREDENTIALS JSON parse failed: {e}"
            log.error(_firebase_init_error)

    # Priority 2: File Path (Local Development)
    if not cred and os.getenv("FIREBASE_CRED_PATH"):
        cred_path = os.getenv("FIREBASE_CRED_PATH")
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            _firebase_cred_source = "FIREBASE_CRED_PATH"
            log.info("Firebase initialized via file: %s", cred_path)
        else:
            msg = f"FIREBASE_CRED_PATH set but file not found: {cred_path}"
            _firebase_init_error = (_firebase_init_error + "; " if _firebase_init_error else "") + msg
            log.warning(msg)

    if cred:
        firebase_admin.initialize_app(cred)
    else:
        if not _firebase_init_error:
            _firebase_init_error = "No credentials: unset FIREBASE_CREDENTIALS and FIREBASE_CRED_PATH"
        log.warning("Firebase not initialized: %s", _firebase_init_error)


def get_push_service_diagnostics() -> dict:
    """
    Safe snapshot for debugging push on a remote server (no secrets).
    Call from an authenticated debug route.
    """
    initialized = bool(firebase_admin._apps)
    project_id = None
    if initialized:
        try:
            project_id = firebase_admin.get_app().project_id
        except Exception as e:
            log.debug("Could not read project_id: %s", e)

    env_json = os.getenv("FIREBASE_CREDENTIALS")
    cred_path = os.getenv("FIREBASE_CRED_PATH") or ""

    token_count = None
    token_error = None
    try:
        from model import DeviceToken

        token_count = DeviceToken.query.count()
    except Exception as e:
        token_error = str(e)

    tips: list[str] = []
    if not initialized:
        tips.append(
            "Firebase Admin did not start: on the server set FIREBASE_CREDENTIALS (full service-account JSON as one line) "
            "or FIREBASE_CRED_PATH to the JSON file path."
        )
        if env_json:
            tips.append(
                "FIREBASE_CREDENTIALS is non-empty but invalid JSON is common when pasting into .env — "
                "use single-line JSON or base64-wrap; check server logs at startup for parse errors."
            )
        if cred_path and not os.path.exists(cred_path):
            tips.append("FIREBASE_CRED_PATH points to a file that does not exist on this host.")
    if initialized and token_count == 0:
        tips.append(
            "device_tokens is empty: the app must reach THIS host's /api/register-device (check API_BASE_URL / "
            "reverse proxy URL matches the phone build)."
        )
    if initialized and token_count and token_count > 0:
        tips.append(
            "If pushes still never arrive: confirm google-services.json / Firebase project matches this service account's project_id."
        )

    return {
        "firebase_initialized": initialized,
        "firebase_project_id": project_id,
        "credential_source": _firebase_cred_source,
        "init_error_summary": _firebase_init_error,
        "env_firebase_credentials_set": bool(env_json and env_json.strip()),
        "env_firebase_credentials_char_length": len(env_json) if env_json else 0,
        "firebase_cred_path": cred_path[:120] if cred_path else "",
        "firebase_cred_path_exists": bool(cred_path and os.path.exists(cred_path)),
        "device_token_count": token_count,
        "device_token_query_error": token_error,
        "tips": tips,
    }


def send_push_to_all(title, body, tokens=None):
    """
    Send data+notification to each FCM token. Returns a small summary dict for logging/tests.
    """
    summary = {"attempted": 0, "sent": 0, "failed": 0, "errors": []}

    if not firebase_admin._apps:
        log.warning("[push] skipped — Firebase not initialized (%s)", _firebase_init_error or "unknown")
        summary["errors"].append({"phase": "init", "detail": _firebase_init_error or "not_initialized"})
        return summary

    if tokens is None:
        try:
            from model import DeviceToken

            tokens = [t.token for t in DeviceToken.query.all()]
        except Exception as e:
            log.exception("[push] could not load device tokens: %s", e)
            summary["errors"].append({"phase": "db", "detail": str(e)})
            tokens = []

    if not tokens:
        log.warning("[push] no device tokens — nothing sent (title=%r)", title)
        return summary

    for token in tokens:
        summary["attempted"] += 1
        tail = (token[-12:] if len(token) > 12 else token) if token else ""
        try:
            messaging.send(
                messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    token=token,
                )
            )
            summary["sent"] += 1
            log.info("[push] sent ok token_tail=%s title=%r", tail, title)
        except firebase_admin.exceptions.FirebaseError as e:
            summary["failed"] += 1
            code = getattr(e, "error_code", None) or type(e).__name__
            detail = str(e)
            summary["errors"].append({"token_tail": tail, "code": code, "detail": detail})
            log.error("[push] FirebaseError token_tail=%s code=%s detail=%s", tail, code, detail)
            low = detail.lower()
            if "registration-token-not-registered" in low or "invalid-argument" in low:
                try:
                    from model import db, DeviceToken

                    DeviceToken.query.filter_by(token=token).delete()
                    db.session.commit()
                    log.info("[push] removed invalid token from DB token_tail=%s", tail)
                except Exception as db_err:
                    log.warning("[push] failed to remove bad token: %s", db_err)
        except Exception as e:
            summary["failed"] += 1
            summary["errors"].append({"token_tail": tail, "code": type(e).__name__, "detail": str(e)})
            log.exception("[push] unexpected error token_tail=%s", tail)

    log.info(
        "[push] batch done title=%r attempted=%s sent=%s failed=%s",
        title,
        summary["attempted"],
        summary["sent"],
        summary["failed"],
    )
    return summary
