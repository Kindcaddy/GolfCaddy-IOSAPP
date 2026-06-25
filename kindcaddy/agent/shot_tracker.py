"""ShotTrackerTool - tracks shot outcomes and detects patterns.

CLI: manual input via /shot command.
iOS (future): GPS-based automatic tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .base import Alert

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState


@dataclass
class ShotRecord:
    hole: int
    club: str
    intended_distance: Optional[float] = None
    actual_distance: Optional[float] = None
    miss_direction: Optional[str] = None  # "left", "right", "short", "long"
    lie: str = "fairway"
    notes: str = ""
    profile_carry: Optional[float] = None  # auto-filled from golfer profile

    @property
    def distance_diff(self) -> Optional[float]:
        """Difference between actual and expected distance.
        Uses intended_distance if set, otherwise falls back to profile carry."""
        expected = self.intended_distance or self.profile_carry
        if expected and self.actual_distance:
            return self.actual_distance - expected
        return None


class ShotTrackerTool:
    """Tracks shot outcomes and detects performance patterns mid-round."""

    name: str = "shot_tracker"

    def __init__(self, pattern_threshold: int = 3):
        self.shots: list[ShotRecord] = []
        self.pattern_threshold = pattern_threshold
        self._last_alert_shot_count = 0

    def log_shot(self, shot: ShotRecord) -> None:
        self.shots.append(shot)

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """Analyze recent shots for patterns."""
        if len(self.shots) < self.pattern_threshold:
            return None
        if len(self.shots) <= self._last_alert_shot_count:
            return None

        alerts = []

        distance_pattern = self._check_distance_pattern()
        if distance_pattern:
            alerts.append(distance_pattern)

        direction_pattern = self._check_direction_pattern()
        if direction_pattern:
            alerts.append(direction_pattern)

        if not alerts:
            return None

        self._last_alert_shot_count = len(self.shots)

        return Alert(
            source="shot_tracker",
            priority="medium",
            message=" ".join(alerts),
            data={
                "total_shots": len(self.shots),
                "recent_shots": [
                    {
                        "club": s.club,
                        "diff": s.distance_diff,
                        "miss": s.miss_direction,
                    }
                    for s in self.shots[-5:]
                ],
            },
        )

    def _check_distance_pattern(self) -> Optional[str]:
        """Check if recent iron shots are consistently long or short."""
        iron_clubs = {"5i", "6i", "7i", "8i", "9i", "PW"}
        recent_irons = [
            s for s in self.shots[-6:]
            if s.club in iron_clubs and s.distance_diff is not None
        ]

        if len(recent_irons) < self.pattern_threshold:
            return None

        avg_diff = sum(s.distance_diff for s in recent_irons) / len(recent_irons)

        if abs(avg_diff) >= 5:
            direction = "short" if avg_diff < 0 else "long"
            return (
                f"Pattern detected: your last {len(recent_irons)} iron shots "
                f"averaged {abs(avg_diff):.0f} yards {direction} of target. "
                f"Adjusting club recommendations accordingly."
            )
        return None

    def _check_direction_pattern(self) -> Optional[str]:
        """Check if recent shots are consistently missing in one direction."""
        recent_with_miss = [
            s for s in self.shots[-5:]
            if s.miss_direction and s.miss_direction in ("left", "right")
        ]

        if len(recent_with_miss) < self.pattern_threshold:
            return None

        directions = [s.miss_direction for s in recent_with_miss]
        right_count = directions.count("right")
        left_count = directions.count("left")

        dominant = max(right_count, left_count)
        if dominant >= self.pattern_threshold:
            miss_dir = "right" if right_count > left_count else "left"
            return (
                f"Pattern detected: {dominant} of your last {len(recent_with_miss)} "
                f"shots missed {miss_dir}. Consider adjusting your aim."
            )
        return None

    def get_distance_adjustment(self) -> float:
        """Get suggested yardage adjustment based on recent performance."""
        iron_clubs = {"5i", "6i", "7i", "8i", "9i", "PW"}
        recent_irons = [
            s for s in self.shots[-6:]
            if s.club in iron_clubs and s.distance_diff is not None
        ]
        if len(recent_irons) < 3:
            return 0.0
        avg_diff = sum(s.distance_diff for s in recent_irons) / len(recent_irons)
        if abs(avg_diff) >= 5:
            return -avg_diff  # if hitting 5 short, add 5 to effective distance
        return 0.0

    def get_round_summary(self) -> dict:
        """Generate shot statistics for the round."""
        if not self.shots:
            return {"total_shots": 0}

        clubs_used = {}
        total_diff = []
        misses = {"left": 0, "right": 0, "short": 0, "long": 0}

        for s in self.shots:
            clubs_used[s.club] = clubs_used.get(s.club, 0) + 1
            if s.distance_diff is not None:
                total_diff.append(s.distance_diff)
            if s.miss_direction:
                misses[s.miss_direction] = misses.get(s.miss_direction, 0) + 1

        return {
            "total_shots": len(self.shots),
            "clubs_used": clubs_used,
            "avg_distance_diff": sum(total_diff) / len(total_diff) if total_diff else 0,
            "miss_directions": misses,
        }

    def execute(self, params: dict) -> dict:
        return self.get_round_summary()

    def reset(self) -> None:
        self.shots.clear()
        self._last_alert_shot_count = 0
