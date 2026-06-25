"""FatigueModelTool - estimates energy and adjusts distances.

CLI: uses hole number as a proxy for fatigue.
iOS (future): HealthKit step count + elapsed time + heat index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base import Alert

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState


class FatigueModelTool:
    """Estimates fatigue level and adjusts distance recommendations."""

    name: str = "fatigue"

    def __init__(self):
        self._alerted_turn = False
        self._alerted_late = False
        self._distance_penalty_yards: float = 0.0

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """Check for fatigue-related adjustments."""
        hole = round_state.current_hole
        if hole is None or hole < 2:
            return None

        back_nine_tendency = ""
        if round_state.profile and round_state.profile.tendencies.back_nine:
            back_nine_tendency = round_state.profile.tendencies.back_nine

        # Alert at the turn (hole 10)
        if hole >= 10 and not self._alerted_turn and back_nine_tendency:
            self._alerted_turn = True
            self._distance_penalty_yards = self._estimate_penalty(hole, back_nine_tendency)
            return Alert(
                source="fatigue",
                priority="medium",
                message=(
                    f"Back 9 starting. Your profile notes: '{back_nine_tendency}'. "
                    f"I'm adjusting iron distances by {self._distance_penalty_yards:+.0f} yards "
                    f"for the rest of the round."
                ),
                data={
                    "hole": hole,
                    "adjustment_yards": self._distance_penalty_yards,
                    "tendency": back_nine_tendency,
                },
            )

        # Alert at hole 15+ if penalty should increase
        if hole >= 15 and not self._alerted_late and back_nine_tendency:
            self._alerted_late = True
            late_penalty = self._estimate_penalty(hole, back_nine_tendency)
            if abs(late_penalty) > abs(self._distance_penalty_yards):
                diff = late_penalty - self._distance_penalty_yards
                self._distance_penalty_yards = late_penalty
                return Alert(
                    source="fatigue",
                    priority="low",
                    message=(
                        f"Late-round fatigue: increasing distance adjustment to "
                        f"{self._distance_penalty_yards:+.0f} yards on irons."
                    ),
                    data={
                        "hole": hole,
                        "adjustment_yards": self._distance_penalty_yards,
                    },
                )

        return None

    def _estimate_penalty(self, hole: int, tendency: str) -> float:
        """Estimate distance penalty in yards based on hole and tendency text.

        Parses tendency strings like 'loses 3-5 yards on irons' to extract
        a numeric estimate, then scales by how deep into the back 9 we are.
        """
        import re

        numbers = re.findall(r"(\d+)", tendency)
        if len(numbers) >= 2:
            low, high = int(numbers[0]), int(numbers[1])
            base = (low + high) / 2
        elif len(numbers) == 1:
            base = float(numbers[0])
        else:
            base = 3.0  # default assumption

        # Scale: hole 10 = 60% of penalty, hole 18 = 100%
        progress = min((hole - 9) / 9, 1.0)
        scale = 0.6 + 0.4 * progress

        return base * scale

    def get_current_adjustment(self) -> float:
        """Get current fatigue distance adjustment in yards.
        Positive = need more club (hitting shorter)."""
        return self._distance_penalty_yards

    def execute(self, params: dict) -> dict:
        return {
            "distance_adjustment_yards": self._distance_penalty_yards,
            "alerted_turn": self._alerted_turn,
            "alerted_late": self._alerted_late,
        }

    def reset(self) -> None:
        self._alerted_turn = False
        self._alerted_late = False
        self._distance_penalty_yards = 0.0
