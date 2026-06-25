"""KindCaddy REST API -- FastAPI backend for the iOS voice app.

Run:
    OPENAI_API_KEY=sk-... uvicorn kindcaddy.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
import base64
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal, Optional

import jwt as pyjwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from cryptography import x509

from .agent.fatigue_model import FatigueModelTool
from .agent.memory_agent import MemoryAgent
from .agent.score_calculator import ScoreCalculatorTool
from .agent.shot_tracker import ShotRecord, ShotTrackerTool
from .agent.weather_tool import WeatherTool
from .api_models import (
    AdviceRequest,
    AdviceResponse,
    AnalyticsEventRequest,
    AppleAuthRequest,
    AuthResponse,
    AuthUserResponse,
    CalibrationResponse,
    CalibrationSuggestion,
    ClubInsight,
    CommandRequest,
    CommandResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    EditRoundScoreRequest,
    EstimateDistancesRequest,
    EstimateDistancesResponse,
    FinishRoundRequest,
    GoogleAuthRequest,
    MemoryPreferenceRequest,
    MissTendencies,
    RecentRoundStat,
    RecoverSessionRequest,
    RecoverSessionResponse,
    RoundDetailResponse,
    RoundListResponse,
    RoundMessageEntry,
    RoundScoreEntry,
    RoundShotEntry,
    RoundSummaryResponse,
    ScoringDistribution,
    ScoringPatterns,
    SessionStateResponse,
    StatsResponse,
    SubscriptionStatusResponse,
    SubscriptionVerifyRequest,
    SubscriptionVerifyResponse,
    TranscribeResponse,
    DeviceTokenRequest,
    UpdateProfileRequest,
    UserInsightsResponse,
    WeatherUpdateRequest,
    WeatherUpdateResponse,
)
from .auth import (
    create_access_token,
    decode_access_token,
    verify_apple_identity_token,
    verify_google_identity_token,
)
from .caddy import (
    Caddy,
    _summarize_insights,
    generate_pre_round_briefing,
    generate_recap_from_data,
    maybe_distill_user_style,
)
from .distance_estimator import estimate_distances
from .profile import ClubDistance, GolferProfile
from .db import (
    compute_user_insights,
    create_round,
    delete_round,
    delete_user_account,
    delete_user_note,
    finish_round,
    get_entitlement_status,
    get_active_round_for_user,
    get_calibration_suggestions,
    get_last_round_recap,
    get_round_by_id,
    get_round_messages,
    get_round_stats,
    get_rounds_for_user,
    get_style_profile,
    get_user_by_id,
    get_user_insights,
    get_user_notes,
    init_db,
    is_memory_enabled,
    record_trial_round_completed,
    save_round_message,
    save_round_score,
    save_round_shot,
    save_user_note,
    set_memory_enabled,
    update_round_course_name,
    update_round_session_id,
    update_user_display_name,
    upsert_apple_user,
    upsert_google_user,
    upsert_subscription,
    upsert_device_token,
    get_device_tokens,
    delete_device_token,
)
from .apns import send_recap_notification
from .main import parse_weather_input

log = logging.getLogger(__name__)


def _send_recap_push(user_id: str, round_id: str, summary_text: str) -> None:
    """Fire-and-forget APNs push for a completed round recap."""
    import asyncio
    tokens = get_device_tokens(user_id)
    if not tokens:
        return
    rnd = get_round_by_id(round_id)
    score_label = None
    if rnd:
        scores = rnd.get("scores", [])
        if scores:
            total = sum(s["strokes"] for s in scores)
            total_par = sum(s["par"] for s in scores)
            vs = total - total_par
            vs_str = f"{vs:+d}" if vs != 0 else "E"
            score_label = f"{total} ({vs_str}) — {len(scores)} holes"
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    for token in tokens:
        try:
            ok = loop.run_until_complete(
                send_recap_notification(token, round_id, summary_text, score_label)
            )
            if not ok:
                delete_device_token(user_id, token)
        except Exception:
            log.warning("Push notification failed for token %s…", token[:8], exc_info=True)


SESSION_TTL_SECONDS = 4 * 3600  # 4 hours
LOG_DIR = Path(os.environ.get("KINDCADDY_LOG_DIR", "data"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.chmod(0o700)
_log_lock = Lock()

init_db()


@dataclass
class Session:
    caddy: Caddy
    weather_tool: WeatherTool
    shot_tracker: ShotTrackerTool
    score_calc: ScoreCalculatorTool
    fatigue_tool: FatigueModelTool
    user_id: str | None = None
    round_id: str | None = None
    advice_count: int = 0
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, Session] = {}


class _RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self, max_calls: int, period_seconds: float) -> None:
        self._max = max_calls
        self._period = period_seconds
        self._calls: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._calls[key]
            while q and now - q[0] > self._period:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True


_session_limiter = _RateLimiter(10, 60)   # 10 new sessions / min / IP
_advice_limiter = _RateLimiter(60, 60)    # 60 advice requests / min / IP
_tts_limiter = _RateLimiter(30, 60)       # 30 TTS requests / min / IP
_recap_limiter = _RateLimiter(10, 60)     # 10 recap requests / min / IP
_transcribe_limiter = _RateLimiter(30, 60)  # 30 transcribe requests / min / IP

_API_KEY = os.environ.get("KINDCADDY_API_KEY", "")
_SUBSCRIPTION_PRODUCT_IDS = {
    value.strip()
    for value in os.environ.get(
        "KINDCADDY_SUBSCRIPTION_PRODUCTS",
        "kindcaddy.pro.monthly,kindcaddy.pro.yearly",
    ).split(",")
    if value.strip()
}
_IOS_BUNDLE_ID = os.environ.get("KINDCADDY_IOS_BUNDLE_ID", "")
_ALLOW_UNVERIFIED_STOREKIT = os.environ.get("KINDCADDY_ALLOW_UNVERIFIED_STOREKIT", "") == "1"


def _subscriptions_enabled() -> bool:
    """Subscription gating is opt-in. Set KINDCADDY_SUBSCRIPTIONS_ENABLED=1 to re-enable."""
    return os.environ.get("KINDCADDY_SUBSCRIPTIONS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _unlocked_subscription_status() -> SubscriptionStatusResponse:
    """Synthetic 'all access' entitlement returned when subscriptions are disabled."""
    return SubscriptionStatusResponse(
        trial_round_starts=0,
        trial_round_limit=0,
        trial_rounds_remaining=0,
        is_trial_available=False,
        profile_stats_allowed=True,
        can_start_round=True,
        subscription_status="disabled",
        is_subscribed=True,
    )

_PUBLIC_PATHS = frozenset({
    "/auth/apple",
    "/auth/google",
    "/estimate-distances",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _authenticate(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Global auth gate.

    Accepts either ``Authorization: Bearer <jwt>`` (sets user_id) or the
    legacy ``X-API-Key`` header.  Public auth endpoints are exempt.
    """
    if request.url.path in _PUBLIC_PATHS:
        request.state.user_id = None
        return

    if authorization and authorization.startswith("Bearer "):
        user_id = decode_access_token(authorization[7:])
        if user_id:
            request.state.user_id = user_id
            return
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if _API_KEY:
        if x_api_key == _API_KEY:
            request.state.user_id = None
            return
        raise HTTPException(status_code=401, detail="Invalid or missing authentication")

    # No API key configured (dev mode) — allow unauthenticated access
    request.state.user_id = None


