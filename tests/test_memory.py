"""
Comprehensive tests for the KindCaddy Golfer Memory Profile feature.

Tests compute_user_insights(), get_user_insights(), and get_calibration_suggestions()
from kindcaddy/db.py using isolated temp SQLite databases.

Key implementation facts discovered from reading db.py:
- DB_PATH is a module-level Path derived from KINDCADDY_DB_PATH env var at import time.
  We must patch kindcaddy.db.DB_PATH directly.
- compute_user_insights() requires >= 3 shots per club to include it in club_actuals.
- improvement_trend requires >= 4 rounds with scores; compares last 3 vs all prior.
  Threshold: diff < -1.5 → "improving", diff > 1.5 → "declining", else "stable".
- calibration threshold: shot_count >= 5 AND abs(delta) >= 5 AND profile_carry not None.
- fatigue_signal: front-9 avg minus back-9 avg carry per club, averaged across clubs.
- pressure_delta: avg (strokes - par) on holes >= 15 minus same for holes < 15.
- scoring_patterns: par3/4/5_avg are avg of (strokes - par) diffs; front9/back9_avg are
  avg of raw strokes (not vs-par).
- get_user_insights() returns None for unknown user (no row in user_insights).
- No rounds → returns dict with rounds_analyzed=0, upserts an empty record.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import kindcaddy.db as db_module
from kindcaddy.db import (
    compute_user_insights,
    get_calibration_suggestions,
    get_user_insights,
    init_db,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def test_db(tmp_path):
    """Provide a fully isolated temp SQLite DB for each test.

    Patches kindcaddy.db.DB_PATH so every get_db() call hits the temp file,
    never the real data/kindcaddy.db.
    """
    db_file = tmp_path / "test_kindcaddy.db"
    with patch.object(db_module, "DB_PATH", db_file):
        init_db()
        yield db_file


# ── Seed Helper ───────────────────────────────────────────────────────────────


def _now_iso(days_ago: int = 0) -> str:
    """Return an ISO timestamp for `days_ago` days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _insert_user(conn: sqlite3.Connection, user_id: str) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO users (id, email, display_name, provider, created_at, updated_at) "
        "VALUES (?, ?, ?, 'email', ?, ?)",
        (user_id, f"{user_id}@test.com", f"User {user_id[:6]}", now, now),
    )


def _insert_round(
    conn: sqlite3.Connection,
    user_id: str,
    round_id: str,
    days_ago: int = 0,
    status: str = "completed",
) -> str:
    now = _now_iso(days_ago)
    conn.execute(
        "INSERT INTO rounds "
        "(id, user_id, session_id, status, started_at, finished_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (round_id, user_id, f"sess-{round_id[:8]}", status, now, now, now, now),
    )
    return round_id


def _insert_score(
    conn: sqlite3.Connection,
    round_id: str,
    hole: int,
    strokes: int,
    par: int,
) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO round_scores (round_id, hole, strokes, par, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (round_id, hole, strokes, par, now),
    )


def _insert_shot(
    conn: sqlite3.Connection,
    round_id: str,
    hole: int,
    club: str,
    actual_distance=150.0,
    miss_direction=None,
    profile_carry=150.0,
    lie: str = "fairway",
) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO round_shots "
        "(round_id, hole, club, actual_distance, miss_direction, profile_carry, lie, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (round_id, hole, club, actual_distance, miss_direction, profile_carry, lie, now),
    )


def seed_18_scores(conn: sqlite3.Connection, round_id: str, strokes_per_hole: list[int] | None = None) -> None:
    """Seed 18 holes of scores. Default strokes = par + 1 (bogey golf)."""
    standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
    for i, par in enumerate(standard_pars):
        hole = i + 1
        strokes = strokes_per_hole[i] if strokes_per_hole else par + 1
        _insert_score(conn, round_id, hole, strokes, par)


# ── compute_user_insights tests ───────────────────────────────────────────────


class TestComputeUserInsightsNoRounds:
    def test_no_rounds_returns_zero_rounds_analyzed(self, test_db):
        """User with no completed rounds: rounds_analyzed must be 0."""
        user_id = "user-no-rounds"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["rounds_analyzed"] == 0, (
            f"Expected rounds_analyzed=0, got {result['rounds_analyzed']}"
        )

    def test_no_rounds_persists_to_db(self, test_db):
        """compute_user_insights with no rounds must still write to user_insights."""
        user_id = "user-no-rounds-persist"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.commit()
        conn.close()

        compute_user_insights(user_id)
        stored = get_user_insights(user_id)
        assert stored is not None, "Expected a user_insights row even for zero rounds"
        assert stored["rounds_analyzed"] == 0

    def test_no_rounds_does_not_raise(self, test_db):
        """compute_user_insights must not raise for a brand-new user."""
        user_id = "user-no-rounds-safe"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        conn.commit()
        conn.close()

        # Should complete without exception
        compute_user_insights(user_id)


