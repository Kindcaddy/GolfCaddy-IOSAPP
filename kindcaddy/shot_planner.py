"""Deterministic shot planning for club recommendation.

This module computes a candidate club and plays-like distance in Python so the
LLM can focus on narration and tone instead of arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .profile import GolferProfile
from .round_state import RoundState


@dataclass
class DeterministicShotPlan:
    """Computed shot plan produced by deterministic rules."""

    raw_distance_yards: int
    plays_like_yards: int
    recommended_club: str
    recommended_club_carry: int
    lie: str
    wind_kind: str
    applied_adjustments: dict[str, float]


def build_shot_plan(
    user_text: str,
    round_state: RoundState,
    profile: GolferProfile,
    user_insights: Optional[dict] = None,
    fatigue_adjustment_yards: float = 0.0,
) -> Optional[DeterministicShotPlan]:
    """Build a deterministic club recommendation from free-text shot input.

    Returns None when no reliable yardage is found in user text.
    """
    raw_distance = _extract_distance_yards(user_text)
    if raw_distance is None:
        return None

    lie = _extract_lie(user_text)
    wind_kind = _extract_wind_kind(user_text)
    adjustments = _compute_adjustments(
        raw_distance=raw_distance,
        round_state=round_state,
        lie=lie,
        wind_kind=wind_kind,
        fatigue_adjustment_yards=fatigue_adjustment_yards,
    )

    plays_like = int(round(raw_distance + sum(adjustments.values())))
    club_name, club_carry = _select_club_for_distance(
        profile=profile,
        target_yards=plays_like,
        user_insights=user_insights,
        lie=lie,
    )

    return DeterministicShotPlan(
        raw_distance_yards=raw_distance,
        plays_like_yards=plays_like,
        recommended_club=club_name,
        recommended_club_carry=club_carry,
        lie=lie,
        wind_kind=wind_kind,
        applied_adjustments=adjustments,
    )


def _extract_distance_yards(user_text: str) -> Optional[int]:
    # Capture numbers followed by "yd", "yards", or "out".
    explicit = re.search(r"\b(\d{2,3})\s*(?:yds?|yards?|out)\b", user_text, re.IGNORECASE)
    if explicit:
        return int(explicit.group(1))

    # Common voice phrasing: "155 to the pin", "160 to hole", "145 left".
    relative = re.search(
        r"\b(\d{2,3})\s*(?:to|left|remaining|to the pin|to pin|to hole)\b",
        user_text,
        re.IGNORECASE,
    )
    if relative:
        return int(relative.group(1))

    # Fallback: first standalone 2-3 digit number in typical approach-shot range.
    generic = re.search(r"\b(\d{2,3})\b", user_text)
    if generic:
        value = int(generic.group(1))
        if 60 <= value <= 260:
            return value
    return None


def _extract_lie(user_text: str) -> str:
    text = user_text.lower()
    if "fairway bunker" in text or "bunker" in text:
        return "fairway_bunker"
    if "deep rough" in text or "thick rough" in text:
        return "deep_rough"
    if "rough" in text:
        return "light_rough"
    if "hardpan" in text or "bare lie" in text:
        return "hardpan"
    if "downhill lie" in text:
        return "downhill_lie"
    if "uphill lie" in text:
        return "uphill_lie"
    return "fairway"


def _extract_wind_kind(user_text: str) -> str:
    text = user_text.lower()
    head_terms = ("headwind", "into me", "into us", "into the wind", "in our face", "against")
    tail_terms = ("tailwind", "from behind", "helping", "downwind")
    cross_terms = ("left to right", "right to left", "crosswind", "across")

    if any(term in text for term in head_terms):
        return "head"
    if any(term in text for term in tail_terms):
        return "tail"
    if any(term in text for term in cross_terms):
        return "cross"
    return "unknown"


def _compute_adjustments(
    *,
    raw_distance: int,
    round_state: RoundState,
    lie: str,
    wind_kind: str,
    fatigue_adjustment_yards: float,
) -> dict[str, float]:
    adjustments: dict[str, float] = {}

    # Wind: only apply head/tail to distance; crosswind is handled in aim.
    if round_state.wind_speed_mph > 0 and wind_kind in {"head", "tail"}:
        if wind_kind == "head":
            adjustments["wind"] = raw_distance * 0.01 * round_state.wind_speed_mph
        else:
            adjustments["wind"] = -(raw_distance * 0.005 * round_state.wind_speed_mph)

    # Temperature: ~2 yards per 10F away from 70F baseline.
    if round_state.temp_f:
        adjustments["temperature"] = -((round_state.temp_f - 70.0) / 10.0) * 2.0

    # Altitude: ball flies farther at altitude => plays shorter.
    if round_state.altitude_ft > 0:
        adjustments["altitude"] = -(raw_distance * 0.02 * (round_state.altitude_ft / 1000.0))

    lie_map = {
        "fairway": 0.0,
        "light_rough": 4.0,
        "deep_rough": max(10.0, raw_distance * 0.12),
        "fairway_bunker": 12.0,
        "hardpan": 3.0,
        "uphill_lie": 5.0,
        "downhill_lie": -4.0,
    }
    adjustments["lie"] = lie_map.get(lie, 0.0)

    if fatigue_adjustment_yards:
        adjustments["fatigue"] = max(0.0, float(fatigue_adjustment_yards))

    return adjustments


def _select_club_for_distance(
    *,
    profile: GolferProfile,
    target_yards: int,
    user_insights: Optional[dict],
    lie: str,
) -> tuple[str, int]:
    # Build effective carry per club using optional memory adjustments.
    effective_carries: list[tuple[str, int]] = []
    club_actuals = (user_insights or {}).get("club_actuals", {})
    lie_deltas = (user_insights or {}).get("club_lie_deltas", {})

    for club_name, dist in profile.clubs.items():
        carry = float(dist.carry)

        club_info = club_actuals.get(club_name) or {}
        global_delta = club_info.get("delta")
        if isinstance(global_delta, (int, float)) and club_info.get("shot_count", 0) >= 5:
            # delta is actual - profile; negative means shorter in reality.
            carry += float(global_delta)

        by_lie = lie_deltas.get(club_name) or {}
        lie_info = by_lie.get(lie) or {}
        lie_delta = lie_info.get("avg_delta")
        if isinstance(lie_delta, (int, float)) and lie_info.get("n", 0) >= 6:
            carry += float(lie_delta)

        effective_carries.append((club_name, int(round(carry))))

    effective_carries.sort(key=lambda item: item[1])

    if not effective_carries:
        # Defensive fallback; profile normally always has clubs.
        return "Unknown club", target_yards

    lower = [c for c in effective_carries if c[1] <= target_yards]
    higher = [c for c in effective_carries if c[1] > target_yards]

    if lower and higher:
        low = lower[-1]
        high = higher[0]
        # Conservative tie-breaker: avoid long misses by default.
        if abs(low[1] - target_yards) <= abs(high[1] - target_yards):
            return low
        return high
    if lower:
        return lower[-1]
    return higher[0]
