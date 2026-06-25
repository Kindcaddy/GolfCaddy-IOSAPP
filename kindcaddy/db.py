"""SQLite database layer for KindCaddy user accounts and round history."""

import json
import math
import os
import sqlite3
import struct
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("KINDCADDY_DB_PATH", "data/kindcaddy.db"))
TRIAL_ROUND_LIMIT = 5


@contextmanager
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist, and run lightweight migrations."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                apple_sub TEXT UNIQUE,
                google_sub TEXT UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT,
                display_name TEXT,
                provider TEXT NOT NULL DEFAULT 'email',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_apple_sub ON users(apple_sub);
            CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

            CREATE TABLE IF NOT EXISTS rounds (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                course_name TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                target_score INTEGER,
                pars TEXT,
                profile_snapshot TEXT,
                weather_summary TEXT,
                summary_text TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_rounds_user_id ON rounds(user_id);
            CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds(status);

            CREATE TABLE IF NOT EXISTS round_scores (
                round_id TEXT NOT NULL,
                hole INTEGER NOT NULL,
                strokes INTEGER NOT NULL,
                par INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (round_id, hole),
                FOREIGN KEY (round_id) REFERENCES rounds(id)
            );

            CREATE TABLE IF NOT EXISTS round_shots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id TEXT NOT NULL,
                hole INTEGER NOT NULL,
                club TEXT NOT NULL,
                intended_distance REAL,
                actual_distance REAL,
                miss_direction TEXT,
                lie TEXT DEFAULT 'fairway',
                notes TEXT,
                profile_carry REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id)
            );
            CREATE INDEX IF NOT EXISTS idx_round_shots_round_id ON round_shots(round_id);

            CREATE TABLE IF NOT EXISTS round_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                hole INTEGER,
                created_at TEXT NOT NULL,
                embedding BLOB,
                embed_model TEXT,
                FOREIGN KEY (round_id) REFERENCES rounds(id)
            );
            CREATE INDEX IF NOT EXISTS idx_round_messages_round_id ON round_messages(round_id);

            CREATE TABLE IF NOT EXISTS user_style_profile (
                user_id TEXT PRIMARY KEY,
                voice_descriptor TEXT NOT NULL,
                rounds_at_distill INTEGER NOT NULL DEFAULT 0,
                distilled_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_insights (
                user_id TEXT PRIMARY KEY,
                insights_json TEXT NOT NULL,
                rounds_analyzed INTEGER NOT NULL DEFAULT 0,
                last_round_id TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS user_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                note_text TEXT NOT NULL,
                note_type TEXT NOT NULL DEFAULT 'reminder',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_user_notes_user_id ON user_notes(user_id);

            CREATE TABLE IF NOT EXISTS device_tokens (
                user_id TEXT NOT NULL,
                device_token TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'ios',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, device_token),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_device_tokens_user_id ON device_tokens(user_id);

            CREATE TABLE IF NOT EXISTS user_entitlements (
                user_id TEXT PRIMARY KEY,
                trial_round_starts INTEGER NOT NULL DEFAULT 0,
                trial_limit INTEGER NOT NULL DEFAULT 5,
                subscription_status TEXT NOT NULL DEFAULT 'none',
                product_id TEXT,
                original_transaction_id TEXT,
                latest_transaction_id TEXT,
                environment TEXT,
                purchased_at TEXT,
                expires_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS trial_round_starts (
                user_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, round_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (round_id) REFERENCES rounds(id)
            );
            CREATE INDEX IF NOT EXISTS idx_trial_round_starts_user_id ON trial_round_starts(user_id);

            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                original_transaction_id TEXT,
                transaction_id TEXT,
                environment TEXT,
                status TEXT NOT NULL,
                signed_transaction_info TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_subscription_events_user_id ON subscription_events(user_id);

            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
        """)

        # Migration: add google_sub column to existing databases
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "google_sub" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT UNIQUE")

        score_cols = {row[1] for row in conn.execute("PRAGMA table_info(round_scores)").fetchall()}
        if "yardage" not in score_cols:
            conn.execute("ALTER TABLE round_scores ADD COLUMN yardage INTEGER")

        entitlement_cols = {row[1] for row in conn.execute("PRAGMA table_info(user_entitlements)").fetchall()}
        for column, ddl in {
            "latest_transaction_id": "ALTER TABLE user_entitlements ADD COLUMN latest_transaction_id TEXT",
            "purchased_at": "ALTER TABLE user_entitlements ADD COLUMN purchased_at TEXT",
            "revoked_at": "ALTER TABLE user_entitlements ADD COLUMN revoked_at TEXT",
        }.items():
            if column not in entitlement_cols:
                conn.execute(ddl)

        # Trial usage used to be counted on round start. The paywall now counts
        # only rounds finished as completed, and the allowance is five rounds.
        trial_completion_migration = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            ("trial_completed_rounds_v1",),
        ).fetchone()
        if not trial_completion_migration:
            conn.execute(
                "DELETE FROM trial_round_starts "
                "WHERE round_id NOT IN (SELECT id FROM rounds WHERE status = 'completed')"
            )
            conn.execute(
                "UPDATE user_entitlements "
                "SET trial_round_starts = ("
                "  SELECT COUNT(*) FROM trial_round_starts "
                "  WHERE trial_round_starts.user_id = user_entitlements.user_id"
                ")"
            )
            conn.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                ("trial_completed_rounds_v1", _now_iso()),
            )
        conn.execute(
            "UPDATE user_entitlements SET trial_limit = ? WHERE trial_limit < ?",
            (TRIAL_ROUND_LIMIT, TRIAL_ROUND_LIMIT),
        )

        message_cols = {row[1] for row in conn.execute("PRAGMA table_info(round_messages)").fetchall()}
        if "embedding" not in message_cols:
            conn.execute("ALTER TABLE round_messages ADD COLUMN embedding BLOB")
        if "embed_model" not in message_cols:
            conn.execute("ALTER TABLE round_messages ADD COLUMN embed_model TEXT")

        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "memory_enabled" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1")


# ── User CRUD ────────────────────────────────────────────────────────────────


class UserRow:
    __slots__ = ("id", "apple_sub", "google_sub", "email", "password_hash",
                 "display_name", "provider", "created_at", "updated_at")

    def __init__(self, row: sqlite3.Row):
        self.id: str = row["id"]
        self.apple_sub: Optional[str] = row["apple_sub"]
        self.google_sub: Optional[str] = row["google_sub"]
        self.email: Optional[str] = row["email"]
        self.password_hash: Optional[str] = row["password_hash"]
        self.display_name: Optional[str] = row["display_name"]
        self.provider: str = row["provider"]
        self.created_at: str = row["created_at"]
        self.updated_at: str = row["updated_at"]


def upsert_apple_user(
    apple_sub: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> UserRow:
    """Find or create a user by Apple's stable ``sub`` identifier.

    On repeat sign-ins Apple may re-send email/name — we update if provided.
    If a user with the same email already exists (e.g. from Google or email
    sign-up), link the Apple sub to that existing account.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE apple_sub = ?", (apple_sub,)
        ).fetchone()

        if not row and email:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

        if row:
            updates, params = [], []
            if not row["apple_sub"]:
                updates.append("apple_sub = ?")
                params.append(apple_sub)
            if email:
                updates.append("email = ?")
                params.append(email)
            if display_name:
                updates.append("display_name = ?")
                params.append(display_name)
            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(row["id"])
                conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (row["id"],)
                ).fetchone()
            return UserRow(row)

        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users (id, apple_sub, email, display_name, provider, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'apple', ?, ?)",
            (user_id, apple_sub, email, display_name, now, now),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return UserRow(row)


def upsert_google_user(
    google_sub: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> UserRow:
    """Find or create a user by Google's stable ``sub`` identifier.

    If a user with the same email already exists (e.g. from Apple or email
    sign-up), link the Google sub to that existing account.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()

        if not row and email:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

        if row:
            updates, params = [], []
            if not row["google_sub"]:
                updates.append("google_sub = ?")
                params.append(google_sub)
            if email:
                updates.append("email = ?")
                params.append(email)
            if display_name:
                updates.append("display_name = ?")
                params.append(display_name)
            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(row["id"])
                conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (row["id"],)
                ).fetchone()
            return UserRow(row)

        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users (id, google_sub, email, display_name, provider, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'google', ?, ?)",
            (user_id, google_sub, email, display_name, now, now),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return UserRow(row)


def get_user_by_id(user_id: str) -> Optional[UserRow]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return UserRow(row) if row else None


def update_user_display_name(user_id: str, display_name: str) -> Optional[UserRow]:
    """Update a user's display name. Returns the updated UserRow or None if not found."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
            (display_name, now, user_id),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return UserRow(row) if row else None


def delete_user_account(user_id: str) -> bool:
    """Delete a user and all database rows that directly identify them."""
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False

        round_rows = conn.execute(
            "SELECT id FROM rounds WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        round_ids = [row["id"] for row in round_rows]

        if round_ids:
            placeholders = ",".join("?" for _ in round_ids)
            conn.execute(f"DELETE FROM round_messages WHERE round_id IN ({placeholders})", round_ids)
            conn.execute(f"DELETE FROM round_scores WHERE round_id IN ({placeholders})", round_ids)
            conn.execute(f"DELETE FROM round_shots WHERE round_id IN ({placeholders})", round_ids)

        conn.execute("DELETE FROM trial_round_starts WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM subscription_events WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_entitlements WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM device_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_notes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_insights WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_style_profile WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM rounds WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True


# ── Entitlements / subscriptions ─────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ensure_entitlement_row(conn: sqlite3.Connection, user_id: str) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO user_entitlements "
        "(user_id, trial_round_starts, trial_limit, subscription_status, updated_at) "
        "VALUES (?, 0, ?, 'none', ?)",
        (user_id, TRIAL_ROUND_LIMIT, now),
    )


def _entitlement_status_from_row(row: sqlite3.Row) -> dict:
    trial_limit = int(row["trial_limit"] or TRIAL_ROUND_LIMIT)
    trial_starts = int(row["trial_round_starts"] or 0)
    expires_at = row["expires_at"]
    revoked_at = row["revoked_at"]
    raw_status = row["subscription_status"] or "none"
    expiry = _parse_iso(expires_at)
    now = datetime.now(timezone.utc)

    is_subscribed = raw_status == "active" and not revoked_at and bool(expiry and expiry > now)
    subscription_status = raw_status
    if raw_status == "active" and not is_subscribed:
        subscription_status = "revoked" if revoked_at else "expired"

    trial_remaining = max(0, trial_limit - trial_starts)
    is_trial_available = trial_remaining > 0
    can_start_round = is_subscribed or is_trial_available
    profile_stats_allowed = is_subscribed or trial_starts < trial_limit

    return {
        "trial_round_starts": trial_starts,
        "trial_round_limit": trial_limit,
        "trial_rounds_remaining": trial_remaining,
        "is_trial_available": is_trial_available,
        "profile_stats_allowed": profile_stats_allowed,
        "can_start_round": can_start_round,
        "subscription_status": subscription_status,
        "is_subscribed": is_subscribed,
        "product_id": row["product_id"],
        "expires_at": expires_at,
        "environment": row["environment"],
    }


def get_entitlement_status(user_id: str) -> dict:
    """Return trial/subscription state for a user, creating defaults if needed."""
    with get_db() as conn:
        _ensure_entitlement_row(conn, user_id)
        row = conn.execute(
            "SELECT * FROM user_entitlements WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        stored_trial_count = int(row["trial_round_starts"] or 0)
        counted_trial_count = conn.execute(
            "SELECT COUNT(*) AS c FROM trial_round_starts WHERE user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        trial_count = max(stored_trial_count, int(counted_trial_count or 0))
        if trial_count != stored_trial_count:
            conn.execute(
                "UPDATE user_entitlements SET trial_round_starts = ?, updated_at = ? WHERE user_id = ?",
                (trial_count, _now_iso(), user_id),
            )
            row = conn.execute(
                "SELECT * FROM user_entitlements WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return _entitlement_status_from_row(row)


def record_trial_round_completed(user_id: str, round_id: str) -> dict:
    """Count a completed round against the free trial, idempotently by round id."""
    now = _now_iso()
    with get_db() as conn:
        _ensure_entitlement_row(conn, user_id)
        conn.execute(
            "INSERT OR IGNORE INTO trial_round_starts (user_id, round_id, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, round_id, now),
        )
        trial_count = conn.execute(
            "SELECT COUNT(*) AS c FROM trial_round_starts WHERE user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        conn.execute(
            "UPDATE user_entitlements SET trial_round_starts = ?, updated_at = ? WHERE user_id = ?",
            (trial_count, now, user_id),
        )
        row = conn.execute(
            "SELECT * FROM user_entitlements WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return _entitlement_status_from_row(row)


def upsert_subscription(
    *,
    user_id: str,
    product_id: str,
    status: str,
    original_transaction_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
    environment: Optional[str] = None,
    purchased_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    revoked_at: Optional[str] = None,
    signed_transaction_info: Optional[str] = None,
    payload: Optional[dict] = None,
) -> dict:
    """Store the latest App Store entitlement and keep an audit event."""
    now = _now_iso()
    with get_db() as conn:
        _ensure_entitlement_row(conn, user_id)
        conn.execute(
            "UPDATE user_entitlements SET "
            "subscription_status = ?, product_id = ?, original_transaction_id = ?, "
            "latest_transaction_id = ?, environment = ?, purchased_at = ?, "
            "expires_at = ?, revoked_at = ?, updated_at = ? "
            "WHERE user_id = ?",
            (
                status,
                product_id,
                original_transaction_id,
                transaction_id,
                environment,
                purchased_at,
                expires_at,
                revoked_at,
                now,
                user_id,
            ),
        )
        conn.execute(
            "INSERT INTO subscription_events "
            "(user_id, product_id, original_transaction_id, transaction_id, environment, "
            " status, signed_transaction_info, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                product_id,
                original_transaction_id,
                transaction_id,
                environment,
                status,
                signed_transaction_info,
                json.dumps(payload or {}, sort_keys=True),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM user_entitlements WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return _entitlement_status_from_row(row)


# ── Round CRUD ────────────────────────────────────────────────────────────────


def create_round(
    user_id: str,
    session_id: str,
    target_score: Optional[int] = None,
    pars: Optional[list[int]] = None,
    profile_snapshot: Optional[dict] = None,
    course_name: Optional[str] = None,
) -> str:
    """Create a new round row. Returns the round ID."""
    now = datetime.now(timezone.utc).isoformat()
    round_id = uuid.uuid4().hex
    with get_db() as conn:
        conn.execute(
            "INSERT INTO rounds "
            "(id, user_id, session_id, course_name, status, target_score, pars, "
            " profile_snapshot, started_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                user_id,
                session_id,
                course_name,
                target_score,
                json.dumps(pars) if pars else None,
                json.dumps(profile_snapshot) if profile_snapshot else None,
                now,
                now,
                now,
            ),
        )
    return round_id


def save_round_score(round_id: str, hole: int, strokes: int, par: int, yardage: Optional[int] = None) -> None:
    """Insert or replace a score for a hole within a round."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO round_scores (round_id, hole, strokes, par, yardage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (round_id, hole, strokes, par, yardage, now),
        )
        conn.execute(
            "UPDATE rounds SET updated_at = ? WHERE id = ?", (now, round_id)
        )


def save_round_shot(
    round_id: str,
    hole: int,
    club: str,
    actual_distance: Optional[float] = None,
    miss_direction: Optional[str] = None,
    intended_distance: Optional[float] = None,
    lie: str = "fairway",
    notes: str = "",
    profile_carry: Optional[float] = None,
) -> None:
    """Append a shot record to a round."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO round_shots "
            "(round_id, hole, club, intended_distance, actual_distance, "
            " miss_direction, lie, notes, profile_carry, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                hole,
                club,
                intended_distance,
                actual_distance,
                miss_direction,
                lie,
                notes,
                profile_carry,
                now,
            ),
        )
        conn.execute(
            "UPDATE rounds SET updated_at = ? WHERE id = ?", (now, round_id)
        )


def _pack_embedding(vec: Optional[list[float]]) -> Optional[bytes]:
    """Serialize a float32 embedding vector for BLOB storage. ``None`` passes through."""
    if not vec:
        return None
    return struct.pack(f"{len(vec)}f", *(float(x) for x in vec))


def _unpack_embedding(blob: Optional[bytes]) -> Optional[list[float]]:
    """Inverse of :func:`_pack_embedding`. ``None`` / empty blobs return ``None``."""
    if not blob:
        return None
    n = len(blob) // 4
    if n == 0:
        return None
    return list(struct.unpack(f"{n}f", blob))


def save_round_message(
    round_id: str,
    role: str,
    content: str,
    hole: Optional[int] = None,
    embedding: Optional[list[float]] = None,
    embed_model: Optional[str] = None,
) -> None:
    """Append a chat message (user prompt or caddy response) to the round log.

    ``embedding`` is optional and only attached to user-role rows by callers —
    it powers the MemoryAgent's similarity search across past prompts.
    """
    if not content:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO round_messages "
            "(round_id, role, content, hole, created_at, embedding, embed_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (round_id, role, content, hole, now, _pack_embedding(embedding), embed_model),
        )
        conn.execute(
            "UPDATE rounds SET updated_at = ? WHERE id = ?", (now, round_id)
        )


def get_round_messages(round_id: str) -> list[dict]:
    """Return the full chat transcript for a round, ordered chronologically."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content, hole, created_at FROM round_messages "
            "WHERE round_id = ? ORDER BY id ASC",
            (round_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def finish_round(
    round_id: str,
    status: str = "completed",
    summary_text: Optional[str] = None,
    weather_summary: Optional[str] = None,
) -> None:
    """Mark a round as completed or abandoned and store the summary."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE rounds SET status = ?, finished_at = ?, summary_text = ?, "
            "weather_summary = ?, updated_at = ? WHERE id = ?",
            (status, now, summary_text, weather_summary, now, round_id),
        )


def delete_round(round_id: str, user_id: str) -> bool:
    """Delete a round and all its shots/scores. Returns False if not found or not owned."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if not row or row["user_id"] != user_id:
            return False
        conn.execute("DELETE FROM round_shots WHERE round_id = ?", (round_id,))
        conn.execute("DELETE FROM round_scores WHERE round_id = ?", (round_id,))
        conn.execute("DELETE FROM round_messages WHERE round_id = ?", (round_id,))
        conn.execute("DELETE FROM trial_round_starts WHERE round_id = ?", (round_id,))
        conn.execute("DELETE FROM rounds WHERE id = ?", (round_id,))
    return True


def update_round_course_name(round_id: str, course_name: str) -> None:
    """Set the course name on a round."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE rounds SET course_name = ?, updated_at = ? WHERE id = ?",
            (course_name, now, round_id),
        )


def update_round_session_id(round_id: str, session_id: str) -> None:
    """Update the active session ID associated with a round."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE rounds SET session_id = ?, updated_at = ? WHERE id = ?",
            (session_id, now, round_id),
        )


def get_round_by_id(round_id: str) -> Optional[dict]:
    """Load a round with its scores and shots."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
        if not row:
            return None
        rnd = dict(row)
        rnd["pars"] = json.loads(rnd["pars"]) if rnd["pars"] else None
        rnd["profile_snapshot"] = json.loads(rnd["profile_snapshot"]) if rnd["profile_snapshot"] else None

        scores = conn.execute(
            "SELECT hole, strokes, par, yardage FROM round_scores WHERE round_id = ? ORDER BY hole",
            (round_id,),
        ).fetchall()
        rnd["scores"] = [dict(s) for s in scores]

        total_strokes = sum(s["strokes"] for s in scores)
        total_par = sum(s["par"] for s in scores)
        rnd["total_strokes"] = total_strokes
        rnd["total_par"] = total_par
        rnd["score_vs_par"] = total_strokes - total_par
        rnd["holes_played"] = len(scores)

        shots = conn.execute(
            "SELECT hole, club, intended_distance, actual_distance, "
            "miss_direction, lie, notes, profile_carry FROM round_shots "
            "WHERE round_id = ? ORDER BY id",
            (round_id,),
        ).fetchall()
        rnd["shots"] = [dict(s) for s in shots]

        return rnd


def get_rounds_for_user(
    user_id: str,
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
) -> list[dict]:
    """List rounds for a user, most recent first. Includes computed totals."""
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM rounds WHERE user_id = ? AND status = ? "
                "ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (user_id, status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rounds WHERE user_id = ? "
                "ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()

        results = []
        for row in rows:
            rnd = dict(row)
            rnd["pars"] = json.loads(rnd["pars"]) if rnd["pars"] else None
            rnd.pop("profile_snapshot", None)

            scores = conn.execute(
                "SELECT hole, strokes, par FROM round_scores WHERE round_id = ? ORDER BY hole",
                (rnd["id"],),
            ).fetchall()
            total_strokes = sum(s["strokes"] for s in scores)
            total_par = sum(s["par"] for s in scores)
            rnd["total_strokes"] = total_strokes
            rnd["total_par"] = total_par
            rnd["score_vs_par"] = total_strokes - total_par if scores else None
            rnd["holes_played"] = len(scores)
            results.append(rnd)

        return results


def get_round_stats(user_id: str) -> dict:
    """Aggregate scoring statistics for a user across completed rounds."""
    with get_db() as conn:
        rounds = conn.execute(
            "SELECT id, target_score, started_at FROM rounds "
            "WHERE user_id = ? AND status = 'completed' ORDER BY started_at",
            (user_id,),
        ).fetchall()

        if not rounds:
            return {"total_rounds": 0}

        round_summaries = []
        all_scores_vs_par = []
        all_holes_data = []

        for rnd in rounds:
            scores = conn.execute(
                "SELECT hole, strokes, par FROM round_scores WHERE round_id = ?",
                (rnd["id"],),
            ).fetchall()

            if not scores:
                continue

            total_strokes = sum(s["strokes"] for s in scores)
            total_par = sum(s["par"] for s in scores)
            holes_played = len(scores)
            vs_par = total_strokes - total_par

            all_scores_vs_par.append(vs_par)
            for s in scores:
                all_holes_data.append(dict(s))

            round_summaries.append({
                "round_id": rnd["id"],
                "date": rnd["started_at"],
                "total_strokes": total_strokes,
                "holes_played": holes_played,
                "score_vs_par": vs_par,
                "target_score": rnd["target_score"],
                "hit_target": (
                    total_strokes <= rnd["target_score"]
                    if rnd["target_score"] and holes_played == 18
                    else None
                ),
            })

        scoring_dist = {"eagle_or_better": 0, "birdie": 0, "par": 0, "bogey": 0,
                        "double_bogey": 0, "triple_or_worse": 0}
        for h in all_holes_data:
            diff = h["strokes"] - h["par"]
            if diff <= -2:
                scoring_dist["eagle_or_better"] += 1
            elif diff == -1:
                scoring_dist["birdie"] += 1
            elif diff == 0:
                scoring_dist["par"] += 1
            elif diff == 1:
                scoring_dist["bogey"] += 1
            elif diff == 2:
                scoring_dist["double_bogey"] += 1
            else:
                scoring_dist["triple_or_worse"] += 1

        avg_vs_par = sum(all_scores_vs_par) / len(all_scores_vs_par) if all_scores_vs_par else 0
        best = min(all_scores_vs_par) if all_scores_vs_par else None
        worst = max(all_scores_vs_par) if all_scores_vs_par else None

        shots = conn.execute(
            "SELECT club, actual_distance, miss_direction FROM round_shots "
            "WHERE round_id IN (SELECT id FROM rounds WHERE user_id = ? AND status = 'completed')",
            (user_id,),
        ).fetchall()

        miss_dirs = {"left": 0, "right": 0, "short": 0, "long": 0}
        for s in shots:
            if s["miss_direction"] and s["miss_direction"] in miss_dirs:
                miss_dirs[s["miss_direction"]] += 1

        return {
            "total_rounds": len(round_summaries),
            "total_holes": len(all_holes_data),
            "avg_score_vs_par": round(avg_vs_par, 1),
            "best_score_vs_par": best,
            "worst_score_vs_par": worst,
            "scoring_distribution": scoring_dist,
            "miss_tendencies": miss_dirs,
            "recent_rounds": round_summaries[-10:],
        }


def get_active_round_for_session(session_id: str) -> Optional[str]:
    """Get the active round ID for a session, if any."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM rounds WHERE session_id = ? AND status = 'active'",
            (session_id,),
        ).fetchone()
        return row["id"] if row else None


def get_active_round_for_user(user_id: str) -> Optional[dict]:
    """Return the user's most recent active (in-progress) round, or None.

    A user is expected to have at most one active round at a time, but if multiple
    exist (e.g. from a previous bug or interrupted finish), the most recent wins.
    """
    rows = get_rounds_for_user(user_id, limit=1, status="active")
    return rows[0] if rows else None


# ── User Insights ─────────────────────────────────────────────────────────────


def compute_user_insights(user_id: str) -> dict:
    """Compute and persist a per-user intelligence profile from round history.

    Pure Python computation — zero LLM calls.  The result is upserted into
    the user_insights table and also returned to the caller.
    """
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        rounds = conn.execute(
            "SELECT id FROM rounds WHERE user_id = ? AND status = 'completed' "
            "ORDER BY started_at",
            (user_id,),
        ).fetchall()

        if not rounds:
            empty: dict = {"rounds_analyzed": 0, "updated_at": now}
            conn.execute(
                "INSERT OR REPLACE INTO user_insights "
                "(user_id, insights_json, rounds_analyzed, updated_at) "
                "VALUES (?, ?, 0, ?)",
                (user_id, json.dumps(empty), now),
            )
            return empty

        round_ids = [r["id"] for r in rounds]
        last_round_id = round_ids[-1]
        placeholders = ",".join("?" * len(round_ids))

        # All scores across completed rounds
        all_scores = conn.execute(
            f"SELECT round_id, hole, strokes, par FROM round_scores "
            f"WHERE round_id IN ({placeholders}) ORDER BY round_id, hole",
            round_ids,
        ).fetchall()

        # All shots across completed rounds
        all_shots = conn.execute(
            f"SELECT hole, club, actual_distance, miss_direction, profile_carry, lie "
            f"FROM round_shots WHERE round_id IN ({placeholders})",
            round_ids,
        ).fetchall()

        # ── club_actuals ──────────────────────────────────────────────────────
        club_shots_map: dict = defaultdict(list)
        for shot in all_shots:
            if shot["actual_distance"] is not None:
                club_shots_map[shot["club"]].append(dict(shot))

        club_actuals: dict = {}
        for club, shots in club_shots_map.items():
            if len(shots) < 3:
                continue
            avg_carry = sum(s["actual_distance"] for s in shots) / len(shots)
            profile_vals = [s["profile_carry"] for s in shots if s["profile_carry"] is not None]
            avg_profile = sum(profile_vals) / len(profile_vals) if profile_vals else None
            delta = round(avg_carry - avg_profile, 1) if avg_profile is not None else None

            miss_counts: dict = defaultdict(int)
            for s in shots:
                if s["miss_direction"]:
                    miss_counts[s["miss_direction"]] += 1
            dominant_miss = None
            if miss_counts:
                top = max(miss_counts, key=lambda k: miss_counts[k])
                if miss_counts[top] / sum(miss_counts.values()) >= 0.60:
                    dominant_miss = top

            club_actuals[club] = {
                "avg_carry": round(avg_carry, 1),
                "profile_carry": round(avg_profile, 1) if avg_profile is not None else None,
                "delta": delta,
                "shot_count": len(shots),
                "dominant_miss": dominant_miss,
            }

        # ── club_lie_deltas (context-aware carry deltas by lie) ───────────────
        club_lie_buckets: dict = defaultdict(lambda: defaultdict(list))
        for shot in all_shots:
            actual = shot["actual_distance"]
            profile = shot["profile_carry"]
            if actual is None or profile is None:
                continue
            lie = shot["lie"] or "fairway"
            club_lie_buckets[shot["club"]][lie].append(float(actual) - float(profile))

        club_lie_deltas: dict = {}
        for club, lie_map in club_lie_buckets.items():
            by_lie: dict = {}
            for lie, deltas in lie_map.items():
                if len(deltas) < 6:
                    continue
                by_lie[lie] = {
                    "avg_delta": round(sum(deltas) / len(deltas), 1),
                    "n": len(deltas),
                }
            if by_lie:
                club_lie_deltas[club] = by_lie

        # ── miss_tendencies ───────────────────────────────────────────────────
        miss_dirs = {"left": 0, "right": 0, "short": 0, "long": 0}
        for shot in all_shots:
            d = shot["miss_direction"]
            if d and d in miss_dirs:
                miss_dirs[d] += 1

        # ── scoring_patterns ──────────────────────────────────────────────────
        par3_diffs: list = []
        par4_diffs: list = []
        par5_diffs: list = []
        front9_strokes: list = []
        back9_strokes: list = []

        for s in all_scores:
            diff = s["strokes"] - s["par"]
            if s["par"] == 3:
                par3_diffs.append(diff)
            elif s["par"] == 4:
                par4_diffs.append(diff)
            elif s["par"] == 5:
                par5_diffs.append(diff)
            if s["hole"] <= 9:
                front9_strokes.append(s["strokes"])
            else:
                back9_strokes.append(s["strokes"])

        def _avg(lst: list) -> Optional[float]:
            return round(sum(lst) / len(lst), 2) if lst else None

        scoring_patterns = {
            "par3_avg": _avg(par3_diffs),
            "par4_avg": _avg(par4_diffs),
            "par5_avg": _avg(par5_diffs),
            "front9_avg": _avg(front9_strokes),
            "back9_avg": _avg(back9_strokes),
        }

        # ── fatigue_signal ────────────────────────────────────────────────────
        club_front: dict = defaultdict(list)
        club_back: dict = defaultdict(list)
        for shot in all_shots:
            if shot["actual_distance"] is not None:
                if shot["hole"] <= 9:
                    club_front[shot["club"]].append(shot["actual_distance"])
                else:
                    club_back[shot["club"]].append(shot["actual_distance"])

        fatigue_deltas: list = []
        for club in set(club_front) & set(club_back):
            front_avg = sum(club_front[club]) / len(club_front[club])
            back_avg = sum(club_back[club]) / len(club_back[club])
            fatigue_deltas.append(front_avg - back_avg)  # positive = shorter on back 9

        fatigue_signal = round(sum(fatigue_deltas) / len(fatigue_deltas), 1) if fatigue_deltas else None

        # ── pressure_pattern ─────────────────────────────────────────────────
        late = [s for s in all_scores if s["hole"] >= 15]
        early = [s for s in all_scores if s["hole"] < 15]

        def _avg_vs_par(lst: list) -> Optional[float]:
            if not lst:
                return None
            return sum(s["strokes"] - s["par"] for s in lst) / len(lst)

        late_avg = _avg_vs_par(late)
        early_avg = _avg_vs_par(early)
        pressure_delta = (
            round(late_avg - early_avg, 2)
            if late_avg is not None and early_avg is not None
            else None
        )

        # ── improvement_trend ─────────────────────────────────────────────────
        round_score_map: dict = defaultdict(list)
        for s in all_scores:
            round_score_map[s["round_id"]].append(dict(s))

        round_vs_par: list = []
        for rid in round_ids:
            hole_scores = round_score_map.get(rid, [])
            if hole_scores:
                round_vs_par.append(sum(s["strokes"] - s["par"] for s in hole_scores))

        improvement_trend = None
        if len(round_vs_par) >= 4:
            recent_avg = sum(round_vs_par[-3:]) / 3
            prior_avg = sum(round_vs_par[:-3]) / len(round_vs_par[:-3])
            diff = recent_avg - prior_avg
            if diff < -1.5:
                improvement_trend = "improving"
            elif diff > 1.5:
                improvement_trend = "declining"
            else:
                improvement_trend = "stable"

        insights = {
            "club_actuals": club_actuals,
            "club_lie_deltas": club_lie_deltas,
            "miss_tendencies": miss_dirs,
            "scoring_patterns": scoring_patterns,
            "fatigue_yards_lost": fatigue_signal,
            "pressure_scoring_delta": pressure_delta,
            "improvement_trend": improvement_trend,
            "rounds_analyzed": len(round_ids),
        }

        conn.execute(
            "INSERT OR REPLACE INTO user_insights "
            "(user_id, insights_json, rounds_analyzed, last_round_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, json.dumps(insights), len(round_ids), last_round_id, now),
        )

        insights["updated_at"] = now
        return insights


def get_calibration_suggestions(user_id: str) -> list:
    """Return clubs where actual carry differs from profile by 5+ yards (5+ shot min).

    Sorted by abs(delta) descending so the biggest discrepancies come first.
    Server-side read-only — the client applies changes to its local profile.
    """
    insights = get_user_insights(user_id)
    if not insights:
        return []

    suggestions = []
    for club, data in insights.get("club_actuals", {}).items():
        shot_count = data.get("shot_count", 0)
        delta = data.get("delta")
        profile_carry = data.get("profile_carry")
        avg_carry = data.get("avg_carry")
        if shot_count >= 5 and delta is not None and abs(delta) >= 5 and profile_carry is not None:
            suggestions.append({
                "club": club,
                "profile_carry": int(round(profile_carry)),
                "avg_carry": int(round(avg_carry)),
                "delta": int(round(delta)),
                "shot_count": shot_count,
            })

    suggestions.sort(key=lambda s: abs(s["delta"]), reverse=True)
    return suggestions


def save_user_note(user_id: str, note_text: str, note_type: str = "reminder") -> int:
    """Save a free-form reminder or swing thought for a user. Returns the new note id."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO user_notes (user_id, note_text, note_type, created_at) VALUES (?, ?, ?, ?)",
            (user_id, note_text.strip(), note_type, now),
        )
        return cursor.lastrowid


def get_user_notes(user_id: str, active_only: bool = True) -> list[dict]:
    """Return the user's stored notes, newest first."""
    with get_db() as conn:
        query = "SELECT id, note_text, note_type, created_at FROM user_notes WHERE user_id = ?"
        params: list = [user_id]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def delete_user_note(user_id: str, note_id: int) -> bool:
    """Soft-delete a note (marks is_active = 0). Returns True if a row was updated."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE user_notes SET is_active = 0 WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        return cursor.rowcount > 0


# ── Device tokens (APNs push notifications) ──────────────────────────────────


def upsert_device_token(user_id: str, device_token: str, platform: str = "ios") -> None:
    """Register or refresh a device token for push notifications."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO device_tokens (user_id, device_token, platform, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id, device_token) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (user_id, device_token, platform, now, now),
        )


