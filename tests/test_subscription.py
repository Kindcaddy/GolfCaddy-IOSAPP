from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("KINDCADDY_JWT_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("APPLE_BUNDLE_ID", "com.kindcaddy.app")
# Subscription gating is disabled by default; these tests exercise the gating path.
os.environ["KINDCADDY_SUBSCRIPTIONS_ENABLED"] = "1"

import kindcaddy.api as api_module
import kindcaddy.db as db_module
from kindcaddy.auth import create_access_token
from kindcaddy.db import init_db, upsert_google_user, upsert_subscription


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "subscription.db"
    log_dir = tmp_path / "logs"
    with patch.object(db_module, "DB_PATH", db_file), patch.object(api_module, "LOG_DIR", log_dir):
        init_db()
        api_module._sessions.clear()
        api_module._session_limiter._calls.clear()
        api_module._advice_limiter._calls.clear()
        api_module._tts_limiter._calls.clear()
        api_module._recap_limiter._calls.clear()
        api_module._transcribe_limiter._calls.clear()
        monkeypatch.setattr(api_module, "generate_pre_round_briefing", lambda **_: None)
        yield TestClient(api_module.app)
        api_module._sessions.clear()


def _create_user_headers(email: str = "subscriber@test.com") -> tuple[str, dict[str, str]]:
    user = upsert_google_user(
        google_sub=f"test-google-{email}",
        email=email,
        display_name="Sub Tester",
    )
    token = create_access_token(user.id)
    return user.id, {"Authorization": f"Bearer {token}"}


def _profile() -> dict:
    return {
        "name": "Sub Tester",
        "handicap": 15.0,
        "shot_shape": "fade",
        "handed": "right",
        "chat_style": "minimal",
        "model_selection": "gpt_wrapper",
        "target_score": 90,
        "clubs": {"7i": {"carry": 155, "total": 165}},
        "tendencies": {},
        "physical": {"gender": "male"},
    }


def _start_round(client: TestClient, headers: dict[str, str]):
    return client.post("/session", json={"profile": _profile()}, headers=headers)


def _finish_latest_active_round(client: TestClient, headers: dict[str, str], status: str = "completed"):
    active = client.get("/rounds/active", headers=headers)
    assert active.status_code == 200
    round_id = active.json()["id"]
    response = client.post(f"/rounds/{round_id}/finish", json={"status": status}, headers=headers)
    assert response.status_code == 200
    return round_id


def test_subscriptions_disabled_grants_unlimited_access(client, monkeypatch):
    """When KINDCADDY_SUBSCRIPTIONS_ENABLED is unset, every user is fully entitled."""
    monkeypatch.delenv("KINDCADDY_SUBSCRIPTIONS_ENABLED", raising=False)
    _, headers = _create_user_headers("disabled@test.com")

    for _ in range(5):
        assert _start_round(client, headers).status_code == 200

    status = client.get("/subscription/status", headers=headers).json()
    assert status["is_subscribed"] is True
    assert status["can_start_round"] is True
    assert status["profile_stats_allowed"] is True
    assert status["subscription_status"] == "disabled"
    assert client.get("/rounds/stats", headers=headers).status_code == 200


def test_email_password_auth_routes_are_not_exposed(client):
    register = client.post(
        "/auth/email/register",
        json={
            "email": "trial-reset@test.com",
            "password": "TestPass123!",
            "display_name": "Trial Reset",
        },
    )
    login = client.post(
        "/auth/email/login",
        json={"email": "trial-reset@test.com", "password": "TestPass123!"},
    )

    assert register.status_code == 404
    assert login.status_code == 404


def test_five_completed_trial_rounds_then_paywall_blocks_start_and_stats(client):
    _, headers = _create_user_headers()

    for expected_completions in range(1, 6):
        response = _start_round(client, headers)
        assert response.status_code == 200
        _finish_latest_active_round(client, headers)
        status = client.get("/subscription/status", headers=headers).json()
        assert status["trial_round_starts"] == expected_completions

    status = client.get("/subscription/status", headers=headers).json()
    assert status["trial_rounds_remaining"] == 0
    assert status["can_start_round"] is False
    assert status["profile_stats_allowed"] is False

    blocked_start = _start_round(client, headers)
    assert blocked_start.status_code == 402
    assert blocked_start.json()["detail"]["code"] == "subscription_required"

    blocked_stats = client.get("/rounds/stats", headers=headers)
    assert blocked_stats.status_code == 402


def test_unfinished_trial_round_does_not_consume_free_round(client):
    _, headers = _create_user_headers("unfinished@test.com")

    response = _start_round(client, headers)
    assert response.status_code == 200

    status = client.get("/subscription/status", headers=headers).json()
    assert status["trial_round_starts"] == 0
    assert status["trial_rounds_remaining"] == 5


def test_abandoned_trial_round_does_not_consume_free_round(client):
    _, headers = _create_user_headers("abandoned@test.com")

    assert _start_round(client, headers).status_code == 200
    _finish_latest_active_round(client, headers, status="abandoned")

    status = client.get("/subscription/status", headers=headers).json()
    assert status["trial_round_starts"] == 0
    assert status["trial_rounds_remaining"] == 5


def test_finish_round_requires_authentication(client):
    _, headers = _create_user_headers("finish-auth@test.com")

    assert _start_round(client, headers).status_code == 200
    active = client.get("/rounds/active", headers=headers)
    assert active.status_code == 200
    round_id = active.json()["id"]

    response = client.post(f"/rounds/{round_id}/finish", json={"status": "completed"})
    assert response.status_code == 401


def test_deleting_completed_trial_round_does_not_refund_trial_usage(client):
    _, headers = _create_user_headers("delete@test.com")

    assert _start_round(client, headers).status_code == 200
    round_id = _finish_latest_active_round(client, headers)
    assert client.delete(f"/rounds/{round_id}", headers=headers).status_code == 200

    status = client.get("/subscription/status", headers=headers).json()
    assert status["trial_round_starts"] == 1
    assert status["trial_rounds_remaining"] == 4


def test_active_subscription_bypasses_exhausted_trial(client):
    user_id, headers = _create_user_headers("active@test.com")
    for _ in range(5):
        assert _start_round(client, headers).status_code == 200
        _finish_latest_active_round(client, headers)

    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    upsert_subscription(
        user_id=user_id,
        product_id="kindcaddy.pro.monthly",
        status="active",
        original_transaction_id="orig-active",
        transaction_id="txn-active",
        environment="Sandbox",
        expires_at=expires_at,
    )

    status = client.get("/subscription/status", headers=headers).json()
    assert status["is_subscribed"] is True
    assert status["can_start_round"] is True
    assert status["profile_stats_allowed"] is True

    assert _start_round(client, headers).status_code == 200
    assert client.get("/rounds/stats", headers=headers).status_code == 200


def test_expired_subscription_does_not_bypass_exhausted_trial(client):
    user_id, headers = _create_user_headers("expired@test.com")
    for _ in range(5):
        assert _start_round(client, headers).status_code == 200
        _finish_latest_active_round(client, headers)

    expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    upsert_subscription(
        user_id=user_id,
        product_id="kindcaddy.pro.monthly",
        status="active",
        original_transaction_id="orig-expired",
        transaction_id="txn-expired",
        environment="Sandbox",
        expires_at=expires_at,
    )

    status = client.get("/subscription/status", headers=headers).json()
    assert status["subscription_status"] == "expired"
    assert status["is_subscribed"] is False
    assert _start_round(client, headers).status_code == 402


def test_storekit_verify_persists_active_entitlement(client, monkeypatch):
    _, headers = _create_user_headers("verify@test.com")
    monkeypatch.setattr(api_module, "_ALLOW_UNVERIFIED_STOREKIT", True)
    expires_ms = int((time.time() + 30 * 24 * 3600) * 1000)
    payload = {
        "bundleId": "com.kindcaddy.app",
        "productId": "kindcaddy.pro.yearly",
        "originalTransactionId": "orig-jws",
        "transactionId": "txn-jws",
        "environment": "Sandbox",
        "purchaseDate": int(time.time() * 1000),
        "expiresDate": expires_ms,
    }
    signed = jwt.encode(payload, key="", algorithm="none")

    response = client.post(
        "/subscription/verify",
        json={"signed_transaction_info": signed},
        headers=headers,
    )

    assert response.status_code == 200
    status = response.json()["status"]
    assert status["is_subscribed"] is True
    assert status["product_id"] == "kindcaddy.pro.yearly"