app = FastAPI(
    title="KindCaddy API",
    version="0.2.0",
    dependencies=[Depends(_authenticate)],
)

_raw_origins = os.environ.get("KINDCADDY_ALLOWED_ORIGINS", "")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or [
    "http://localhost:8000",
    "https://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


def _get_session(session_id: str) -> Session:
    sess = _sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if time.time() - sess.created_at > SESSION_TTL_SECONDS:
        _sessions.pop(session_id, None)
        raise HTTPException(status_code=410, detail="Session expired")
    return sess


def _purge_expired() -> None:
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now - s.created_at > SESSION_TTL_SECONDS]
    for sid in expired:
        _sessions.pop(sid, None)


def _extract_course_name(user_text: str) -> str | None:
    """Use GPT-4o-mini to extract a course name from user text. Returns None if not found."""
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract the golf course name from the user's message. "
                    "Return ONLY the course name, nothing else. "
                    "If no course name is mentioned, return exactly 'NONE'."
                ),
            },
            {"role": "user", "content": user_text},
        ],
        max_tokens=32,
        temperature=0,
    )
    result = resp.choices[0].message.content.strip()
    return None if result.upper() == "NONE" else result


def _append_advice_log(
    *,
    session_id: str,
    user_text: str,
    response_text: str,
    model: str,
    latency_ms: int,
    caddy: Caddy,
    user_id: str | None = None,
) -> None:
    """Append one advice interaction as JSONL for fine-tuning data collection."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"advice_logs_{datetime.now(timezone.utc):%Y-%m}.jsonl"
        rs = caddy.round_state
        profile = rs.profile
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_id": user_id,
            "model": model,
            "latency_ms": latency_ms,
            "user_input": user_text,
            "assistant_response": response_text,
            "round_context": {
                "hole": rs.current_hole,
                "is_active": rs.is_active,
                "conditions_summary": rs.get_conditions_summary(),
                "round_summary": rs.get_round_state_summary(),
            },
            "profile_context": {
                "name": profile.name if profile else "",
                "handicap": profile.handicap if profile else None,
                "shot_shape": profile.shot_shape if profile else "",
                "chat_style": profile.chat_style if profile else "",
            },
        }
        line = json.dumps(record, ensure_ascii=True)
        with _log_lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        # Logging must never block live advice responses.
        pass


def _append_kpi_event(
    *,
    event_name: str,
    user_id: str | None,
    session_id: str | None = None,
    round_id: str | None = None,
    platform: str = "ios",
    ip: str | None = None,
    properties: dict | None = None,
) -> None:
    """Append one analytics KPI event as JSONL."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"kpi_events_{datetime.now(timezone.utc):%Y-%m}.jsonl"
        event = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_name": event_name,
            "user_id": user_id,
            "session_id": session_id,
            "round_id": round_id,
            "platform": platform,
            "ip": ip,
            "properties": properties or {},
        }
        line = json.dumps(event, ensure_ascii=True)
        with _log_lock:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        # Analytics logging must never block user-facing requests.
        pass


def _build_caddy_for_user(
    *,
    profile: GolferProfile,
    user_id: str | None,
    model_slug: str | None = None,
    max_tokens: int = 1024,
) -> Caddy:
    """Create a caddy configured for public or private model paths."""
    user_insights: dict | None = None
    user_notes: list[dict] = []
    user_style: str | None = None
    if user_id:
        try:
            user_insights = get_user_insights(user_id)
        except Exception:
            log.warning("Failed to load user insights", exc_info=True)
        try:
            user_notes = get_user_notes(user_id)
        except Exception:
            log.warning("Failed to load user notes", exc_info=True)
        try:
            style_row = get_style_profile(user_id)
            if style_row:
                user_style = style_row.get("voice_descriptor") or None
        except Exception:
            log.warning("Failed to load style profile", exc_info=True)

    chosen_model = (model_slug or "").strip()
    if not chosen_model:
        chosen_model = "qwen3.5:4b" if profile.model_selection == "private_model" else "gpt-4o"
    is_private = (
        chosen_model.lower() in {"private-model", "private_model"}
        or "qwen" in chosen_model.lower()
    )

    if is_private:
        if chosen_model.lower() in {"private-model", "private_model"}:
            chosen_model = "qwen3.5:4b"
        private_model = os.environ.get("PRIVATE_MODEL_NAME", chosen_model)
        private_base_url = os.environ.get(
            "PRIVATE_OPENAI_BASE_URL",
            os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
        )
        private_api_key = os.environ.get("PRIVATE_OPENAI_API_KEY", "not-needed")
        caddy = Caddy(
            model=private_model,
            api_key=private_api_key,
            base_url=private_base_url,
            max_tokens=max_tokens,
            user_insights=user_insights,
            user_notes=user_notes,
            user_style=user_style,
        )
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set on server")
        caddy = Caddy(
            model=chosen_model,
            api_key=api_key,
            max_tokens=max_tokens,
            user_insights=user_insights,
            user_notes=user_notes,
            user_style=user_style,
        )

    if user_id:
        caddy.agent.register_tool(MemoryAgent(user_id=user_id))

    caddy.round_state.profile = profile
    caddy.round_state.target_score = profile.target_score
    return caddy


def _hydrate_session_from_round(sess: Session, rnd: dict) -> int:
    """Restore score + shot + hole state from an active round row."""
    profile_snapshot = rnd.get("profile_snapshot") or {}
    profile = GolferProfile(**profile_snapshot)
    sess.caddy.round_state.start_round(profile)
    sess.caddy.round_state.is_active = True

    if rnd.get("pars") and len(rnd["pars"]) == 18:
        sess.score_calc.pars = list(rnd["pars"])

    max_scored_hole = 0
    for score in rnd.get("scores", []):
        hole = int(score["hole"])
        sess.score_calc.log_score(hole, int(score["strokes"]))
        yardage = score.get("yardage")
        if yardage is not None:
            sess.score_calc.log_yardage(hole, int(yardage))
        max_scored_hole = max(max_scored_hole, hole)

    if max_scored_hole > 0:
        sess.caddy.round_state.set_hole(min(max_scored_hole + 1, 18))

    for shot in rnd.get("shots", []):
        sess.shot_tracker.log_shot(
            ShotRecord(
                hole=int(shot["hole"]),
                club=shot["club"],
                actual_distance=shot.get("actual_distance"),
                miss_direction=shot.get("miss_direction"),
                profile_carry=shot.get("profile_carry"),
            )
        )

    return len(rnd.get("scores", []))