class TestClubActuals:
    def test_club_with_fewer_than_3_shots_excluded(self, test_db):
        """Club with only 2 shots must not appear in club_actuals."""
        user_id = "user-few-shots"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-few", days_ago=5)
        seed_18_scores(conn, rid)
        _insert_shot(conn, rid, 1, "7i", actual_distance=145.0, profile_carry=155.0)
        _insert_shot(conn, rid, 2, "7i", actual_distance=148.0, profile_carry=155.0)
        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert "7i" not in result["club_actuals"], (
            "7i has only 2 shots — it must be excluded from club_actuals"
        )

    def test_club_with_3_shots_included(self, test_db):
        """Club with exactly 3 shots must appear in club_actuals."""
        user_id = "user-three-shots"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-three", days_ago=5)
        seed_18_scores(conn, rid)
        for _ in range(3):
            _insert_shot(conn, rid, 1, "6i", actual_distance=160.0, profile_carry=165.0)
        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert "6i" in result["club_actuals"], (
            "6i has exactly 3 shots — it must be included in club_actuals"
        )

    def test_club_actuals_avg_carry_and_delta(self, test_db):
        """club_actuals must compute correct avg_carry and delta within ±1 yard."""
        user_id = "user-club-accuracy"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        # Round 1
        rid1 = _insert_round(conn, user_id, "round-ca1", days_ago=10)
        seed_18_scores(conn, rid1)
        # 7i: 8 shots averaging 149 yards; profile 155 → delta ≈ -6
        for d in [145, 148, 150, 152, 147, 149, 151, 150]:
            _insert_shot(conn, rid1, 1, "7i", actual_distance=float(d), profile_carry=155.0)

        # Round 2
        rid2 = _insert_round(conn, user_id, "round-ca2", days_ago=5)
        seed_18_scores(conn, rid2)
        # PW: 6 shots averaging 120 yards; profile 118 → delta ≈ +2
        for d in [118, 120, 122, 119, 121, 120]:
            _insert_shot(conn, rid2, 2, "PW", actual_distance=float(d), profile_carry=118.0)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        actuals = result["club_actuals"]

        assert "7i" in actuals, "7i should be in club_actuals"
        seven_iron = actuals["7i"]
        expected_7i_avg = sum([145, 148, 150, 152, 147, 149, 151, 150]) / 8  # 149.0
        assert abs(seven_iron["avg_carry"] - expected_7i_avg) <= 1.0, (
            f"7i avg_carry expected ~{expected_7i_avg}, got {seven_iron['avg_carry']}"
        )
        expected_7i_delta = expected_7i_avg - 155.0
        assert abs(seven_iron["delta"] - expected_7i_delta) <= 1.0, (
            f"7i delta expected ~{expected_7i_delta}, got {seven_iron['delta']}"
        )
        assert seven_iron["shot_count"] == 8, (
            f"7i shot_count expected 8, got {seven_iron['shot_count']}"
        )

        assert "PW" in actuals, "PW should be in club_actuals"
        pw = actuals["PW"]
        expected_pw_avg = sum([118, 120, 122, 119, 121, 120]) / 6  # 120.0
        assert abs(pw["avg_carry"] - expected_pw_avg) <= 1.0, (
            f"PW avg_carry expected ~{expected_pw_avg}, got {pw['avg_carry']}"
        )
        expected_pw_delta = expected_pw_avg - 118.0
        assert abs(pw["delta"] - expected_pw_delta) <= 1.0, (
            f"PW delta expected ~{expected_pw_delta}, got {pw['delta']}"
        )

    def test_shots_without_actual_distance_excluded_from_carry(self, test_db):
        """Shots with NULL actual_distance must be excluded from carry calculations."""
        user_id = "user-null-dist"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-null", days_ago=5)
        seed_18_scores(conn, rid)

        # 3 shots with real distances — club qualifies
        for d in [140.0, 142.0, 141.0]:
            _insert_shot(conn, rid, 3, "8i", actual_distance=d, profile_carry=145.0)
        # 5 shots with NULL distances — must be ignored
        for _ in range(5):
            _insert_shot(conn, rid, 4, "8i", actual_distance=None, profile_carry=145.0)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        # Should not crash, and avg_carry should only reflect the 3 non-null shots
        assert "8i" in result["club_actuals"], "8i should be included (3 non-null shots qualify)"
        eight_iron = result["club_actuals"]["8i"]
        expected_avg = (140.0 + 142.0 + 141.0) / 3
        assert abs(eight_iron["avg_carry"] - expected_avg) <= 1.0, (
            f"8i avg_carry should only use non-null shots: expected ~{expected_avg}, "
            f"got {eight_iron['avg_carry']}"
        )
        assert eight_iron["shot_count"] == 3, (
            f"shot_count must count only non-null distance shots, got {eight_iron['shot_count']}"
        )