def get_device_tokens(user_id: str) -> list[str]:
    """Return all active device tokens for a user."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT device_token FROM device_tokens WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [r["device_token"] for r in rows]


def delete_device_token(user_id: str, device_token: str) -> None:
    """Remove a stale or revoked token (e.g. APNs 410 response)."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM device_tokens WHERE user_id = ? AND device_token = ?",
            (user_id, device_token),
        )


def get_last_round_recap(user_id: str) -> Optional[str]:
    """Return the summary_text of the most recent completed round for a user."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT summary_text FROM rounds WHERE user_id = ? AND status = 'completed' "
            "AND summary_text IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["summary_text"] if row else None


def get_user_insights(user_id: str) -> Optional[dict]:
    """Return the stored insights dict for a user, or None if not computed yet."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT insights_json, updated_at FROM user_insights WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["insights_json"])
        data["updated_at"] = row["updated_at"]
        return data


# ── Memory & style profile ────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 on length mismatch or zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def is_memory_enabled(user_id: str) -> bool:
    """Return True when the user has not opted out of caddy memory recall."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT memory_enabled FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return False
        try:
            return bool(int(row["memory_enabled"]))
        except (TypeError, ValueError):
            return True


def set_memory_enabled(user_id: str, enabled: bool) -> None:
    """Update the per-user memory opt-out flag."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET memory_enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now, user_id),
        )


