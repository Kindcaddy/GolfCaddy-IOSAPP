"""ScoreCalculatorTool - monitors round progress and triggers strategic advice.

CLI: manual input via /score command.
iOS (future): auto-calculated from shot tracker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .base import Alert

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState


DEFAULT_PARS = [4, 4, 4, 3, 5, 4, 4, 3, 5, 4, 4, 4, 3, 5, 4, 4, 3, 5]


class ScoreCalculatorTool:
    """Monitors scoring and triggers strategic advice at key moments."""

    name: str = "score_calculator"

    def __init__(self, pars: list[int] | None = None):
        self.pars = list(pars) if pars else list(DEFAULT_PARS)
        self.scores: dict[int, int] = {}  # hole_number -> strokes
        self._alerted_moments: set[str] = set()
        self.hole_yardages: dict[int, int] = {}

    def update_par(self, hole: int, par: int) -> None:
        if 1 <= hole <= 18 and 3 <= par <= 5:
            self.pars[hole - 1] = par

    def log_yardage(self, hole: int, yards: int) -> None:
        if 50 <= yards <= 700:
            self.hole_yardages[hole] = yards

    def log_score(self, hole: int, strokes: int) -> None:
        self.scores[hole] = strokes

    @property
    def holes_played(self) -> int:
        return len(self.scores)

    @property
    def total_strokes(self) -> int:
        return sum(self.scores.values())

    @property
    def total_par(self) -> int:
        return sum(self.pars[h - 1] for h in self.scores)

    @property
    def score_vs_par(self) -> int:
        return self.total_strokes - self.total_par

    @property
    def remaining_par(self) -> int:
        played = set(self.scores.keys())
        return sum(p for i, p in enumerate(self.pars) if (i + 1) not in played)

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """Check for strategic scoring moments."""
        if self.holes_played == 0:
            return None

        target = round_state.target_score
        alerts = []

        # Turn report (after hole 9)
        if self.holes_played == 9 and "turn" not in self._alerted_moments:
            self._alerted_moments.add("turn")
            front_score = sum(self.scores.get(h, 0) for h in range(1, 10) if h in self.scores)
            front_par = sum(self.pars[:9])
            vs_par = front_score - front_par
            vs_str = f"{vs_par:+d}" if vs_par != 0 else "even"

            msg = f"Front 9 complete: {front_score} ({vs_str})."
            if target:
                needed_back = target - front_score
                back_par = sum(self.pars[9:])
                back_vs = needed_back - back_par
                back_str = f"{back_vs:+d}" if back_vs != 0 else "even"
                msg += f" To hit your target of {target}, you need {needed_back} on the back ({back_str} par)."

            alerts.append(msg)

        # Closing stretch (holes 15-18)
        if (
            self.holes_played >= 14
            and self.holes_played < 18
            and target
            and "closing" not in self._alerted_moments
        ):
            self._alerted_moments.add("closing")
            remaining_holes = 18 - self.holes_played
            strokes_remaining = target - self.total_strokes
            par_remaining = self.remaining_par

            if strokes_remaining > 0:
                vs_par_needed = strokes_remaining - par_remaining
                alerts.append(
                    f"Closing stretch: {remaining_holes} holes left. "
                    f"You need {strokes_remaining} strokes (par is {par_remaining}) "
                    f"to hit your target of {target}."
                )

        # Target in reach or at risk
        if target and self.holes_played >= 12 and self.holes_played < 18:
            strokes_remaining = target - self.total_strokes
            par_remaining = self.remaining_par
            remaining_holes = 18 - self.holes_played

            moment_key = f"target_{self.holes_played}"
            if moment_key not in self._alerted_moments:
                if strokes_remaining <= par_remaining - 2:
                    self._alerted_moments.add(moment_key)
                    alerts.append(
                        f"You're in great shape -- {strokes_remaining - par_remaining:+d} "
                        f"vs par needed over {remaining_holes} holes. Play smart, protect your score."
                    )
                elif strokes_remaining > par_remaining + 2:
                    self._alerted_moments.add(moment_key)
                    alerts.append(
                        f"Target score is tight -- you need {strokes_remaining} in {remaining_holes} holes "
                        f"(par {par_remaining}). Look for birdie opportunities, be aggressive on par 5s."
                    )

        if not alerts:
            return None

        return Alert(
            source="score_calculator",
            priority="high" if target and self.holes_played >= 14 else "medium",
            message=" ".join(alerts),
            data={
                "holes_played": self.holes_played,
                "total_strokes": self.total_strokes,
                "vs_par": self.score_vs_par,
                "target": target,
            },
        )

    def get_scorecard(self) -> str:
        """Generate a text scorecard."""
        lines = []
        for h in range(1, 19):
            par = self.pars[h - 1]
            if h in self.scores:
                score = self.scores[h]
                diff = score - par
                label = {-2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double"}.get(
                    diff, f"+{diff}" if diff > 0 else str(diff)
                )
                lines.append(f"  Hole {h:2d} (par {par}): {score} ({label})")
            else:
                lines.append(f"  Hole {h:2d} (par {par}): --")

        lines.append(f"\n  Total: {self.total_strokes} ({self.score_vs_par:+d})" if self.scores else "")
        return "\n".join(lines)

    def execute(self, params: dict) -> dict:
        return {
            "holes_played": self.holes_played,
            "total_strokes": self.total_strokes,
            "vs_par": self.score_vs_par,
            "scorecard": self.get_scorecard(),
        }

    def reset(self) -> None:
        self.scores.clear()
        self._alerted_moments.clear()
        self.hole_yardages.clear()