class TestClubLieDeltas:
    def test_lie_delta_included_with_minimum_sample(self, test_db):
        """club_lie_deltas should include a lie bucket at 6+ shots."""
        user_id = "user-lie-delta"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-lie-delta", days_ago=2)
        seed_18_scores(conn, rid)

        # 7i from fairway, avg actual 148 vs profile 155 => avg_delta -7
        for d in [147, 148, 149, 146, 150, 148]:
            _insert_shot(conn, rid, 1, "7i", actual_distance=float(d), profile_carry=155.0, lie="fairway")

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        by_lie = result.get("club_lie_deltas", {}).get("7i", {}).get("fairway")
        assert by_lie is not None, "Expected fairway lie bucket for 7i"
        assert by_lie["n"] == 6, f"Expected n=6, got {by_lie['n']}"
        assert abs(by_lie["avg_delta"] - (-7.0)) <= 1.0, (
            f"Expected avg_delta ~-7.0, got {by_lie['avg_delta']}"
        )

    def test_lie_delta_excluded_below_sample_threshold(self, test_db):
        """club_lie_deltas should skip lie buckets with fewer than 6 shots."""
        user_id = "user-lie-delta-low-n"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-lie-delta-low-n", days_ago=2)
        seed_18_scores(conn, rid)

        for d in [148, 149, 147, 148, 150]:
            _insert_shot(conn, rid, 1, "7i", actual_distance=float(d), profile_carry=155.0, lie="fairway")

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert "7i" not in result.get("club_lie_deltas", {}), (
            "Expected no 7i lie bucket when fairway samples are below threshold"
        )


class TestMissTendencies:
    def test_miss_counts_are_correct(self, test_db):
        """miss_tendencies must count each direction accurately."""
        user_id = "user-misses"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-miss", days_ago=3)
        seed_18_scores(conn, rid)

        # 12 right, 3 left, 2 short, 1 long, 5 no direction
        for _ in range(12):
            _insert_shot(conn, rid, 1, "7i", miss_direction="right")
        for _ in range(3):
            _insert_shot(conn, rid, 2, "7i", miss_direction="left")
        for _ in range(2):
            _insert_shot(conn, rid, 3, "7i", miss_direction="short")
        _insert_shot(conn, rid, 4, "7i", miss_direction="long")
        for _ in range(5):
            _insert_shot(conn, rid, 5, "7i", miss_direction=None)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        mt = result["miss_tendencies"]

        assert mt["right"] == 12, f"Expected 12 right, got {mt['right']}"
        assert mt["left"] == 3, f"Expected 3 left, got {mt['left']}"
        assert mt["short"] == 2, f"Expected 2 short, got {mt['short']}"
        assert mt["long"] == 1, f"Expected 1 long, got {mt['long']}"

    def test_dominant_miss_detected(self, test_db):
        """dominant_miss on club_actuals should flag when one direction is >= 60%."""
        user_id = "user-dominant-miss"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-dom", days_ago=3)
        seed_18_scores(conn, rid)

        # 4 right misses out of 5 misses on 6 shots total = 4/5 = 80% right → dominant
        for _ in range(4):
            _insert_shot(conn, rid, 1, "5i", actual_distance=180.0, profile_carry=185.0,
                         miss_direction="right")
        _insert_shot(conn, rid, 2, "5i", actual_distance=178.0, profile_carry=185.0,
                     miss_direction="left")
        _insert_shot(conn, rid, 3, "5i", actual_distance=182.0, profile_carry=185.0,
                     miss_direction=None)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        actuals = result["club_actuals"]
        assert "5i" in actuals, "5i should be in club_actuals"
        assert actuals["5i"]["dominant_miss"] == "right", (
            f"Expected dominant_miss='right', got {actuals['5i']['dominant_miss']}"
        )

    def test_no_dominant_miss_when_below_threshold(self, test_db):
        """dominant_miss should be None when no direction hits 60% threshold."""
        user_id = "user-no-dom"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-nodom", days_ago=3)
        seed_18_scores(conn, rid)

        # 3 right, 3 left — 50/50 split, no dominant
        for _ in range(3):
            _insert_shot(conn, rid, 1, "4i", actual_distance=190.0, profile_carry=195.0,
                         miss_direction="right")
        for _ in range(3):
            _insert_shot(conn, rid, 2, "4i", actual_distance=192.0, profile_carry=195.0,
                         miss_direction="left")

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        actuals = result["club_actuals"]
        assert "4i" in actuals, "4i should be in club_actuals"
        assert actuals["4i"]["dominant_miss"] is None, (
            f"Expected dominant_miss=None (50/50 split), got {actuals['4i']['dominant_miss']}"
        )