def _user_id_from(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


def _subscription_status_response(user_id: str) -> SubscriptionStatusResponse:
    if not _subscriptions_enabled():
        return _unlocked_subscription_status()
    return SubscriptionStatusResponse(**get_entitlement_status(user_id))


def _payment_required(status: SubscriptionStatusResponse) -> None:
    raise HTTPException(
        status_code=402,
        detail={
            "code": "subscription_required",
            "message": "Your free trial has ended. Choose a plan to continue.",
            "subscription": status.model_dump(),
        },
    )


def _require_start_round_access(user_id: str, *, ip: str | None = None) -> SubscriptionStatusResponse:
    status = _subscription_status_response(user_id)
    if not status.can_start_round:
        _append_kpi_event(
            event_name="round_start_blocked_paywall",
            user_id=user_id,
            ip=ip,
            properties={"trial_round_starts": status.trial_round_starts},
        )
        _payment_required(status)
    return status


def _require_profile_stats_access(user_id: str) -> SubscriptionStatusResponse:
    status = _subscription_status_response(user_id)
    if not status.profile_stats_allowed:
        _payment_required(status)
    return status


def _record_trial_round_completed_if_needed(user_id: str | None, round_id: str) -> None:
    if not user_id or not _subscriptions_enabled():
        return
    status = _subscription_status_response(user_id)
    if status.is_subscribed:
        return
    updated = record_trial_round_completed(user_id, round_id)
    _append_kpi_event(
        event_name="trial_round_completed",
        user_id=user_id,
        round_id=round_id,
        properties={
            "trial_rounds_used": str(updated["trial_round_starts"]),
            "trial_round_limit": str(updated["trial_round_limit"]),
        },
    )


def _millis_to_iso(value: object) -> str | None:
    if value is None:
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def _decode_storekit_transaction(signed_transaction_info: str) -> dict:
    """Decode a StoreKit 2 signed transaction JWS.

    Apple signs StoreKit transactions with an x5c certificate chain in the JWS
    header. We verify the JWS signature with the leaf certificate public key and
    then validate bundle/product/expiry before granting entitlement.
    """
    try:
        header = pyjwt.get_unverified_header(signed_transaction_info)
        cert_chain = header.get("x5c") or []
        algorithm = header.get("alg", "ES256")
        if cert_chain:
            cert_data = base64.b64decode(cert_chain[0])
            cert = x509.load_der_x509_certificate(cert_data)
            return pyjwt.decode(
                signed_transaction_info,
                key=cert.public_key(),
                algorithms=[algorithm],
                options={"verify_aud": False},
            )
        if _ALLOW_UNVERIFIED_STOREKIT:
            return pyjwt.decode(
                signed_transaction_info,
                options={"verify_signature": False, "verify_aud": False},
            )
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="Invalid StoreKit transaction") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not decode StoreKit transaction") from exc
    raise HTTPException(status_code=400, detail="StoreKit transaction is missing a signing certificate")


def _subscription_payload_to_status(payload: dict) -> tuple[str, dict]:
    product_id = payload.get("productId")
    if product_id not in _SUBSCRIPTION_PRODUCT_IDS:
        raise HTTPException(status_code=400, detail="Unknown subscription product")

    bundle_id = payload.get("bundleId")
    if _IOS_BUNDLE_ID and bundle_id != _IOS_BUNDLE_ID:
        raise HTTPException(status_code=400, detail="StoreKit transaction bundle mismatch")

    expires_at = _millis_to_iso(payload.get("expiresDate"))
    revoked_at = _millis_to_iso(payload.get("revocationDate"))
    expiry = datetime.fromisoformat(expires_at) if expires_at else None
    is_active = bool(expires_at and expiry and expiry > datetime.now(timezone.utc) and not revoked_at)
    status = "active" if is_active else ("revoked" if revoked_at else "expired")

    return status, {
        "product_id": product_id,
        "original_transaction_id": payload.get("originalTransactionId"),
        "transaction_id": payload.get("transactionId"),
        "environment": payload.get("environment"),
        "purchased_at": _millis_to_iso(payload.get("purchaseDate")),
        "expires_at": expires_at,
        "revoked_at": revoked_at,
    }


# ── Auth endpoints ───────────────────────────────────────────────────────────


def _auth_response(user) -> AuthResponse:
    return AuthResponse(
        access_token=create_access_token(user.id),
        user=_user_response(user),
    )


@app.post("/auth/apple", response_model=AuthResponse)
async def auth_apple(req: AppleAuthRequest) -> AuthResponse:
    """Exchange an Apple Sign In identity token for a KindCaddy access token."""
    claims = await verify_apple_identity_token(req.identity_token)
    if not claims or "sub" not in claims:
        raise HTTPException(status_code=401, detail="Invalid Apple identity token")

    user = upsert_apple_user(
        apple_sub=claims["sub"],
        email=req.email or claims.get("email"),
        display_name=req.display_name,
    )
    return _auth_response(user)


@app.post("/auth/google", response_model=AuthResponse)
async def auth_google(req: GoogleAuthRequest) -> AuthResponse:
    """Exchange a Google Sign-In ID token for a KindCaddy access token."""
    claims = await verify_google_identity_token(req.id_token)
    if not claims or "sub" not in claims:
        raise HTTPException(status_code=401, detail="Invalid Google ID token")

    user = upsert_google_user(
        google_sub=claims["sub"],
        email=req.email or claims.get("email"),
        display_name=req.display_name or claims.get("name"),
    )
    return _auth_response(user)


def _user_response(user) -> AuthUserResponse:
    """Project a UserRow into the public response shape, including the memory
    opt-out flag so the iOS Profile screen can render the toggle correctly."""
    try:
        memory_on = is_memory_enabled(user.id)
    except Exception:
        memory_on = True
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        provider=user.provider,
        memory_enabled=memory_on,
    )


@app.get("/auth/me", response_model=AuthUserResponse)
def auth_me(request: Request) -> AuthUserResponse:
    """Return the currently authenticated user's profile."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(user)


@app.patch("/auth/me", response_model=AuthUserResponse)
def update_profile(req: UpdateProfileRequest, request: Request) -> AuthUserResponse:
    """Update the authenticated user's display name."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = update_user_display_name(uid, req.display_name.strip())
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(user)


@app.patch("/auth/me/memory", response_model=AuthUserResponse)
def update_memory_preference(req: MemoryPreferenceRequest, request: Request) -> AuthUserResponse:
    """Enable or disable the caddy's episodic memory recall for this user.

    When disabled, ``MemoryAgent`` returns no hits and we stop injecting
    "Past Similar Advice" into the system prompt — past chats are still saved
    so the user can re-enable later without losing history.
    """
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    set_memory_enabled(uid, req.memory_enabled)
    return _user_response(user)


