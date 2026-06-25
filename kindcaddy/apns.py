"""Apple Push Notification service (APNs) helper.

Requires env vars:
  APNS_KEY_ID       — 10-char key ID from Apple Developer portal
  APNS_TEAM_ID      — 10-char team ID
  APNS_PRIVATE_KEY  — contents of the .p8 file (PEM, newlines as \\n)
  APNS_BUNDLE_ID    — e.g. com.kindcaddy.app
  APNS_SANDBOX      — "true" for dev/TestFlight, "false" for App Store (default: false)

If any required var is missing, send_recap_notification() is a silent no-op.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx
import jwt

log = logging.getLogger(__name__)

_KEY_ID = os.environ.get("APNS_KEY_ID", "")
_TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
_PRIVATE_KEY = os.environ.get("APNS_PRIVATE_KEY", "").replace("\\n", "\n")
_BUNDLE_ID = os.environ.get("APNS_BUNDLE_ID", "com.kindcaddy.app")
_SANDBOX = os.environ.get("APNS_SANDBOX", "false").lower() == "true"

_APNS_PROD_HOST = "api.push.apple.com"
_APNS_SANDBOX_HOST = "api.sandbox.push.apple.com"
_APNS_HOST = _APNS_SANDBOX_HOST if _SANDBOX else _APNS_PROD_HOST

# Cache the bearer token (valid 60 min, refresh with 5-min buffer)
_cached_token: Optional[str] = None
_token_issued_at: float = 0.0


def _is_configured() -> bool:
    return bool(_KEY_ID and _TEAM_ID and _PRIVATE_KEY)


def _bearer_token() -> str:
    global _cached_token, _token_issued_at
    now = time.time()
    if _cached_token and now - _token_issued_at < 3300:  # 55-min cache
        return _cached_token
    payload = {"iss": _TEAM_ID, "iat": int(now)}
    _cached_token = jwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": _KEY_ID},
    )
    _token_issued_at = now
    return _cached_token


async def send_recap_notification(
    device_token: str,
    round_id: str,
    summary_text: str,
    score_label: Optional[str] = None,
) -> bool:
    """Send a Caddy Recap push notification. Returns True on success."""
    if not _is_configured():
        log.debug("APNs not configured — skipping push notification")
        return False

    # Truncate recap to first sentence, max 160 chars
    first_sentence = summary_text.split(".")[0].strip()
    body = first_sentence[:157] + "…" if len(first_sentence) > 160 else first_sentence

    subtitle = score_label or "Round complete"
    payload = {
        "aps": {
            "alert": {
                "title": "Caddy Recap",
                "subtitle": subtitle,
                "body": body,
            },
            "sound": "default",
            "badge": 1,
        },
        "round_id": round_id,
    }

    url = f"https://{_APNS_HOST}/3/device/{device_token}"
    headers = {
        "authorization": f"bearer {_bearer_token()}",
        "apns-topic": _BUNDLE_ID,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }

    async with httpx.AsyncClient(http2=True, timeout=10) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except Exception:
            log.warning("APNs send failed", exc_info=True)
            return False

        if resp.status_code == 200:
            return True

        # Production endpoint rejecting a sandbox token — retry on sandbox
        if resp.status_code in (400, 403) and not _SANDBOX:
            log.info("APNs prod rejected token %s…, retrying on sandbox", device_token[:8])
            sandbox_url = f"https://{_APNS_SANDBOX_HOST}/3/device/{device_token}"
            try:
                retry = await client.post(sandbox_url, json=payload, headers=headers)
                if retry.status_code == 200:
                    return True
            except Exception:
                pass

        if resp.status_code == 410:
            log.info("APNs 410: stale token %s…", device_token[:8])
        else:
            log.warning("APNs %s for token %s…: %s", resp.status_code, device_token[:8], resp.text)
        return False
