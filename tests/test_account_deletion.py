from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("KINDCADDY_JWT_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("APPLE_BUNDLE_ID", "com.kindcaddy.app")

import kindcaddy.api as api_module
import kindcaddy.db as db_module
from kindcaddy.auth import create_access_token
from kindcaddy.db import (
    create_round,
    init_db,
    save_round_message,
    save_round_score,
    save_round_shot,
    save_user_note,
    upsert_device_token,
    upsert_google_user,
    upsert_subscription,
)


def test_delete_current_user_removes_account_data(tmp_path):
    db_file = tmp_path / "account_delete.db"
    log_dir = tmp_path / "logs"

    with patch.object(db_module, "DB_PATH", db_file), patch.object(api_module, "LOG_DIR", log_dir):
        init_db()
        api_module._sessions.clear()

        user = upsert_google_user(
            google_sub="delete-me-google-sub",
            email="delete-me@test.com",
            display_name="Delete Me",
        )
        round_id = create_round(user.id, "session-delete-me", profile_snapshot={"name": "Delete Me"})
        save_round_score(round_id, hole=1, strokes=4, par=4)
        save_round_shot(round_id, hole=1, club="7i", actual_distance=150)
        save_round_message(round_id, role="user", content="What should I hit?", hole=1)
        save_user_note(user.id, "Aim small")
        upsert_device_token(user.id, "a" * 64)
        upsert_subscription(
            user_id=user.id,
            product_id="kindcaddy.pro.monthly",
            status="active",
            original_transaction_id="original-delete-me",
            transaction_id="transaction-delete-me",
        )

        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        response = TestClient(api_module.app).delete("/auth/me", headers=headers)

        assert response.status_code == 200
        assert response.json()["message"] == "Account deleted"

        with db_module.get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user.id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM rounds WHERE user_id = ?", (user.id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM round_scores WHERE round_id = ?", (round_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM round_shots WHERE round_id = ?", (round_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM round_messages WHERE round_id = ?", (round_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM user_notes WHERE user_id = ?", (user.id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM device_tokens WHERE user_id = ?", (user.id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM user_entitlements WHERE user_id = ?", (user.id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM subscription_events WHERE user_id = ?", (user.id,)).fetchone()[0] == 0