@app.delete("/auth/me")
def delete_current_user(request: Request) -> dict:
    """Delete the authenticated user's account and associated stored data."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    deleted = delete_user_account(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    for session_id, session in list(_sessions.items()):
        if session.user_id == uid:
            _sessions.pop(session_id, None)

    _append_kpi_event(
        event_name="account_deleted",
        user_id=None,
        properties={"source": "ios"},
    )
    return {"message": "Account deleted"}


@app.get("/subscription/status", response_model=SubscriptionStatusResponse)
def subscription_status(request: Request) -> SubscriptionStatusResponse:
    """Return the authenticated user's trial and subscription entitlement."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _subscription_status_response(uid)


@app.post("/subscription/verify", response_model=SubscriptionVerifyResponse)
def verify_subscription(
    req: SubscriptionVerifyRequest,
    request: Request,
) -> SubscriptionVerifyResponse:
    """Verify and persist a StoreKit 2 transaction JWS."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not _subscriptions_enabled():
        return SubscriptionVerifyResponse(status=_unlocked_subscription_status())

    payload = _decode_storekit_transaction(req.signed_transaction_info)
    status, fields = _subscription_payload_to_status(payload)
    entitlement = upsert_subscription(
        user_id=uid,
        status=status,
        signed_transaction_info=req.signed_transaction_info,
        payload=payload,
        **fields,
    )
    _append_kpi_event(
        event_name="subscription_verified",
        user_id=uid,
        properties={
            "status": status,
            "product_id": fields["product_id"] or "",
            "environment": fields.get("environment") or "",
        },
    )
    return SubscriptionVerifyResponse(status=SubscriptionStatusResponse(**entitlement))


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.post("/session", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest, request: Request) -> CreateSessionResponse:
    """Create a new caddy session with a golfer profile."""
    ip = request.client.host if request.client else "unknown"
    if not _session_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    _purge_expired()

    user_id = _user_id_from(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_start_round_access(user_id, ip=ip)

    caddy = _build_caddy_for_user(
        profile=req.profile,
        user_id=user_id,
        model_slug=req.model,
        max_tokens=req.max_tokens,
    )

    weather_tool = WeatherTool()
    shot_tracker = ShotTrackerTool()
    score_calc = ScoreCalculatorTool()
    fatigue_tool = FatigueModelTool()

    caddy.agent.register_tool(weather_tool)
    caddy.agent.register_tool(shot_tracker)
    caddy.agent.register_tool(score_calc)
    caddy.agent.register_tool(fatigue_tool)

    session_id = uuid.uuid4().hex

    round_id: str | None = None
    try:
        round_id = create_round(
            user_id=user_id,
            session_id=session_id,
            target_score=req.profile.target_score,
            pars=score_calc.pars,
            profile_snapshot=req.profile.model_dump(),
        )
    except Exception:
        log.warning("Failed to persist round row", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start round")

    memory_tool = caddy.agent.get_tool("memory")
    if memory_tool is not None:
        try:
            memory_tool.set_round_id(round_id)
        except Exception:
            pass

    _sessions[session_id] = Session(
        caddy=caddy,
        weather_tool=weather_tool,
        shot_tracker=shot_tracker,
        score_calc=score_calc,
        fatigue_tool=fatigue_tool,
        user_id=user_id,
        round_id=round_id,
    )
    _append_kpi_event(
        event_name="round_session_created",
        user_id=user_id,
        session_id=session_id,
        round_id=round_id,
        ip=ip,
        properties={"model": caddy.model},
    )

    # Generate pre-round briefing — non-blocking, failures return session without briefing
    briefing: str | None = None
    if user_id:
        try:
            last_recap = get_last_round_recap(user_id) or ""
            insights_summary = _summarize_insights(caddy.user_insights)
            golfer_name = req.profile.name or "there"
            chat_style = req.profile.chat_style or "casual"
            todos_text = "\n".join(f"- {n['note_text']}" for n in caddy.user_notes) if caddy.user_notes else ""
            briefing = generate_pre_round_briefing(
                name=golfer_name,
                insights_summary=insights_summary,
                todos_text=todos_text,
                last_recap=last_recap,
                chat_style=chat_style,
            )
        except Exception:
            log.warning("Failed to generate pre-round briefing", exc_info=True)

    return CreateSessionResponse(session_id=session_id, briefing=briefing)


@app.post("/session/recover", response_model=RecoverSessionResponse)
def recover_session(req: RecoverSessionRequest, request: Request) -> RecoverSessionResponse:
    """Recover a live in-memory session from an active round snapshot."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    target_round: dict | None = None
    if req.round_id:
        target_round = get_round_by_id(req.round_id)
        if not target_round:
            raise HTTPException(status_code=404, detail="Round not found")
        if target_round["user_id"] != uid:
            raise HTTPException(status_code=403, detail="Access denied")
        if target_round["status"] != "active":
            raise HTTPException(status_code=409, detail="Round is already finished")
    else:
        active = get_active_round_for_user(uid)
        if active:
            target_round = get_round_by_id(active["id"])

    if not target_round:
        raise HTTPException(status_code=404, detail="No active round to recover")

    prior_sid = target_round.get("session_id")
    existing = _sessions.get(prior_sid) if prior_sid else None
    if existing:
        if time.time() - existing.created_at <= SESSION_TTL_SECONDS and existing.user_id == uid:
            return RecoverSessionResponse(
                session_id=prior_sid,
                round_id=target_round["id"],
                holes_played=len(target_round.get("scores", [])),
            )
        _sessions.pop(prior_sid, None)

    profile_snapshot = target_round.get("profile_snapshot") or {}
    if not profile_snapshot:
        raise HTTPException(
            status_code=409,
            detail="Active round is missing a profile snapshot and cannot be recovered",
        )
    profile = GolferProfile(**profile_snapshot)
    caddy = _build_caddy_for_user(profile=profile, user_id=uid)

    weather_tool = WeatherTool()
    shot_tracker = ShotTrackerTool()
    score_calc = ScoreCalculatorTool()
    fatigue_tool = FatigueModelTool()

    caddy.agent.register_tool(weather_tool)
    caddy.agent.register_tool(shot_tracker)
    caddy.agent.register_tool(score_calc)
    caddy.agent.register_tool(fatigue_tool)

    sid = uuid.uuid4().hex
    recovered = Session(
        caddy=caddy,
        weather_tool=weather_tool,
        shot_tracker=shot_tracker,
        score_calc=score_calc,
        fatigue_tool=fatigue_tool,
        user_id=uid,
        round_id=target_round["id"],
    )
    memory_tool = caddy.agent.get_tool("memory")
    if memory_tool is not None:
        try:
            memory_tool.set_round_id(target_round["id"])
        except Exception:
            pass
    holes_played = _hydrate_session_from_round(recovered, target_round)
    _sessions[sid] = recovered
    update_round_session_id(target_round["id"], sid)

    ip = request.client.host if request.client else "unknown"
    _append_kpi_event(
        event_name="session_recovered",
        user_id=uid,
        session_id=sid,
        round_id=target_round["id"],
        ip=ip,
        properties={"holes_played": holes_played},
    )

    return RecoverSessionResponse(
        session_id=sid,
        round_id=target_round["id"],
        holes_played=holes_played,
    )


