"""Round state tracker - shared state for all agent tools.

Tracks everything about the current round: hole, score, shots, weather, patterns.
All agent tools read from and write to this shared state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .profile import GolferProfile


@dataclass
class RoundState:
    """Shared state for the current round. All agent tools reference this."""

    profile: Optional[GolferProfile] = None
    current_hole: Optional[int] = None
    target_score: Optional[int] = None

    # Weather (updated by WeatherTool)
    temp_f: float = 75.0
    wind_speed_mph: float = 0.0
    wind_deg: float = 0.0
    wind_gust_mph: float = 0.0
    humidity: int = 50
    altitude_ft: float = 0.0
    weather_description: str = ""

    # Conversation history for context
    conversation: list[dict] = field(default_factory=list)

    # Round active flag
    is_active: bool = False

    # True once weather has been received from WeatherKit or set manually
    weather_received: bool = False

    def start_round(self, profile: GolferProfile) -> None:
        """Initialize state for a new round."""
        self.profile = profile
        self.current_hole = 1
        self.target_score = profile.target_score
        self.conversation = []
        self.is_active = True
        self.weather_received = False

    def set_hole(self, hole: int) -> None:
        if 1 <= hole <= 18:
            self.current_hole = hole

    def update_weather(
        self,
        temp_f: float,
        wind_speed_mph: float,
        wind_deg: float,
        wind_gust_mph: float = 0,
        humidity: int = 50,
        description: str = "",
    ) -> None:
        self.temp_f = temp_f
        self.wind_speed_mph = wind_speed_mph
        self.wind_deg = wind_deg
        self.wind_gust_mph = wind_gust_mph
        self.humidity = humidity
        self.weather_description = description
        self.weather_received = True

    def set_altitude(self, altitude_ft: float) -> None:
        self.altitude_ft = altitude_ft

    def add_message(self, role: str, content: str) -> None:
        """Add a message to conversation history."""
        self.conversation.append({"role": role, "content": content})
        # Keep conversation manageable (last 40 messages)
        if len(self.conversation) > 40:
            self.conversation = self.conversation[-40:]

    def get_conditions_summary(self) -> str:
        """Readable summary of current conditions for prompts.

        Includes shot-impact notes so the model knows what each condition means
        for club selection without having to re-derive it.
        """
        from .agent.weather_tool import _compass_label

        parts = []

        if self.temp_f:
            temp_delta = round((self.temp_f - 70) / 10 * 2)
            if temp_delta > 0:
                temp_note = f" (+{temp_delta}yd carry vs baseline — ball flies farther in heat)"
            elif temp_delta < 0:
                temp_note = f" ({temp_delta}yd carry vs baseline — ball flies shorter in cold)"
            else:
                temp_note = ""
            parts.append(f"Temperature: {self.temp_f:.0f}°F{temp_note}")

        if self.wind_speed_mph > 0:
            direction = _compass_label(self.wind_deg)
            parts.append(f"Wind: {self.wind_speed_mph:.0f}mph from {direction}")
            if self.wind_gust_mph > self.wind_speed_mph + 3:
                parts.append(f"Gusts: {self.wind_gust_mph:.0f}mph")
            # Headwind/tailwind impact at typical iron distance (150yd)
            headwind_add = round(self.wind_speed_mph * 0.01 * 150)
            tailwind_sub = round(self.wind_speed_mph * 0.005 * 150)
            parts.append(
                f"Wind impact at 150yd: headwind adds ~{headwind_add}yd plays-like distance, "
                f"tailwind removes ~{tailwind_sub}yd. Crosswind drifts ~{round(self.wind_speed_mph / 5 * 1.5):.0f}yd "
                f"per 150yd — aim into the wind."
            )

        if self.humidity:
            parts.append(f"Humidity: {self.humidity}%")

        if self.altitude_ft > 0:
            alt_pct = round(self.altitude_ft / 1000 * 2)
            parts.append(
                f"Altitude: {self.altitude_ft:.0f}ft "
                f"(+{alt_pct}% carry — club DOWN, ball flies farther)"
            )

        if self.weather_description:
            parts.append(f"Conditions: {self.weather_description}")

        return "\n".join(parts) if (parts and self.weather_received) else "No conditions set."

    def get_round_state_summary(self) -> str:
        """Readable summary of round progress for prompts."""
        parts = []
        if self.current_hole:
            parts.append(f"Current hole: {self.current_hole}")
        if self.target_score:
            parts.append(f"Target score: {self.target_score}")
        if not self.is_active:
            parts.append("Round not started.")
        return "\n".join(parts) if parts else "Round not started."

    def get_profile_summary(self) -> str:
        """Readable profile summary for prompts."""
        if not self.profile:
            return "No profile loaded."

        p = self.profile
        lines = [
            f"Name: {p.name}",
            f"Handicap: {p.handicap}",
            f"Shot shape: {p.shot_shape} ({p.handed}-handed)",
        ]
        if p.physical.gender:
            lines.append(f"Gender: {p.physical.gender}")
        if p.physical.age_group:
            lines.append(f"Build: {p.physical.age_group}")
        if p.physical.driver_clubhead_speed_mph:
            lines.append(f"Driver clubhead speed: {p.physical.driver_clubhead_speed_mph:.0f}mph")
        if p.physical.workout_frequency:
            lines.append(f"Workout frequency: {p.physical.workout_frequency}")
        if p.physical.practice_frequency:
            lines.append(f"Practice: {p.physical.practice_frequency}")
        lines.append("Club distances:")
        lines.append(p.club_list_summary())
        if p.tendencies.under_pressure:
            lines.append(f"Under pressure: {p.tendencies.under_pressure}")
        if p.tendencies.back_nine:
            lines.append(f"Back 9: {p.tendencies.back_nine}")
        if p.tendencies.wind:
            lines.append(f"In wind: {p.tendencies.wind}")
        if p.tendencies.general:
            lines.append(f"General tendency: {p.tendencies.general}")
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all round state."""
        self.current_hole = None
        self.target_score = self.profile.target_score if self.profile else None
        self.conversation = []
        self.is_active = False