class TestScoringPatterns:
    def test_scoring_patterns_values(self, test_db):
        """scoring_patterns must compute correct par-type and front/back avgs."""
        user_id = "user-scoring"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        # Standard 18-hole par layout matching seed_18_scores pars:
        # [4,4,3,4,5,4,3,4,5, 4,4,3,4,5,4,3,4,5]
        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        # Par 3 holes: 3,7,12,16 (indices 2,6,11,15)
        # Par 4 holes: 1,2,4,6,8,10,11,13,15,17 (indices 0,1,3,5,7,9,10,12,14,16)
        # Par 5 holes: 5,9,14,18 (indices 4,8,13,17)

        # Strokes: play exactly par on all holes
        strokes = list(standard_pars)  # all exactly par

        rid = _insert_round(conn, user_id, "round-score1", days_ago=5)
        for i, (par, stroke) in enumerate(zip(standard_pars, strokes)):
            _insert_score(conn, rid, i + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        sp = result["scoring_patterns"]

        # All at par → diff = 0 for all
        assert sp["par3_avg"] == 0.0, f"par3_avg expected 0.0, got {sp['par3_avg']}"
        assert sp["par4_avg"] == 0.0, f"par4_avg expected 0.0, got {sp['par4_avg']}"
        assert sp["par5_avg"] == 0.0, f"par5_avg expected 0.0, got {sp['par5_avg']}"

        # front9_avg = average raw strokes on holes 1-9
        front9_strokes = strokes[:9]
        expected_front9 = sum(front9_strokes) / len(front9_strokes)
        assert abs(sp["front9_avg"] - expected_front9) <= 0.01, (
            f"front9_avg expected {expected_front9:.2f}, got {sp['front9_avg']}"
        )

        # back9_avg = average raw strokes on holes 10-18
        back9_strokes = strokes[9:]
        expected_back9 = sum(back9_strokes) / len(back9_strokes)
        assert abs(sp["back9_avg"] - expected_back9) <= 0.01, (
            f"back9_avg expected {expected_back9:.2f}, got {sp['back9_avg']}"
        )

    def test_scoring_patterns_with_bogey_golf(self, test_db):
        """Scoring patterns with bogeys produce positive par diffs."""
        user_id = "user-bogey"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        strokes = [p + 1 for p in standard_pars]  # bogey on every hole

        rid = _insert_round(conn, user_id, "round-bogey", days_ago=3)
        for i, (par, stroke) in enumerate(zip(standard_pars, strokes)):
            _insert_score(conn, rid, i + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        sp = result["scoring_patterns"]

        # All +1 vs par
        assert sp["par3_avg"] == 1.0, f"par3_avg expected 1.0, got {sp['par3_avg']}"
        assert sp["par4_avg"] == 1.0, f"par4_avg expected 1.0, got {sp['par4_avg']}"
        assert sp["par5_avg"] == 1.0, f"par5_avg expected 1.0, got {sp['par5_avg']}"

    def test_partial_scores_does_not_crash(self, test_db):
        """Round with only 9 holes scored must compute without crashing."""
        user_id = "user-partial"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5]  # first 9 only
        strokes = [p + 1 for p in standard_pars]

        rid = _insert_round(conn, user_id, "round-partial", days_ago=3)
        for i, (par, stroke) in enumerate(zip(standard_pars, strokes)):
            _insert_score(conn, rid, i + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        sp = result["scoring_patterns"]

        # Back 9 data: should be None since no back-9 scores
        assert sp["back9_avg"] is None, (
            f"back9_avg should be None with only front-9 scores, got {sp['back9_avg']}"
        )
        assert sp["front9_avg"] is not None, "front9_avg should have a value"


class TestFatigueSignal:
    def test_fatigue_yards_lost(self, test_db):
        """fatigue_yards_lost should reflect carry drop from front to back 9."""
        user_id = "user-fatigue"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-fatigue", days_ago=5)
        seed_18_scores(conn, rid)

        # 7i on front 9 (holes 1-4): avg 155 yards
        for hole, d in [(1, 153.0), (2, 155.0), (3, 157.0), (4, 155.0)]:
            _insert_shot(conn, rid, hole, "7i", actual_distance=d, profile_carry=155.0)

        # 7i on back 9 (holes 10-13): avg 148 yards → fatigue delta ≈ 7
        for hole, d in [(10, 146.0), (11, 148.0), (12, 150.0), (13, 148.0)]:
            _insert_shot(conn, rid, hole, "7i", actual_distance=d, profile_carry=155.0)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        fatigue = result["fatigue_yards_lost"]

        assert fatigue is not None, "fatigue_yards_lost should be computed"
        front_avg = (153.0 + 155.0 + 157.0 + 155.0) / 4  # 155.0
        back_avg = (146.0 + 148.0 + 150.0 + 148.0) / 4   # 148.0
        expected = front_avg - back_avg  # 7.0
        assert abs(fatigue - expected) <= 1.0, (
            f"fatigue_yards_lost expected ~{expected}, got {fatigue}"
        )

    def test_fatigue_signal_none_without_both_halves(self, test_db):
        """fatigue_yards_lost is None when a club has shots on only one half."""
        user_id = "user-nofatigue"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-nofatigue", days_ago=3)
        seed_18_scores(conn, rid)

        # Only front-9 shots for each club
        for hole in [1, 2, 3]:
            _insert_shot(conn, rid, hole, "9i", actual_distance=130.0)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["fatigue_yards_lost"] is None, (
            "fatigue_yards_lost should be None when no club has shots on both halves"
        )


class TestPressurePattern:
    def test_pressure_scoring_delta(self, test_db):
        """pressure_scoring_delta = avg(strokes-par) on holes >=15 minus holes <15."""
        user_id = "user-pressure"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]

        # Holes 1-14: all bogeys (+1 vs par)
        # Holes 15-18: all double bogeys (+2 vs par)
        # → late_avg = 2.0 (avg vs par on holes 15-18)
        # → early_avg = 1.0 (avg vs par on holes 1-14)
        # → pressure_delta = 2.0 - 1.0 = 1.0
        strokes = []
        for i, par in enumerate(standard_pars):
            hole = i + 1
            if hole >= 15:
                strokes.append(par + 2)
            else:
                strokes.append(par + 1)

        rid = _insert_round(conn, user_id, "round-pressure", days_ago=3)
        for i, (par, stroke) in enumerate(zip(standard_pars, strokes)):
            _insert_score(conn, rid, i + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        pressure = result["pressure_scoring_delta"]

        assert pressure is not None, "pressure_scoring_delta should be computed"
        assert abs(pressure - 1.0) <= 0.05, (
            f"pressure_scoring_delta expected ~1.0, got {pressure}"
        )


class TestImprovementTrend:
    def test_improving_trend(self, test_db):
        """improvement_trend is 'improving' when recent 3 rounds are lower vs par."""
        user_id = "user-improving"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        total_par = sum(standard_pars)  # 72

        # 6 rounds: older 3 avg +12 vs par, recent 3 avg +6 vs par
        # diff = recent_avg - prior_avg = 6 - 12 = -6 → "improving" (< -1.5)
        round_scores_vs_par = [12, 13, 11, 6, 7, 5]  # older first, recent last

        for i, svp in enumerate(round_scores_vs_par):
            rid = f"round-imp-{i}"
            days = (6 - i) * 5  # round 0 is oldest
            _insert_round(conn, user_id, rid, days_ago=days)
            # Distribute excess over all par-4s uniformly
            strokes = list(standard_pars)  # start at par
            remaining = svp
            for j in range(len(strokes)):
                if remaining <= 0:
                    break
                strokes[j] += 1
                remaining -= 1
            for j, (par, stroke) in enumerate(zip(standard_pars, strokes)):
                _insert_score(conn, rid, j + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["improvement_trend"] == "improving", (
            f"Expected 'improving', got {result['improvement_trend']}"
        )

    def test_declining_trend(self, test_db):
        """improvement_trend is 'declining' when recent rounds are higher vs par."""
        user_id = "user-declining"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        # older 3 avg +5, recent 3 avg +12
        # diff = 12 - 5 = +7 → "declining" (> 1.5)
        round_scores_vs_par = [5, 6, 4, 12, 11, 13]

        for i, svp in enumerate(round_scores_vs_par):
            rid = f"round-dec-{i}"
            days = (6 - i) * 5
            _insert_round(conn, user_id, rid, days_ago=days)
            strokes = list(standard_pars)
            remaining = svp
            for j in range(len(strokes)):
                if remaining <= 0:
                    break
                strokes[j] += 1
                remaining -= 1
            for j, (par, stroke) in enumerate(zip(standard_pars, strokes)):
                _insert_score(conn, rid, j + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["improvement_trend"] == "declining", (
            f"Expected 'declining', got {result['improvement_trend']}"
        )

    def test_stable_trend(self, test_db):
        """improvement_trend is 'stable' when recent vs prior difference is <= 1.5."""
        user_id = "user-stable"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        # All rounds identical +8 → diff = 0 → "stable"
        round_scores_vs_par = [8, 8, 8, 8, 8, 8]

        for i, svp in enumerate(round_scores_vs_par):
            rid = f"round-stable-{i}"
            days = (6 - i) * 5
            _insert_round(conn, user_id, rid, days_ago=days)
            strokes = list(standard_pars)
            remaining = svp
            for j in range(len(strokes)):
                if remaining <= 0:
                    break
                strokes[j] += 1
                remaining -= 1
            for j, (par, stroke) in enumerate(zip(standard_pars, strokes)):
                _insert_score(conn, rid, j + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["improvement_trend"] == "stable", (
            f"Expected 'stable', got {result['improvement_trend']}"
        )

    def test_improvement_trend_requires_4_rounds(self, test_db):
        """improvement_trend must be None for fewer than 4 rounds with scores."""
        user_id = "user-few-rounds"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        standard_pars = [4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5]
        for i in range(3):
            rid = f"round-few-{i}"
            _insert_round(conn, user_id, rid, days_ago=(3 - i) * 5)
            strokes = [p + 1 for p in standard_pars]
            for j, (par, stroke) in enumerate(zip(standard_pars, strokes)):
                _insert_score(conn, rid, j + 1, stroke, par)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)
        assert result["improvement_trend"] is None, (
            f"Expected improvement_trend=None with only 3 rounds, got {result['improvement_trend']}"
        )


class TestInsightsPersistence:
    def test_get_user_insights_returns_none_for_unknown_user(self, test_db):
        """get_user_insights must return None when no row exists."""
        result = get_user_insights("nonexistent-user-xyz")
        assert result is None, "Expected None for user with no insights row"

    def test_get_user_insights_returns_computed_data(self, test_db):
        """get_user_insights must return matching data after compute_user_insights."""
        user_id = "user-persist"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-persist", days_ago=3)
        seed_18_scores(conn, rid)
        for _ in range(4):
            _insert_shot(conn, rid, 1, "7i", actual_distance=150.0, profile_carry=155.0)
        conn.commit()
        conn.close()

        computed = compute_user_insights(user_id)
        stored = get_user_insights(user_id)

        assert stored is not None, "get_user_insights should return data after compute"
        assert stored["rounds_analyzed"] == computed["rounds_analyzed"], (
            f"rounds_analyzed mismatch: computed={computed['rounds_analyzed']}, "
            f"stored={stored['rounds_analyzed']}"
        )
        # club_actuals should match
        assert stored.get("club_actuals", {}) == computed.get("club_actuals", {}), (
            "club_actuals should match between computed and stored insights"
        )

    def test_upsert_behavior_updates_on_recompute(self, test_db):
        """Calling compute_user_insights again must update the persisted row."""
        user_id = "user-upsert"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)

        # First compute with 1 round
        rid1 = _insert_round(conn, user_id, "round-ups1", days_ago=10)
        seed_18_scores(conn, rid1)
        conn.commit()
        conn.close()

        compute_user_insights(user_id)
        first = get_user_insights(user_id)
        assert first["rounds_analyzed"] == 1, f"Expected 1 round, got {first['rounds_analyzed']}"

        # Add a second round and recompute
        conn2 = sqlite3.connect(str(test_db))
        conn2.row_factory = sqlite3.Row
        rid2 = _insert_round(conn2, user_id, "round-ups2", days_ago=5)
        seed_18_scores(conn2, rid2)
        conn2.commit()
        conn2.close()

        compute_user_insights(user_id)
        second = get_user_insights(user_id)
        assert second["rounds_analyzed"] == 2, (
            f"After recompute with 2 rounds, expected rounds_analyzed=2, got {second['rounds_analyzed']}"
        )


class TestSingleRound:
    def test_single_round_basics(self, test_db):
        """Single completed round: rounds_analyzed=1, club data present, trend=None."""
        user_id = "user-single"
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_id)
        rid = _insert_round(conn, user_id, "round-single", days_ago=3)
        seed_18_scores(conn, rid)

        # 5 shots with 3 different clubs to produce club data
        for _ in range(5):
            _insert_shot(conn, rid, 1, "7i", actual_distance=150.0, profile_carry=155.0)
        for _ in range(4):
            _insert_shot(conn, rid, 2, "PW", actual_distance=118.0, profile_carry=120.0)
        for _ in range(2):
            _insert_shot(conn, rid, 3, "Driver", actual_distance=240.0, profile_carry=250.0)

        conn.commit()
        conn.close()

        result = compute_user_insights(user_id)

        assert result["rounds_analyzed"] == 1, (
            f"Expected rounds_analyzed=1, got {result['rounds_analyzed']}"
        )
        assert "7i" in result["club_actuals"], "7i (5 shots) must be in club_actuals"
        assert "PW" in result["club_actuals"], "PW (4 shots) must be in club_actuals"
        assert "Driver" not in result["club_actuals"], (
            "Driver (2 shots) must NOT be in club_actuals (< 3 shot minimum)"
        )
        assert result["scoring_patterns"] is not None, "scoring_patterns must not be None"
        assert result["improvement_trend"] is None, (
            f"improvement_trend must be None with 1 round, got {result['improvement_trend']}"
        )


# ── get_calibration_suggestions tests ────────────────────────────────────────


class TestCalibrationSuggestions:
    def _setup_user_with_insights(self, test_db, user_id: str, club_actuals: dict) -> None:
        """Insert a user_insights row directly with given club_actuals."""
        now = datetime.now(timezone.utc).isoformat()
        insights = {
            "club_actuals": club_actuals,
            "miss_tendencies": {"left": 0, "right": 0, "short": 0, "long": 0},
            "scoring_patterns": {"par3_avg": 0, "par4_avg": 1, "par5_avg": 1,
                                 "front9_avg": 4.5, "back9_avg": 4.5},
            "fatigue_yards_lost": None,
            "pressure_scoring_delta": None,
            "improvement_trend": None,
            "rounds_analyzed": 2,
        }
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO users (id, email, display_name, provider, created_at, updated_at) "
            "VALUES (?, ?, ?, 'email', ?, ?)",
            (user_id, f"{user_id}@t.com", "U", now, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO user_insights "
            "(user_id, insights_json, rounds_analyzed, updated_at) VALUES (?, ?, 2, ?)",
            (user_id, json.dumps(insights), now),
        )
        conn.commit()
        conn.close()

    def test_empty_for_unknown_user(self, test_db):
        """get_calibration_suggestions returns [] for a user with no insights."""
        result = get_calibration_suggestions("completely-unknown-user")
        assert result == [], f"Expected [], got {result}"

    def test_qualifying_club_returned(self, test_db):
        """Club with >= 5 shots and abs(delta) >= 5 must appear in suggestions."""
        user_id = "user-cal-qualify"
        self._setup_user_with_insights(test_db, user_id, {
            "7i": {
                "avg_carry": 148.0,
                "profile_carry": 155.0,
                "delta": -7.0,
                "shot_count": 6,
                "dominant_miss": None,
            }
        })

        result = get_calibration_suggestions(user_id)
        assert len(result) == 1, f"Expected 1 suggestion, got {len(result)}"
        assert result[0]["club"] == "7i"
        assert result[0]["shot_count"] == 6
        assert abs(result[0]["delta"]) >= 5

    def test_club_with_fewer_than_5_shots_excluded(self, test_db):
        """Club with only 4 shots must NOT appear even with large delta."""
        user_id = "user-cal-few"
        self._setup_user_with_insights(test_db, user_id, {
            "5i": {
                "avg_carry": 170.0,
                "profile_carry": 185.0,
                "delta": -15.0,
                "shot_count": 4,
                "dominant_miss": None,
            }
        })

        result = get_calibration_suggestions(user_id)
        assert result == [], (
            f"Expected no suggestions (only 4 shots), got {result}"
        )

    def test_club_with_small_delta_excluded(self, test_db):
        """Club with >= 5 shots but delta < 5 yards must NOT appear."""
        user_id = "user-cal-small-delta"
        self._setup_user_with_insights(test_db, user_id, {
            "PW": {
                "avg_carry": 118.0,
                "profile_carry": 120.0,
                "delta": -2.0,
                "shot_count": 10,
                "dominant_miss": None,
            }
        })

        result = get_calibration_suggestions(user_id)
        assert result == [], (
            f"Expected no suggestions (delta < 5 yards), got {result}"
        )

    def test_calibration_sorted_by_abs_delta_descending(self, test_db):
        """Multiple qualifying clubs must be sorted by abs(delta) descending."""
        user_id = "user-cal-sort"
        self._setup_user_with_insights(test_db, user_id, {
            "6i": {
                "avg_carry": 158.0,
                "profile_carry": 165.0,
                "delta": -7.0,
                "shot_count": 8,
                "dominant_miss": None,
            },
            "Driver": {
                "avg_carry": 225.0,
                "profile_carry": 250.0,
                "delta": -25.0,
                "shot_count": 12,
                "dominant_miss": None,
            },
            "9i": {
                "avg_carry": 126.0,
                "profile_carry": 132.0,
                "delta": -6.0,
                "shot_count": 6,
                "dominant_miss": None,
            },
        })

        result = get_calibration_suggestions(user_id)
        assert len(result) == 3, f"Expected 3 suggestions, got {len(result)}"
        deltas = [abs(s["delta"]) for s in result]
        assert deltas == sorted(deltas, reverse=True), (
            f"Suggestions must be sorted by abs(delta) desc, got deltas: {deltas}"
        )
        assert result[0]["club"] == "Driver", (
            f"Driver has the biggest delta and should be first, got {result[0]['club']}"
        )

    def test_calibration_excludes_none_profile_carry(self, test_db):
        """Club with profile_carry=None must be excluded even with shots and large delta."""
        user_id = "user-cal-noprofile"
        self._setup_user_with_insights(test_db, user_id, {
            "3i": {
                "avg_carry": 200.0,
                "profile_carry": None,
                "delta": None,
                "shot_count": 8,
                "dominant_miss": None,
            }
        })

        result = get_calibration_suggestions(user_id)
        assert result == [], (
            f"Club with no profile_carry should be excluded, got {result}"
        )


# ── Multi-user isolation tests ────────────────────────────────────────────────


class TestMultiUserIsolation:
    def test_insights_isolated_per_user(self, test_db):
        """compute_user_insights for user A must not include user B's data."""
        user_a = "user-iso-a"
        user_b = "user-iso-b"

        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        _insert_user(conn, user_a)
        _insert_user(conn, user_b)

        # User A: 5 shots with 7i, avg 150, profile 160 → delta -10
        rid_a = _insert_round(conn, user_a, "round-iso-a", days_ago=5)
        seed_18_scores(conn, rid_a)
        for _ in range(5):
            _insert_shot(conn, rid_a, 1, "7i", actual_distance=150.0, profile_carry=160.0)

        # User B: 5 shots with 7i, avg 180, profile 160 → delta +20 (very different)
        rid_b = _insert_round(conn, user_b, "round-iso-b", days_ago=5)
        seed_18_scores(conn, rid_b)
        for _ in range(5):
            _insert_shot(conn, rid_b, 1, "7i", actual_distance=180.0, profile_carry=160.0)

        conn.commit()
        conn.close()

        result_a = compute_user_insights(user_a)
        result_b = compute_user_insights(user_b)

        seven_a = result_a["club_actuals"].get("7i")
        seven_b = result_b["club_actuals"].get("7i")

        assert seven_a is not None, "User A should have 7i data"
        assert seven_b is not None, "User B should have 7i data"

        assert abs(seven_a["avg_carry"] - 150.0) <= 1.0, (
            f"User A's 7i avg_carry should be ~150, got {seven_a['avg_carry']}"
        )
        assert abs(seven_b["avg_carry"] - 180.0) <= 1.0, (
            f"User B's 7i avg_carry should be ~180, got {seven_b['avg_carry']}"
        )

        # Confirm they are truly independent — no bleed-through
        assert seven_a["avg_carry"] != seven_b["avg_carry"], (
            "User A and B should have different avg_carry values"
        )

    def test_get_user_insights_isolated(self, test_db):
        """get_user_insights for user A must not return user B's stored row."""
        user_a = "user-get-iso-a"
        user_b = "user-get-iso-b"
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(str(test_db))
        _insert_user(conn, user_a)
        _insert_user(conn, user_b)
        conn.commit()
        conn.close()

        # Manually insert insights for user B only
        conn2 = sqlite3.connect(str(test_db))
        insights_b = {"rounds_analyzed": 5, "club_actuals": {}}
        conn2.execute(
            "INSERT INTO user_insights (user_id, insights_json, rounds_analyzed, updated_at) "
            "VALUES (?, ?, 5, ?)",
            (user_b, json.dumps(insights_b), now),
        )
        conn2.commit()
        conn2.close()

        # User A should get None — has no insights row
        result_a = get_user_insights(user_a)
        assert result_a is None, (
            f"User A has no insights row, expected None, got {result_a}"
        )

        # User B should get their data
        result_b = get_user_insights(user_b)
        assert result_b is not None, "User B should have insights"
        assert result_b["rounds_analyzed"] == 5
