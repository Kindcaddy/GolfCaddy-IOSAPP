"""End-to-end integration test for the full KindCaddy round pipeline.

Requires a running local server:

    KINDCADDY_DB_PATH=data/test_e2e.db KINDCADDY_JWT_SECRET=test-e2e-secret python3 -m uvicorn kindcaddy.api:app --port 8765

Run:

    python tests/test_e2e_round.py

This test is intentionally NOT a pytest module — it hits the live server with
real HTTP requests and real OpenAI calls. It is idempotent: each run creates
a fresh unique Google-backed test user directly in the local test database.
"""

import os
import sys
from uuid import uuid4

import httpx

os.environ.setdefault("KINDCADDY_DB_PATH", "data/test_e2e.db")
os.environ.setdefault("KINDCADDY_JWT_SECRET", "test-e2e-secret")

from kindcaddy.auth import create_access_token
from kindcaddy.db import upsert_google_user

BASE_URL = "http://localhost:8765"
TIMEOUT = 60.0

# Default 18-hole par layout used by the backend
DEFAULT_PARS = [4, 4, 4, 3, 5, 4, 4, 3, 5, 4, 4, 4, 3, 5, 4, 4, 3, 5]
TOTAL_PAR = sum(DEFAULT_PARS)  # 72

# Bogey-golf scorecard (87 total, +15 vs par)
# One bogey on every hole except three doubles on par-3s and two birdies
SCORES = [5, 5, 5, 4, 6, 5, 5, 4, 6, 5, 5, 5, 4, 6, 5, 5, 4, 6]  # 90 total → +18
assert len(SCORES) == 18

PASS_COUNT = 0
FAIL_COUNT = 0


def check(condition: bool, message: str) -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  ✓ {message}")
    else:
        FAIL_COUNT += 1
        print(f"  ✗ FAIL: {message}")
        raise AssertionError(message)


def header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def make_profile(display_name: str) -> dict:
    """Full golfer profile with 7-iron carry = 155yd as the key calibration target."""
    return {
        "name": display_name,
        "handicap": 15.0,
        "shot_shape": "fade",
        "handed": "right",
        "chat_style": "minimal",
        "model_selection": "gpt_wrapper",
        "target_score": 90,
        "clubs": {
            "Driver": {"carry": 230, "total": 245},
            "7i": {"carry": 155, "total": 165},
            "PW": {"carry": 120, "total": 127},
        },
        "tendencies": {
            "under_pressure": "pushes right",
            "back_nine": "loses 3 yards on irons",
            "wind": "",
            "general": "tends to miss right",
        },
        "physical": {
            "gender": "male",
            "age_group": "30s",
            "driver_clubhead_speed_mph": 95.0,
            "workout_frequency": "2x/week",
            "practice_frequency": "weekly",
        },
    }


def play_round(client: httpx.Client, session_id: str) -> None:
    """Log all 18 holes via commands: newround → weather → shots → scores → summary."""

    def cmd(command: str, args: str = "") -> dict:
        r = client.post("/command", json={"session_id": session_id, "command": command, "args": args})
        r.raise_for_status()
        return r.json()

    cmd("newround")
    cmd("weather", "72F wind 8mph SW")

    # Log 10 seven-iron shots (avg ~148yd, 6 miss right) across holes 1–10
    seven_iron_shots = [
        (1, "7i", 145, "right"),
        (2, "7i", 150, "right"),
        (3, "7i", 148, "right"),
        (4, "7i", 152, None),
        (5, "7i", 146, "right"),
        (6, "7i", 150, None),
        (7, "7i", 148, "right"),
        (8, "7i", 145, "right"),
        (9, "7i", 150, None),
        (10, "7i", 147, "right"),
    ]
    seven_iron_by_hole = {h: (club, dist, miss) for h, club, dist, miss in seven_iron_shots}

    for hole_idx, (score, par) in enumerate(zip(SCORES, DEFAULT_PARS), start=1):
        cmd("hole", str(hole_idx))

        # Log a 7-iron shot on designated holes
        if hole_idx in seven_iron_by_hole:
            club, dist, miss = seven_iron_by_hole[hole_idx]
            args = f"{club} {dist}" + (f" {miss}" if miss else "")
            cmd("shot", args)

        # Log a PW shot on par-3 holes (3, 8, 13, 17) when no 7i already logged
        if par == 3 and hole_idx not in seven_iron_by_hole:
            cmd("shot", "PW 118")

        cmd("score", str(score))

    cmd("summary")