def search_user_messages_by_embedding(
    user_id: str,
    query_vec: list[float],
    *,
    top_k: int = 3,
    min_similarity: float = 0.78,
    exclude_round_id: Optional[str] = None,
    candidate_limit: int = 400,
) -> list[dict]:
    """Return the top-K past user prompts most similar to ``query_vec``.

    Each hit is expanded with the immediately following assistant reply so the
    caller can show "you asked X, the caddy said Y". Brute-force cosine over
    the user's most recent ``candidate_limit`` embedded prompts — fine for SQLite
    at our volume; can be swapped for sqlite-vec / FAISS later.
    """
    if not query_vec:
        return []

    with get_db() as conn:
        params: list = [user_id]
        clauses = [
            "rounds.user_id = ?",
            "round_messages.role = 'user'",
            "round_messages.embedding IS NOT NULL",
        ]
        if exclude_round_id:
            clauses.append("round_messages.round_id != ?")
            params.append(exclude_round_id)
        where = " AND ".join(clauses)

        rows = conn.execute(
            f"""
            SELECT round_messages.id AS msg_id,
                   round_messages.round_id AS round_id,
                   round_messages.content AS content,
                   round_messages.hole AS hole,
                   round_messages.created_at AS created_at,
                   round_messages.embedding AS embedding
              FROM round_messages
              JOIN rounds ON rounds.id = round_messages.round_id
             WHERE {where}
             ORDER BY round_messages.id DESC
             LIMIT ?
            """,
            (*params, int(candidate_limit)),
        ).fetchall()

        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vec = _unpack_embedding(row["embedding"])
            if not vec:
                continue
            sim = _cosine(query_vec, vec)
            if sim >= min_similarity:
                scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[: max(1, top_k)]

        if not scored:
            return []

        hits: list[dict] = []
        for sim, row in scored:
            reply = conn.execute(
                "SELECT content, created_at FROM round_messages "
                "WHERE round_id = ? AND role = 'assistant' AND id > ? "
                "ORDER BY id ASC LIMIT 1",
                (row["round_id"], row["msg_id"]),
            ).fetchone()
            hits.append({
                "round_id": row["round_id"],
                "hole": row["hole"],
                "user_text": row["content"],
                "assistant_text": reply["content"] if reply else None,
                "similarity": round(float(sim), 3),
                "created_at": row["created_at"],
            })
        return hits


