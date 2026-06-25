"""Authentication utilities: JWT tokens, Apple identity-token verification, password hashing."""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional

import httpx
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

log = logging.getLogger(__name__)

# ── JWT configuration ────────────────────────────────────────────────────────

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 24 * 3600  # 24 hours

_jwt_secret: str = os.environ.get("KINDCADDY_JWT_SECRET", "")

if not _jwt_secret:
    raise RuntimeError(
        "KINDCADDY_JWT_SECRET is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and add it to your .env or systemd environment."
    )


def _ensure_jwt_secret() -> str:
    return _jwt_secret


def create_access_token(user_id: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + JWT_EXPIRY_SECONDS}
    return pyjwt.encode(payload, _ensure_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Return ``user_id`` if *token* is valid, else ``None``."""
    try:
        payload = pyjwt.decode(
            token, _ensure_jwt_secret(), algorithms=[JWT_ALGORITHM]
        )
        return payload.get("sub")
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return None


# ── OIDC identity-token verification (shared) ───────────────────────────────

_JWKS_CACHE_TTL = 3600  # 1 hour

_jwks_caches: dict[str, tuple[dict, float]] = {}


async def _fetch_jwks(url: str) -> Optional[dict]:
    """Fetch (and cache) a provider's public JSON Web Key Set."""
    cached = _jwks_caches.get(url)
    now = time.time()
    if cached and now - cached[1] < _JWKS_CACHE_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            jwks = resp.json()
            _jwks_caches[url] = (jwks, now)
            return jwks
    except Exception:
        return cached[0] if cached else None


async def _verify_oidc_token(
    token: str,
    *,
    jwks_url: str,
    issuer: str | list[str],
    audience: str,
    label: str,
) -> Optional[dict]:
    """Verify an RS256 identity token against a provider's JWKS endpoint.

    Returns decoded claims on success, ``None`` on failure.
    """
    jwks = await _fetch_jwks(jwks_url)
    if not jwks:
        return None

    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.DecodeError:
        return None

    kid = header.get("kid")
    if not kid:
        return None

    matching_key = next(
        (k for k in jwks.get("keys", []) if k.get("kid") == kid), None
    )
    if not matching_key:
        return None

    try:
        public_key = RSAAlgorithm.from_jwk(matching_key)
        options = {}
        if not audience:
            options["verify_aud"] = False

        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience or None,
            options=options,
        )
        return claims
    except (pyjwt.InvalidTokenError, Exception):
        log.debug("%s identity-token verification failed", label, exc_info=True)
        return None


# ── Apple Sign In ────────────────────────────────────────────────────────────

APPLE_BUNDLE_ID = os.environ.get("APPLE_BUNDLE_ID", "")
if not APPLE_BUNDLE_ID:
    raise RuntimeError(
        "APPLE_BUNDLE_ID is not set. "
        "Set it to 'com.kindcaddy.app' in your environment."
    )


async def verify_apple_identity_token(identity_token: str) -> Optional[dict]:
    return await _verify_oidc_token(
        identity_token,
        jwks_url="https://appleid.apple.com/auth/keys",
        issuer="https://appleid.apple.com",
        audience=APPLE_BUNDLE_ID,
        label="Apple",
    )


# ── Google Sign In ───────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")


async def verify_google_identity_token(id_token: str) -> Optional[dict]:
    return await _verify_oidc_token(
        id_token,
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        issuer=["accounts.google.com", "https://accounts.google.com"],
        audience=GOOGLE_CLIENT_ID,
        label="Google",
    )