def _build_advice_response(req: AdviceRequest) -> AdviceResponse:
    """Execute advice generation and return the API response payload."""
    sess = _get_session(req.session_id)
    start = time.time()

    alerts = sess.caddy.run_agent_triggers("interaction")
    if alerts:
        sess.caddy.agent._pending_alerts.extend(alerts)

    response_parts = list(sess.caddy.get_advice(req.text))
    response_text = "".join(response_parts)
    latency_ms = int((time.time() - start) * 1000)

    # Persist the chat exchange so the round-detail screen can replay the
    # conversation later. Best-effort: a DB hiccup must not block the reply.
    if sess.round_id:
        current_hole = getattr(sess.caddy.round_state, "current_hole", None)
        user_embedding = getattr(sess.caddy, "last_user_embedding", None)
        embed_model = getattr(sess.caddy, "last_embed_model", None)
        try:
            save_round_message(
                sess.round_id,
                "user",
                req.text,
                hole=current_hole,
                embedding=user_embedding,
                embed_model=embed_model,
            )
            save_round_message(sess.round_id, "assistant", response_text, hole=current_hole)
        except Exception:
            log.warning("Failed to persist round message", exc_info=True)

    sess.advice_count += 1
    if sess.round_id and sess.advice_count <= 2:
        rnd = get_round_by_id(sess.round_id)
        if rnd and not rnd.get("course_name"):
            try:
                course = _extract_course_name(req.text)
                if course:
                    update_round_course_name(sess.round_id, course)
            except Exception:
                log.warning("Course name extraction failed", exc_info=True)

    _append_advice_log(
        session_id=req.session_id,
        user_text=req.text,
        response_text=response_text,
        model=sess.caddy.model,
        latency_ms=latency_ms,
        caddy=sess.caddy,
        user_id=sess.user_id,
    )
    _append_kpi_event(
        event_name="advice_interaction",
        user_id=sess.user_id,
        session_id=req.session_id,
        round_id=sess.round_id,
        properties={"latency_ms": latency_ms},
    )
    return AdviceResponse(text=response_text)


@app.post("/advice", response_model=AdviceResponse)
def get_advice(req: AdviceRequest, request: Request) -> AdviceResponse:
    """Get caddy advice for a shot question."""
    ip = request.client.host if request.client else "unknown"
    if not _advice_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    return _build_advice_response(req)


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    request: Request,
    session_id: str = Form(...),
    audio: UploadFile = File(...),
) -> TranscribeResponse:
    """Transcribe uploaded audio with Whisper, then pipe transcript to /advice logic."""
    ip = request.client.host if request.client else "unknown"
    if not _transcribe_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    if not _advice_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    payload = await audio.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Audio payload is empty")
    if len(payload) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio payload too large")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set on server")

    from openai import OpenAI

    suffix = Path(audio.filename or "input.wav").suffix or ".wav"
    temp_path = Path("/tmp") / f"kindcaddy_transcribe_{uuid.uuid4().hex}{suffix}"
    transcript_text = ""
    try:
        temp_path.write_bytes(payload)
        client = OpenAI(api_key=api_key)
        with temp_path.open("rb") as audio_file:
            transcript_text = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
            ).strip()
    except HTTPException:
        raise
    except Exception:
        log.error("Whisper transcription failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Transcription failed")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not transcript_text:
        raise HTTPException(status_code=422, detail="No speech recognized")

    advice = _build_advice_response(AdviceRequest(session_id=session_id, text=transcript_text))
    return TranscribeResponse(transcript=transcript_text, advice_text=advice.text)