def get_assistant_reply_samples(user_id: str, limit: int = 30) -> list[dict]:
    """Return recent (user_prompt, assistant_reply) pairs for style distillation.

    Pairs are gathered by walking the user's completed rounds newest-first and
    matching each user message with the next assistant message in the same
    round. Used by the style-distillation pass.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT round_messages.id AS msg_id,
                   round_messages.round_id AS round_id,
                   round_messages.role AS role,
                   round_messages.content AS content
              FROM round_messages
              JOIN rounds ON rounds.id = round_messages.round_id
             WHERE rounds.user_id = ? AND rounds.status = 'completed'
             ORDER BY round_messages.id DESC
             LIMIT ?
            """,
            (user_id, int(limit) * 4),
        ).fetchall()

    by_round: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_round[row["round_id"]].append(row)

    samples: list[dict] = []
    for msgs in by_round.values():
        msgs.sort(key=lambda r: r["msg_id"])
        i = 0
        while i < len(msgs) - 1:
            if msgs[i]["role"] == "user" and msgs[i + 1]["role"] == "assistant":
                samples.append({
                    "user_text": msgs[i]["content"],
                    "assistant_text": msgs[i + 1]["content"],
                })
                i += 2
            else:
                i += 1
    return samples[:limit]


def get_style_profile(user_id: str) -> Optional[dict]:
    """Return the user's distilled voice descriptor (if any)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT voice_descriptor, rounds_at_distill, distilled_at "
            "FROM user_style_profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "voice_descriptor": row["voice_descriptor"],
            "rounds_at_distill": int(row["rounds_at_distill"] or 0),
            "distilled_at": row["distilled_at"],
        }


def upsert_style_profile(user_id: str, voice_descriptor: str, rounds_at_distill: int) -> None:
    """Insert or replace the distilled voice descriptor for a user."""
    descriptor = (voice_descriptor or "").strip()
    if not descriptor:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_style_profile "
            "(user_id, voice_descriptor, rounds_at_distill, distilled_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, descriptor, int(rounds_at_distill), now),
        )


def count_completed_rounds(user_id: str) -> int:
    """Count the user's completed rounds — used to gate style distillation."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM rounds WHERE user_id = ? AND status = 'completed'",
            (user_id,),
        ).fetchone()
        return int(row["c"] or 0)
