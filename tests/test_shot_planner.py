"""Unit tests for deterministic shot planner logic."""

from __future__ import annotations

from kindcaddy.profile import GolferProfile
from kindcaddy.round_state import RoundState
from kindcaddy.shot_planner import build_shot_plan


def _profile() -> GolferProfile:
    return GolferProfile(
        name="Planner Tester",
        handicap=12.0,
        shot_shape="fade",
        handed="right",
        clubs={
            "8i": {"carry": 145, "total": 154},
            "7i": {"carry": 155, "total": 165},
            "6i": {"carry": 165, "total": 176},
        },
    )


def test_headwind_adjustment_picks_longer_club():
    rs = RoundState()
    rs.update_weather(temp_f=70, wind_speed_mph=10, wind_deg=180, description="windy")

    plan = build_shot_plan(
        user_text="155 out, slight headwind into me",
        round_state=rs,
        profile=_profile(),
        user_insights=None,
    )

    assert plan is not None
    assert plan.plays_like_yards > plan.raw_distance_yards
    assert plan.recommended_club == "6i"


def test_fatigue_adjustment_can_shift_candidate_club():
    rs = RoundState()
    rs.update_weather(temp_f=70, wind_speed_mph=0, wind_deg=0, description="clear")

    plan = build_shot_plan(
        user_text="150 out to the pin",
        round_state=rs,
        profile=_profile(),
        user_insights=None,
        fatigue_adjustment_yards=6.0,
    )

    assert plan is not None
    assert plan.plays_like_yards == 156
    assert plan.recommended_club == "7i"


def test_lie_specific_memory_delta_influences_club_pick():
    rs = RoundState()
    rs.update_weather(temp_f=70, wind_speed_mph=0, wind_deg=0, description="clear")
    insights = {
        "club_lie_deltas": {
            "7i": {
                "fairway": {"avg_delta": -15.0, "n": 7},
            }
        }
    }

    plan = build_shot_plan(
        user_text="162 out, clean fairway lie",
        round_state=rs,
        profile=_profile(),
        user_insights=insights,
    )

    assert plan is not None
    assert plan.recommended_club == "6i"


def test_no_reliable_distance_returns_none():
    rs = RoundState()
    rs.update_weather(temp_f=70, wind_speed_mph=5, wind_deg=225, description="breezy")

    plan = build_shot_plan(
        user_text="What do you like here with this wind?",
        round_state=rs,
        profile=_profile(),
        user_insights=None,
    )

    assert plan is None