@app.post("/command", response_model=CommandResponse)
def run_command(req: CommandRequest) -> CommandResponse:
    """Execute a round management command."""
    sess = _get_session(req.session_id)
    caddy = sess.caddy
    cmd = req.command.lower().strip()
    args = req.args.strip()

    if cmd == "newround":
        caddy.round_state.start_round(caddy.round_state.profile)
        caddy.agent.reset_for_new_round()
        target = caddy.round_state.profile.target_score if caddy.round_state.profile else "not set"
        return CommandResponse(message=f"New round started. Hole 1. Target: {target}.")

    elif cmd == "hole":
        try:
            hole = int(args)
        except ValueError:
            raise HTTPException(status_code=400, detail="hole requires a number, e.g. '7'")
        if not 1 <= hole <= 18:
            raise HTTPException(status_code=400, detail="hole must be between 1 and 18")
        caddy.round_state.set_hole(hole)
        _trigger_alerts(sess, "hole_change")
        return CommandResponse(message=f"Now on hole {hole}.")

    elif cmd == "weather":
        if not args:
            return CommandResponse(message=caddy.round_state.get_conditions_summary())
        weather_data = parse_weather_input(args)
        sess.weather_tool.set_weather_manual(**weather_data)
        caddy.round_state.update_weather(**weather_data)
        return CommandResponse(message=f"Weather set. {caddy.round_state.get_conditions_summary()}")

    elif cmd == "altitude":
        try:
            alt = float(args)
        except ValueError:
            raise HTTPException(status_code=400, detail="altitude requires a number in feet")
        caddy.round_state.set_altitude(alt)
        return CommandResponse(message=f"Altitude set to {alt:.0f}ft.")

    elif cmd == "score":
        try:
            strokes = int(args)
        except ValueError:
            raise HTTPException(status_code=400, detail="score requires a stroke count")
        # Correct STT errors where score is transcribed with trailing zeros (e.g. 6 → 600)
        while strokes > 15 and strokes % 10 == 0:
            strokes //= 10
        if not 1 <= strokes <= 15:
            raise HTTPException(status_code=400, detail=f"Score {strokes} is out of range (1-15). Check your input.")
        hole = caddy.round_state.current_hole or 1
        if not 1 <= hole <= 18:
            raise HTTPException(status_code=400, detail="Invalid hole number. Start a new round first.")
        sess.score_calc.log_score(hole, strokes)
        par = sess.score_calc.pars[hole - 1]

        if sess.round_id:
            try:
                save_round_score(sess.round_id, hole, strokes, par,
                                 yardage=sess.score_calc.hole_yardages.get(hole))
            except Exception:
                log.warning("Failed to persist score", exc_info=True)

        diff = strokes - par
        label = {-2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double bogey"}.get(
            diff, f"+{diff}" if diff > 0 else str(diff)
        )
        msg = f"Hole {hole}: {strokes} ({label})."
        if hole < 18:
            caddy.round_state.set_hole(hole + 1)
            msg += f" Moving to hole {hole + 1}."
        _trigger_alerts(sess, "score_logged")
        return CommandResponse(message=msg)

    elif cmd == "shot":
        parts = args.split()
        if not parts:
            raise HTTPException(status_code=400, detail="shot requires at least a club, e.g. '7i 150 right'")
        club = parts[0]
        actual_dist = None
        miss_dir = None
        if len(parts) >= 2:
            try:
                actual_dist = float(parts[1])
            except ValueError:
                miss_dir = parts[1]
        if len(parts) >= 3:
            miss_dir = parts[2]

        profile_carry = None
        if caddy.round_state.profile and club in caddy.round_state.profile.clubs:
            profile_carry = caddy.round_state.profile.clubs[club].carry

        current_hole = caddy.round_state.current_hole or 1
        shot = ShotRecord(
            hole=current_hole,
            club=club,
            actual_distance=actual_dist,
            miss_direction=miss_dir,
            profile_carry=profile_carry,
        )
        sess.shot_tracker.log_shot(shot)

        if sess.round_id:
            try:
                save_round_shot(
                    round_id=sess.round_id,
                    hole=current_hole,
                    club=club,
                    actual_distance=actual_dist,
                    miss_direction=miss_dir,
                    profile_carry=profile_carry,
                )
            except Exception:
                log.warning("Failed to persist shot", exc_info=True)

        _trigger_alerts(sess, "shot_logged")
        msg = f"Shot logged: {club}"
        if actual_dist:
            msg += f" - {actual_dist:.0f}yd"
        if miss_dir:
            msg += f" (missed {miss_dir})"
        return CommandResponse(message=msg)

    elif cmd == "scorecard":
        card = sess.score_calc.get_scorecard()
        return CommandResponse(message=card)

    elif cmd == "summary":
        score_data = sess.score_calc.execute({})
        shot_data = sess.shot_tracker.get_round_summary()
        parts = list(caddy.generate_summary(str(score_data), str(shot_data)))
        summary_text = "".join(parts)

        if sess.round_id:
            try:
                weather_summary = caddy.round_state.get_conditions_summary()
                finish_round(
                    sess.round_id,
                    status="completed",
                    summary_text=summary_text,
                    weather_summary=weather_summary,
                )
                _record_trial_round_completed_if_needed(sess.user_id, sess.round_id)
            except Exception:
                log.warning("Failed to finalize round", exc_info=True)
            if sess.user_id:
                try:
                    compute_user_insights(sess.user_id)
                    sess.caddy.user_insights = get_user_insights(sess.user_id)
                except Exception:
                    log.warning("Failed to refresh user insights after summary", exc_info=True)
                try:
                    maybe_distill_user_style(sess.user_id)
                except Exception:
                    log.warning("Failed to refresh style profile after summary", exc_info=True)

        return CommandResponse(message=summary_text)

    elif cmd == "editscore":
        parts = args.split()
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="editscore requires hole and strokes, e.g. '1 5'")
        try:
            edit_hole = int(parts[0])
            edit_strokes = int(parts[1])
        except ValueError:
            raise HTTPException(status_code=400, detail="editscore requires integer hole and strokes")
        while edit_strokes > 15 and edit_strokes % 10 == 0:
            edit_strokes //= 10
        if not 1 <= edit_hole <= 18:
            raise HTTPException(status_code=400, detail="hole must be 1-18")
        if not 1 <= edit_strokes <= 15:
            raise HTTPException(status_code=400, detail=f"strokes {edit_strokes} out of range (1-15)")
        sess.score_calc.log_score(edit_hole, edit_strokes)
        edit_par = sess.score_calc.pars[edit_hole - 1]
        if sess.round_id:
            try:
                save_round_score(sess.round_id, edit_hole, edit_strokes, edit_par,
                                 yardage=sess.score_calc.hole_yardages.get(edit_hole))
            except Exception:
                log.warning("Failed to persist edited score", exc_info=True)
        edit_diff = edit_strokes - edit_par
        edit_label = {-2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double bogey"}.get(
            edit_diff, f"+{edit_diff}" if edit_diff > 0 else str(edit_diff)
        )
        return CommandResponse(message=f"Hole {edit_hole} updated: {edit_strokes} ({edit_label}).")

    elif cmd == "remind":
        if not args:
            raise HTTPException(status_code=400, detail="remind requires a note, e.g. 'remind I tend to pull under pressure'")
        if sess.user_id:
            try:
                save_user_note(sess.user_id, args)
            except Exception:
                log.warning("Failed to save user note", exc_info=True)
        # Also add to the live caddy session so it takes effect immediately
        sess.caddy.user_notes.append({"note_text": args, "note_type": "reminder"})
        return CommandResponse(message=f"Got it — I'll keep that in mind: \"{args}\"")

    elif cmd == "holestats":
        hs_parts = args.split()
        hs_hole = caddy.round_state.current_hole or 1
        if hs_parts:
            try:
                hs_par = int(hs_parts[0])
                sess.score_calc.update_par(hs_hole, hs_par)
            except ValueError:
                pass
        if len(hs_parts) >= 2:
            try:
                hs_yards = int(hs_parts[1])
                sess.score_calc.log_yardage(hs_hole, hs_yards)
            except ValueError:
                pass
        hs_par_now = sess.score_calc.pars[hs_hole - 1]
        hs_yards_now = sess.score_calc.hole_yardages.get(hs_hole)
        hs_msg = f"Hole {hs_hole}: par {hs_par_now}"
        if hs_yards_now:
            hs_msg += f", {hs_yards_now} yards"
        return CommandResponse(message=hs_msg + ".")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {cmd}")


@app.get("/session/{session_id}", response_model=SessionStateResponse)
def get_session_state(session_id: str) -> SessionStateResponse:
    """Get current round state for UI display."""
    sess = _get_session(session_id)
    rs = sess.caddy.round_state
    live_scores = [
        RoundScoreEntry(
            hole=h,
            strokes=s,
            par=sess.score_calc.pars[h - 1],
            yardage=sess.score_calc.hole_yardages.get(h),
        )
        for h, s in sorted(sess.score_calc.scores.items())
    ]
    return SessionStateResponse(
        session_id=session_id,
        round_id=sess.round_id,
        hole=rs.current_hole,
        is_active=rs.is_active,
        conditions=rs.get_conditions_summary(),
        round_summary=rs.get_round_state_summary(),
        scores=live_scores,
        pars=sess.score_calc.pars,
        yardages={h: y for h, y in sess.score_calc.hole_yardages.items()},
    )


