"""Golfer profile schema and management."""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class ClubDistance(BaseModel):
    carry: int = Field(description="Carry distance in yards")
    total: int = Field(description="Total distance including roll in yards")


class Tendencies(BaseModel):
    under_pressure: str = Field(default="", description="Tendency under pressure")
    back_nine: str = Field(default="", description="Tendency on back 9 / fatigue")
    wind: str = Field(default="", description="Tendency in windy conditions")
    general: str = Field(default="", description="General miss tendency")


class PhysicalProfile(BaseModel):
    gender: str = Field(default="", description="male or female")
    age_group: str = Field(default="", description="e.g., 'athletic 30s', 'active senior'")
    driver_clubhead_speed_mph: Optional[float] = Field(
        default=None, description="Driver clubhead speed in mph"
    )
    workout_frequency: str = Field(default="", description="e.g., '2x/week', 'daily'")
    practice_frequency: str = Field(default="", description="e.g., '2x/week range sessions'")


class GolferProfile(BaseModel):
    name: str
    handicap: float = Field(ge=0, le=54)
    shot_shape: str = Field(description="Primary shot shape: fade, draw, straight")
    handed: str = Field(description="right or left")
    chat_style: str = Field(
        default="casual",
        description="Communication preference: casual, detailed, minimal",
    )
    model_selection: str = Field(
        default="gpt_wrapper",
        description="Model provider preference: gpt_wrapper or private_model",
    )
    target_score: Optional[int] = Field(
        default=None, description="Score goal for the round (e.g., 79 to break 80)"
    )
    clubs: dict[str, ClubDistance] = Field(description="Club bag with distances")
    tendencies: Tendencies = Field(default_factory=Tendencies)
    physical: PhysicalProfile = Field(default_factory=PhysicalProfile)

    def get_club_for_distance(self, target_yards: int, use_carry: bool = True) -> tuple[str, ClubDistance]:
        """Find the best club for a target distance.
        Returns (club_name, club_distances)."""
        key = "carry" if use_carry else "total"
        best_club = None
        best_dist = None
        best_diff = float("inf")

        for club_name, dist in self.clubs.items():
            d = getattr(dist, key)
            diff = abs(d - target_yards)
            if diff < best_diff:
                best_diff = diff
                best_club = club_name
                best_dist = dist

        return best_club, best_dist

    def get_clubs_near_distance(
        self, target_yards: int, margin: int = 15, use_carry: bool = True
    ) -> list[tuple[str, ClubDistance, int]]:
        """Find clubs within margin of target distance.
        Returns list of (club_name, club_distances, difference)."""
        key = "carry" if use_carry else "total"
        results = []
        for club_name, dist in self.clubs.items():
            d = getattr(dist, key)
            diff = d - target_yards
            if abs(diff) <= margin:
                results.append((club_name, dist, diff))
        results.sort(key=lambda x: abs(x[2]))
        return results

    def club_list_summary(self) -> str:
        """Readable summary of the bag for prompts."""
        lines = []
        for club, dist in self.clubs.items():
            lines.append(f"  {club}: {dist.carry}yd carry / {dist.total}yd total")
        return "\n".join(lines)


def load_profile(path: Path) -> GolferProfile:
    """Load a golfer profile from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    return GolferProfile(**data)


def save_profile(profile: GolferProfile, path: Path) -> None:
    """Save a golfer profile to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(profile.model_dump(), f, indent=2)
