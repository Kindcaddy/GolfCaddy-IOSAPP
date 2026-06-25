"""WeatherTool - monitors weather conditions and detects changes.

Uses Open-Meteo API (free, no API key required) for automatic weather fetching.
CLI can also set weather manually.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import httpx

from .base import Alert

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState


@dataclass
class WeatherSnapshot:
    temp_f: float = 75.0
    humidity: int = 50
    wind_speed_mph: float = 0.0
    wind_deg: float = 0.0
    wind_gust_mph: float = 0.0
    description: str = "clear"
    timestamp: float = 0.0

    def summary(self) -> str:
        parts = [f"{self.temp_f:.0f}°F"]
        if self.wind_speed_mph > 2:
            direction = _compass_label(self.wind_deg)
            parts.append(f"wind {self.wind_speed_mph:.0f}mph from {direction}")
            if self.wind_gust_mph > self.wind_speed_mph + 3:
                parts.append(f"gusts {self.wind_gust_mph:.0f}mph")
        parts.append(f"{self.humidity}% humidity")
        parts.append(self.description)
        return ", ".join(parts)


def _compass_label(deg: float) -> str:
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(deg / 45) % 8
    return directions[idx]


class WeatherTool:
    """Monitors weather and detects meaningful changes mid-round."""

    name: str = "weather"

    def __init__(self, check_interval_interactions: int = 6, cache_ttl: int = 60):
        self.check_interval = check_interval_interactions
        self.cache_ttl = cache_ttl
        self._current: Optional[WeatherSnapshot] = None
        self._previous: Optional[WeatherSnapshot] = None
        self._interaction_count = 0
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._last_fetch_time: float = 0.0

    def set_location(self, lat: float, lon: float) -> None:
        self._lat = lat
        self._lon = lon

    def set_weather_manual(
        self,
        temp_f: float = 75,
        wind_speed_mph: float = 0,
        wind_deg: float = 0,
        wind_gust_mph: float = 0,
        humidity: int = 50,
        description: str = "clear",
    ) -> WeatherSnapshot:
        """Manually set weather (for CLI without API key)."""
        self._previous = self._current
        self._current = WeatherSnapshot(
            temp_f=temp_f,
            humidity=humidity,
            wind_speed_mph=wind_speed_mph,
            wind_deg=wind_deg,
            wind_gust_mph=wind_gust_mph,
            description=description,
            timestamp=time.time(),
        )
        return self._current

    _WMO_DESCRIPTIONS: dict[int, str] = {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "foggy", 48: "depositing rime fog",
        51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
        61: "slight rain", 63: "moderate rain", 65: "heavy rain",
        71: "slight snow", 73: "moderate snow", 75: "heavy snow",
        80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
        95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
    }

    async def fetch_weather(self) -> Optional[WeatherSnapshot]:
        """Fetch weather from Open-Meteo API (free, no key required)."""
        if self._lat is None:
            return self._current

        if self._current and (time.time() - self._last_fetch_time) < self.cache_ttl:
            return self._current

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": self._lat,
            "longitude": self._lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()

            cur = data["current"]
            wmo_code = cur.get("weather_code", 0)

            self._previous = self._current
            self._current = WeatherSnapshot(
                temp_f=cur["temperature_2m"],
                humidity=int(cur["relative_humidity_2m"]),
                wind_speed_mph=cur["wind_speed_10m"],
                wind_deg=cur["wind_direction_10m"],
                wind_gust_mph=cur.get("wind_gusts_10m", 0) or 0,
                description=self._WMO_DESCRIPTIONS.get(wmo_code, "unknown"),
                timestamp=time.time(),
            )
            self._last_fetch_time = time.time()
            return self._current
        except Exception:
            logger.exception("Failed to fetch weather from Open-Meteo")
            return self._current

    @property
    def current(self) -> Optional[WeatherSnapshot]:
        return self._current

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """Check for meaningful weather changes."""
        self._interaction_count += 1

        if not self._current or not self._previous:
            return None

        alerts = []

        wind_speed_change = abs(self._current.wind_speed_mph - self._previous.wind_speed_mph)
        if wind_speed_change >= 5:
            alerts.append(
                f"Wind speed changed by {wind_speed_change:.0f}mph "
                f"(was {self._previous.wind_speed_mph:.0f}, now {self._current.wind_speed_mph:.0f})"
            )

        wind_dir_change = abs(self._current.wind_deg - self._previous.wind_deg)
        if wind_dir_change > 180:
            wind_dir_change = 360 - wind_dir_change
        if wind_dir_change >= 30 and self._current.wind_speed_mph >= 5:
            old_dir = _compass_label(self._previous.wind_deg)
            new_dir = _compass_label(self._current.wind_deg)
            alerts.append(f"Wind direction shifted from {old_dir} to {new_dir}")

        temp_change = abs(self._current.temp_f - self._previous.temp_f)
        if temp_change >= 10:
            alerts.append(
                f"Temperature changed by {temp_change:.0f}°F "
                f"(was {self._previous.temp_f:.0f}, now {self._current.temp_f:.0f})"
            )

        if not alerts:
            return None

        return Alert(
            source="weather",
            priority="medium",
            message=". ".join(alerts) + ". Adjusting recommendations.",
            data={
                "current": {
                    "temp_f": self._current.temp_f,
                    "wind_speed": self._current.wind_speed_mph,
                    "wind_deg": self._current.wind_deg,
                },
                "previous": {
                    "temp_f": self._previous.temp_f,
                    "wind_speed": self._previous.wind_speed_mph,
                    "wind_deg": self._previous.wind_deg,
                },
            },
        )

    def execute(self, params: dict) -> dict:
        """Get current weather data."""
        if self._current:
            return {
                "temp_f": self._current.temp_f,
                "wind_speed_mph": self._current.wind_speed_mph,
                "wind_deg": self._current.wind_deg,
                "wind_gust_mph": self._current.wind_gust_mph,
                "humidity": self._current.humidity,
                "description": self._current.description,
                "summary": self._current.summary(),
            }
        return {"error": "No weather data available. Use /weather to set conditions."}

    def reset(self) -> None:
        self._current = None
        self._previous = None
        self._interaction_count = 0