@app.post("/weather/update", response_model=WeatherUpdateResponse)
async def update_weather(req: WeatherUpdateRequest) -> WeatherUpdateResponse:
    """Accept weather data directly (WeatherKit) or fall back to Open-Meteo via GPS."""
    sess = _get_session(req.session_id)

    if req.temp_f is not None:
        temp_f = req.temp_f
        wind_speed = req.wind_speed_mph or 0.0
        wind_deg = req.wind_deg or 0.0
        wind_gust = req.wind_gust_mph or 0.0
        humidity = req.humidity or 50
        description = req.description or "clear"

        sess.weather_tool.set_weather_manual(
            temp_f=temp_f,
            wind_speed_mph=wind_speed,
            wind_deg=wind_deg,
            wind_gust_mph=wind_gust,
            humidity=humidity,
            description=description,
        )
        snapshot = sess.weather_tool.current
    else:
        sess.weather_tool.set_location(req.lat, req.lon)
        snapshot = await sess.weather_tool.fetch_weather()

    if not snapshot:
        raise HTTPException(status_code=503, detail="Weather data unavailable.")

    sess.caddy.round_state.update_weather(
        temp_f=snapshot.temp_f,
        wind_speed_mph=snapshot.wind_speed_mph,
        wind_deg=snapshot.wind_deg,
        wind_gust_mph=snapshot.wind_gust_mph,
        humidity=snapshot.humidity,
        description=snapshot.description,
    )

    return WeatherUpdateResponse(
        temp_f=snapshot.temp_f,
        wind_speed_mph=snapshot.wind_speed_mph,
        wind_deg=snapshot.wind_deg,
        wind_gust_mph=snapshot.wind_gust_mph,
        humidity=snapshot.humidity,
        description=snapshot.description,
        summary=snapshot.summary(),
    )


# ── Round history & stats ────────────────────────────────────────────────────


@app.get("/rounds", response_model=RoundListResponse)
def list_rounds(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
) -> RoundListResponse:
    """List the authenticated user's rounds, most recent first."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)
    rows = get_rounds_for_user(uid, limit=limit, offset=offset, status=status)
    summaries = [RoundSummaryResponse(**_round_row_to_summary(r)) for r in rows]
    return RoundListResponse(rounds=summaries, total=len(summaries))


@app.get("/rounds/stats", response_model=StatsResponse)
def round_stats(request: Request) -> StatsResponse:
    """Aggregate scoring statistics for the authenticated user."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)
    data = get_round_stats(uid)
    if data.get("total_rounds", 0) == 0:
        return StatsResponse()
    return StatsResponse(
        total_rounds=data["total_rounds"],
        total_holes=data["total_holes"],
        avg_score_vs_par=data["avg_score_vs_par"],
        best_score_vs_par=data.get("best_score_vs_par"),
        worst_score_vs_par=data.get("worst_score_vs_par"),
        scoring_distribution=ScoringDistribution(**data.get("scoring_distribution", {})),
        miss_tendencies=MissTendencies(**data.get("miss_tendencies", {})),
        recent_rounds=[RecentRoundStat(**r) for r in data.get("recent_rounds", [])],
    )


@app.get("/rounds/active", response_model=RoundSummaryResponse)
def get_active_round_endpoint(request: Request) -> RoundSummaryResponse:
    """Return the authenticated user's currently in-progress round, or 404 if none.

    Used by the iOS Home screen to surface a "Continue Round" affordance after
    an accidental app exit, app kill, or cross-device launch.
    """
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    rnd = get_active_round_for_user(uid)
    if not rnd:
        raise HTTPException(status_code=404, detail="No active round")
    return RoundSummaryResponse(**_round_row_to_summary(rnd))


@app.get("/rounds/{round_id}", response_model=RoundDetailResponse)
def get_round_detail(round_id: str, request: Request) -> RoundDetailResponse:
    """Get the full detail of a specific round (scorecard + shots)."""
    uid = _user_id_from(request)
    if uid:
        _require_profile_stats_access(uid)
    rnd = get_round_by_id(round_id)
    if not rnd:
        raise HTTPException(status_code=404, detail="Round not found")
    if uid and rnd["user_id"] != uid:
        raise HTTPException(status_code=403, detail="Access denied")
    messages = [RoundMessageEntry(**m) for m in get_round_messages(round_id)]
    return RoundDetailResponse(
        **_round_row_to_summary(rnd),
        pars=rnd.get("pars"),
        scores=[RoundScoreEntry(**s) for s in rnd.get("scores", [])],
        shots=[RoundShotEntry(**s) for s in rnd.get("shots", [])],
        messages=messages,
    )


@app.delete("/rounds/{round_id}")
def delete_round_endpoint(round_id: str, request: Request):
    """Delete a round and all its associated shots and scores."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_profile_stats_access(uid)
    deleted = delete_round(round_id, uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Round not found")
    return {"message": "Round deleted"}


@app.patch("/rounds/{round_id}/scores/{hole}")
def edit_round_score_endpoint(round_id: str, hole: int, req: EditRoundScoreRequest, request: Request):
    """Edit a single hole score on a historical (finished) round."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_profile_stats_access(uid)
    rnd = get_round_by_id(round_id)
    if not rnd:
        raise HTTPException(status_code=404, detail="Round not found")
    if rnd["user_id"] != uid:
        raise HTTPException(status_code=403, detail="Access denied")
    if not 1 <= hole <= 18:
        raise HTTPException(status_code=400, detail="Hole must be 1-18")

    # Find the existing score entry to get the par for this hole
    existing_scores = rnd.get("scores", [])
    score_entry = next((s for s in existing_scores if s["hole"] == hole), None)
    if not score_entry:
        raise HTTPException(status_code=404, detail=f"No score recorded for hole {hole}")

    par = score_entry["par"]
    yardage = score_entry.get("yardage")
    save_round_score(round_id, hole, req.strokes, par, yardage=yardage)

    diff = req.strokes - par
    label = {-2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double bogey"}.get(
        diff, f"+{diff}" if diff > 0 else str(diff)
    )
    return {"message": f"Hole {hole} updated: {req.strokes} ({label})"}


@app.post("/device-token", status_code=204)
def register_device_token(req: DeviceTokenRequest, request: Request):
    """Register an APNs device token for push notifications."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    upsert_device_token(uid, req.device_token, req.platform)


@app.post("/events", status_code=204)
def ingest_event(req: AnalyticsEventRequest, request: Request):
    """Ingest client-side KPI events for product funnel analytics."""
    uid = _user_id_from(request)
    ip = request.client.host if request.client else "unknown"
    _append_kpi_event(
        event_name=req.event_name,
        user_id=uid,
        session_id=req.session_id,
        round_id=req.round_id,
        platform=req.platform,
        ip=ip,
        properties=req.properties,
    )


@app.post("/rounds/{round_id}/finish")
def finish_round_endpoint(round_id: str, req: FinishRoundRequest, request: Request):
    """Manually finish a round (mark as completed or abandoned)."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    rnd = get_round_by_id(round_id)
    if not rnd:
        raise HTTPException(status_code=404, detail="Round not found")
    if rnd["user_id"] != uid:
        raise HTTPException(status_code=403, detail="Access denied")
    if rnd["status"] != "active":
        raise HTTPException(status_code=409, detail="Round is already finished")

    summary_text: str | None = None
    if req.status == "completed" and rnd.get("scores") and not rnd.get("summary_text"):
        try:
            profile_snapshot = rnd.get("profile_snapshot") or {}
            chat_style = profile_snapshot.get("chat_style", "casual")
            summary_text = generate_recap_from_data(
                profile_snapshot=profile_snapshot,
                scores=rnd["scores"],
                shots=rnd.get("shots", []),
                chat_style=chat_style,
            )
        except Exception:
            log.warning("Failed to auto-generate recap on finish", exc_info=True)

    finish_round(round_id, status=req.status, summary_text=summary_text)
    if req.status == "completed":
        _record_trial_round_completed_if_needed(uid, round_id)

    if uid and req.status == "completed":
        try:
            compute_user_insights(uid)
        except Exception:
            log.warning("Failed to refresh user insights after finish", exc_info=True)
        try:
            maybe_distill_user_style(uid)
        except Exception:
            log.warning("Failed to refresh style profile after finish", exc_info=True)

        if summary_text:
            _send_recap_push(uid, round_id, summary_text)

    return {"message": f"Round marked as {req.status}"}