def main() -> None:
    email = f"test_{uuid4().hex[:8]}@kindcaddy.test"
    display_name = "E2E Tester"

    client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)

    # ------------------------------------------------------------------ #
    header("Step 1: Create local test user")
    user = upsert_google_user(
        google_sub=f"e2e-google-{uuid4().hex}",
        email=email,
        display_name=display_name,
    )
    token = create_access_token(user.id)
    user_id = user.id
    check(bool(token), "Access token is non-empty")
    check(bool(user_id), "User ID is non-empty")
    print(f"  → user_id={user_id}")

    auth_headers = {"Authorization": f"Bearer {token}"}
    client.headers.update(auth_headers)

    # ------------------------------------------------------------------ #
    header("Step 2: Create session 1 + play full round")
    r = client.post("/session", json={
        "profile": make_profile(display_name),
        "model": "gpt-4o",
        "max_tokens": 512,
    })
    check(r.status_code == 200, f"Create session returned 200 (got {r.status_code})")
    sess = r.json()
    session_id_1 = sess["session_id"]
    check(bool(session_id_1), "session_id is non-empty")
    briefing_1 = sess.get("briefing", "")
    check(bool(briefing_1), "Pre-round briefing is non-empty")
    print(f"  → session_id={session_id_1}")
    print(f"  → briefing preview: {briefing_1[:80]}...")

    play_round(client, session_id_1)
    print("  → 18 holes logged")

    # ------------------------------------------------------------------ #
    header("Step 3: Make one advice call in session 1")
    r = client.post("/advice", json={"session_id": session_id_1, "text": "155 out, pin back right, slight wind into"})
    check(r.status_code == 200, f"Advice returned 200 (got {r.status_code})")
    advice_text = r.json().get("text", "")
    check(len(advice_text) > 10, f"Advice response is substantive (len={len(advice_text)})")
    print(f"  → advice preview: {advice_text[:80]}...")

    # ------------------------------------------------------------------ #
    header("Step 4: Finish round explicitly")
    r = client.get(f"/session/{session_id_1}")
    check(r.status_code == 200, f"Session state returned 200 (got {r.status_code})")
    state = r.json()
    round_id = state.get("round_id")
    check(bool(round_id), "round_id present in session state")
    print(f"  → round_id={round_id}")

    r = client.post(f"/rounds/{round_id}/finish", json={"status": "completed"})
    # 200 = finished now, 409 = already finished by the summary command — both are fine
    check(r.status_code in (200, 409), f"Finish round returned 200 or 409 (got {r.status_code})")

    # ------------------------------------------------------------------ #
    header("Step 5: Verify GET /rounds")
    r = client.get("/rounds?limit=5")
    check(r.status_code == 200, f"GET /rounds returned 200 (got {r.status_code})")
    rounds_data = r.json()
    check("rounds" in rounds_data, "Response has 'rounds' key")
    rounds = rounds_data["rounds"]
    check(len(rounds) >= 1, f"At least 1 round returned (got {len(rounds)})")

    rnd = next((x for x in rounds if x["id"] == round_id), None)
    check(rnd is not None, "Round 1 appears in /rounds")
    expected_strokes = sum(SCORES)
    total_strokes = rnd["total_strokes"]
    holes_played = rnd["holes_played"]
    score_vs_par = rnd.get("score_vs_par")
    check(total_strokes == expected_strokes, f"total_strokes={total_strokes} == {expected_strokes}")
    check(holes_played == 18, f"holes_played={holes_played} == 18")
    check(score_vs_par == expected_strokes - TOTAL_PAR,
          f"score_vs_par={score_vs_par} == {expected_strokes - TOTAL_PAR}")
    check(rnd.get("status") == "completed", f"Round status=completed (got {rnd.get('status')!r})")
    check(rnd.get("finished_at") is not None, "Round has finished_at timestamp")
    check(bool(rnd.get("weather_summary")), "Round has weather_summary")
    check(bool(rnd.get("summary_text")), "Round has AI-generated summary_text (recap)")
    print(f"  → recap preview: {(rnd.get('summary_text') or '')[:80]}...")

    # ------------------------------------------------------------------ #
    header("Step 6: Verify GET /rounds/{id} — detail view")
    r = client.get(f"/rounds/{round_id}")
    check(r.status_code == 200, f"GET /rounds/{{id}} returned 200 (got {r.status_code})")
    detail = r.json()
    scores = detail.get("scores", [])
    shots = detail.get("shots", [])
    check(len(scores) == 18, f"18 score entries (got {len(scores)})")
    check(len(shots) >= 10, f"At least 10 shot entries (got {len(shots)})")

    # All 18 hole scores match exactly
    score_map = {s["hole"]: s["strokes"] for s in scores}
    for i, expected in enumerate(SCORES, start=1):
        check(score_map.get(i) == expected, f"Hole {i}: strokes={score_map.get(i)} == {expected}")

    # Par values present and correct
    par_map = {s["hole"]: s["par"] for s in scores}
    for i, expected_par in enumerate(DEFAULT_PARS, start=1):
        check(par_map.get(i) == expected_par, f"Hole {i}: par={par_map.get(i)} == {expected_par}")

    # 7-iron shots logged with correct distances and miss directions
    seven_iron_shots_logged = [s for s in shots if s["club"] in ("7i", "7-iron", "7I")]
    check(len(seven_iron_shots_logged) == 10, f"10 seven-iron shots logged (got {len(seven_iron_shots_logged)})")
    right_misses = [s for s in seven_iron_shots_logged if s.get("miss_direction") == "right"]
    check(len(right_misses) == 7, f"7 right-miss 7-iron shots (got {len(right_misses)})")
    carries = [s["actual_distance"] for s in seven_iron_shots_logged if s.get("actual_distance")]
    check(len(carries) == 10, f"All 10 seven-iron shots have actual_distance (got {len(carries)})")
    avg = sum(carries) / len(carries)
    check(144 <= avg <= 152, f"Avg 7-iron carry ~148yd (got {avg:.1f})")

    # ------------------------------------------------------------------ #
    header("Step 7: Verify GET /rounds/stats — scoring distribution & misses")
    r = client.get("/rounds/stats")
    check(r.status_code == 200, f"GET /rounds/stats returned 200 (got {r.status_code})")
    stats = r.json()
    check(stats["total_rounds"] >= 1, f"total_rounds >= 1 (got {stats['total_rounds']})")
    check(stats["total_holes"] >= 18, f"total_holes >= 18 (got {stats['total_holes']})")
    check(stats.get("avg_score_vs_par") is not None, "avg_score_vs_par is present")
    avg_vs_par = stats["avg_score_vs_par"]
    check(avg_vs_par > 0, f"avg_score_vs_par is positive (bogey golf) (got {avg_vs_par})")

    dist = stats.get("scoring_distribution", {})
    check(dist.get("bogey", 0) >= 1, f"At least 1 bogey in distribution (got {dist.get('bogey', 0)})")
    # All 18 holes are bogeys in our test scorecard
    check(dist.get("bogey", 0) == 18, f"All 18 holes are bogeys (got {dist.get('bogey', 0)})")
    total_dist = sum(dist.values())
    check(total_dist == 18, f"Scoring distribution sums to 18 holes (got {total_dist})")
    print(f"  → distribution: {dist}")

    miss = stats.get("miss_tendencies", {})
    check(miss.get("right", 0) >= 6, f"At least 6 right misses (got {miss.get('right', 0)})")
    total_miss = sum(miss.values())
    check(total_miss >= 6, f"At least 6 total misses tracked (got {total_miss})")
    print(f"  → miss tendencies: {miss}")

    recent = stats.get("recent_rounds", [])
    check(len(recent) >= 1, f"recent_rounds has at least 1 entry (got {len(recent)})")
    latest = recent[0]
    check(latest["total_strokes"] == expected_strokes,
          f"recent_rounds[0].total_strokes={latest['total_strokes']} == {expected_strokes}")
    check(latest["holes_played"] == 18, f"recent_rounds[0].holes_played=18")

    # ------------------------------------------------------------------ #
    header("Step 8: Verify GET /insights — club insights & scoring patterns")
    r = client.get("/insights")
    check(r.status_code == 200, f"GET /insights returned 200 (got {r.status_code})")
    insights = r.json()
    club_insights = insights.get("club_insights", [])
    check(len(club_insights) >= 1, f"At least 1 club insight (got {len(club_insights)})")
    check(insights.get("rounds_analyzed", 0) >= 1,
          f"rounds_analyzed >= 1 (got {insights.get('rounds_analyzed')})")

    miss_tend = insights.get("miss_tendencies", {})
    check(miss_tend.get("right", 0) >= 6,
          f"Insights: at least 6 right misses (got {miss_tend.get('right', 0)})")

    # Scoring patterns
    patterns = insights.get("scoring_patterns")
    if patterns:
        check(patterns.get("par4_avg") is not None, "scoring_patterns.par4_avg is present")
        check(patterns.get("par3_avg") is not None, "scoring_patterns.par3_avg is present")
        check(patterns.get("par5_avg") is not None, "scoring_patterns.par5_avg is present")
        front9 = patterns.get("front9_avg")
        back9 = patterns.get("back9_avg")
        check(front9 is not None and back9 is not None, "front9_avg and back9_avg present")
        print(f"  → scoring patterns: par3={patterns.get('par3_avg'):.2f}, "
              f"par4={patterns.get('par4_avg'):.2f}, par5={patterns.get('par5_avg'):.2f}, "
              f"front9={front9:.2f}, back9={back9:.2f}")

    # 7-iron detail
    seven_insight = next((c for c in club_insights if c["club"] in ("7i", "7-iron", "7I")), None)
    check(seven_insight is not None, "7-iron insight exists")
    if seven_insight:
        shot_count = seven_insight["shot_count"]
        avg_carry = seven_insight["avg_carry"]
        delta = seven_insight.get("delta")
        dominant_miss = seven_insight.get("dominant_miss", "")
        check(shot_count >= 10, f"7-iron shot_count >= 10 (got {shot_count})")
        check(144 <= avg_carry <= 152, f"7-iron avg_carry ~148 (got {avg_carry})")
        check(delta is not None and delta < 0, f"7-iron delta is negative (got {delta})")
        check("right" in (dominant_miss or "").lower(),
              f"7-iron dominant_miss=right (got {dominant_miss!r})")
        print(f"  → 7-iron: avg_carry={avg_carry:.1f}yd, delta={delta:.1f}yd, "
              f"miss={dominant_miss!r}, shots={shot_count}")

    # ------------------------------------------------------------------ #
    header("Step 9: Verify GET /calibration")
    r = client.get("/calibration")
    check(r.status_code == 200, f"GET /calibration returned 200 (got {r.status_code})")
    cal = r.json()
    suggestions = cal.get("suggestions", [])
    check(len(suggestions) >= 1, f"At least 1 calibration suggestion (got {len(suggestions)})")

    seven_cal = next((s for s in suggestions if s["club"] in ("7i", "7-iron", "7I")), None)
    check(seven_cal is not None, "7-iron calibration suggestion exists")
    if seven_cal:
        cal_delta = abs(seven_cal["delta"])
        cal_shots = seven_cal["shot_count"]
        cal_avg = seven_cal["avg_carry"]
        cal_profile = seven_cal["profile_carry"]
        check(cal_delta >= 5, f"7-iron calibration |delta| >= 5 (got {cal_delta})")
        check(cal_shots >= 5, f"7-iron calibration shot_count >= 5 (got {cal_shots})")
        check(cal_avg < cal_profile,
              f"Calibration avg_carry ({cal_avg}) < profile_carry ({cal_profile})")
        print(f"  → 7-iron calibration: profile={cal_profile}yd → actual={cal_avg}yd "
              f"(delta={seven_cal['delta']}yd, {cal_shots} shots)")

    # ------------------------------------------------------------------ #
    header("Step 10: Session 2 — pre-round briefing references round 1 data")
    r = client.post("/session", json={
        "profile": make_profile(display_name),
        "model": "gpt-4o",
        "max_tokens": 512,
    })
    check(r.status_code == 200, f"Session 2 create returned 200 (got {r.status_code})")
    sess2 = r.json()
    session_id_2 = sess2["session_id"]
    briefing_2 = sess2.get("briefing", "")
    check(bool(briefing_2), "Session 2 briefing is non-empty")
    check(len(briefing_2) > 30, f"Session 2 briefing is substantive (len={len(briefing_2)})")
    print(f"  → session 2 briefing: {briefing_2[:120]}...")

    # ------------------------------------------------------------------ #
    header("Step 11: Advice in session 2")
    r = client.post("/advice", json={
        "session_id": session_id_2,
        "text": "I'm at Pebble Beach, 160 out to the pin, wind helping from behind",
    })
    check(r.status_code == 200, f"Session 2 advice returned 200 (got {r.status_code})")
    advice2 = r.json().get("text", "")
    check(len(advice2) > 10, f"Session 2 advice is substantive (len={len(advice2)})")
    print(f"  → advice preview: {advice2[:80]}...")

    # ------------------------------------------------------------------ #
    header("Summary")
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n  {PASS_COUNT}/{total} checks passed", end="")
    if FAIL_COUNT == 0:
        print(" — ALL PASS ✓")
    else:
        print(f" — {FAIL_COUNT} FAILED ✗")
        sys.exit(1)


if __name__ == "__main__":
    main()