@app.post("/rounds/{round_id}/recap")
def generate_recap_endpoint(round_id: str, request: Request):
    """Generate (or regenerate) a post-round recap for a completed round."""
    ip = request.client.host if request.client else "unknown"
    if not _recap_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    rnd = get_round_by_id(round_id)
    if not rnd:
        raise HTTPException(status_code=404, detail="Round not found")
    if rnd["user_id"] != uid:
        raise HTTPException(status_code=403, detail="Access denied")
    if rnd["status"] != "completed":
        raise HTTPException(status_code=409, detail="Round is not completed")

    profile_snapshot = rnd.get("profile_snapshot") or {}
    chat_style = profile_snapshot.get("chat_style", "casual")
    try:
        recap_text = generate_recap_from_data(
            profile_snapshot=profile_snapshot,
            scores=rnd.get("scores", []),
            shots=rnd.get("shots", []),
            chat_style=chat_style,
        )
    except Exception:
        log.error("Recap generation failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Recap generation failed")

    finish_round(
        round_id,
        status="completed",
        summary_text=recap_text,
        weather_summary=rnd.get("weather_summary"),
    )
    try:
        compute_user_insights(uid)
    except Exception:
        log.warning("Failed to refresh user insights after recap", exc_info=True)
    try:
        maybe_distill_user_style(uid)
    except Exception:
        log.warning("Failed to refresh style profile after recap", exc_info=True)

    return {"message": recap_text}


@app.get("/insights", response_model=UserInsightsResponse)
def get_insights_endpoint(request: Request) -> UserInsightsResponse:
    """Return the authenticated user's computed golfer memory profile."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)

    raw = get_user_insights(uid)
    if not raw or raw.get("rounds_analyzed", 0) == 0:
        return UserInsightsResponse()

    club_insights = [
        ClubInsight(
            club=club,
            avg_carry=data["avg_carry"],
            profile_carry=data.get("profile_carry"),
            delta=data.get("delta"),
            shot_count=data["shot_count"],
            dominant_miss=data.get("dominant_miss"),
        )
        for club, data in raw.get("club_actuals", {}).items()
    ]

    sp = raw.get("scoring_patterns") or {}
    scoring_patterns = ScoringPatterns(
        par3_avg=sp.get("par3_avg"),
        par4_avg=sp.get("par4_avg"),
        par5_avg=sp.get("par5_avg"),
        front9_avg=sp.get("front9_avg"),
        back9_avg=sp.get("back9_avg"),
    ) if any(v is not None for v in sp.values()) else None

    mt = raw.get("miss_tendencies") or {}
    miss_tendencies = MissTendencies(
        left=mt.get("left", 0),
        right=mt.get("right", 0),
        short=mt.get("short", 0),
        long=mt.get("long", 0),
    )

    return UserInsightsResponse(
        club_insights=club_insights,
        scoring_patterns=scoring_patterns,
        miss_tendencies=miss_tendencies,
        fatigue_yards_lost=raw.get("fatigue_yards_lost"),
        pressure_scoring_delta=raw.get("pressure_scoring_delta"),
        improvement_trend=raw.get("improvement_trend"),
        rounds_analyzed=raw.get("rounds_analyzed", 0),
        updated_at=raw.get("updated_at"),
    )


@app.get("/notes")
def get_notes(request: Request) -> dict:
    """Return the authenticated user's active reminders and notes."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)
    notes = get_user_notes(uid)
    return {"notes": notes}


@app.delete("/notes/{note_id}")
def remove_note(note_id: int, request: Request) -> dict:
    """Soft-delete a note by id."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)
    deleted = delete_user_note(uid, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"message": "Note removed."}


@app.get("/calibration", response_model=CalibrationResponse)
def get_calibration_endpoint(request: Request) -> CalibrationResponse:
    """Return club calibration suggestions for the authenticated user."""
    uid = _user_id_from(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_profile_stats_access(uid)
    suggestions = get_calibration_suggestions(uid)
    return CalibrationResponse(
        suggestions=[CalibrationSuggestion(**s) for s in suggestions]
    )


@app.post("/estimate-distances", response_model=EstimateDistancesResponse)
def estimate_distances_endpoint(req: EstimateDistancesRequest) -> EstimateDistancesResponse:
    """Estimate club distances from handicap and optional swing speed."""
    raw = estimate_distances(
        handicap=req.handicap,
        driver_speed_mph=req.driver_speed_mph,
        gender=req.gender,
    )
    clubs = {name: ClubDistance(carry=d["carry"], total=d["total"]) for name, d in raw.items()}
    return EstimateDistancesResponse(clubs=clubs)


def _round_row_to_summary(rnd: dict) -> dict:
    """Convert a raw round dict to the fields needed for RoundSummaryResponse."""
    return {
        "id": rnd["id"],
        "status": rnd["status"],
        "course_name": rnd.get("course_name"),
        "started_at": rnd["started_at"],
        "finished_at": rnd.get("finished_at"),
        "target_score": rnd.get("target_score"),
        "total_strokes": rnd.get("total_strokes", 0),
        "total_par": rnd.get("total_par", 0),
        "score_vs_par": rnd.get("score_vs_par"),
        "holes_played": rnd.get("holes_played", 0),
        "weather_summary": rnd.get("weather_summary"),
        "summary_text": rnd.get("summary_text"),
    }


# ── TTS ──────────────────────────────────────────────────────────────────────


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    voice: Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"] = "nova"


@app.post("/tts")
async def text_to_speech(req: TTSRequest, request: Request) -> Response:
    """Convert text to speech using OpenAI TTS. Returns MP3 audio bytes."""
    ip = request.client.host if request.client else "unknown"
    if not _tts_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set on server")

    import httpx
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{openai_base_url}/audio/speech",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "tts-1", "voice": req.voice, "input": req.text},
            timeout=30,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="TTS generation failed")

    return Response(content=resp.content, media_type="audio/mpeg")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _trigger_alerts(sess: Session, trigger_type: str) -> None:
    """Run agent triggers; any alerts will be woven into the next advice call."""
    alerts = sess.caddy.run_agent_triggers(trigger_type)
    if alerts:
        sess.caddy.agent._pending_alerts.extend(alerts)
