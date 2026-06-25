"""Golf calculation tools: wind, altitude, temperature, lie adjustments.

All functions use yards and Fahrenheit (standard US golf units).
These calculations are based on established golf physics:
- TrackMan data for wind effects
- PGA Tour altitude studies
- USGA ball testing data for temperature
"""

import math
from dataclasses import dataclass


@dataclass
class WindEffect:
    """Result of wind calculation relative to shot line."""
    headwind_component: float  # positive = into wind, negative = helping
    crosswind_component: float  # positive = left-to-right, negative = right-to-left
    carry_adjustment: float  # yards to add (negative) or subtract (positive)
    lateral_drift: float  # yards of sideways drift (positive = right)
    description: str


@dataclass
class ShotAdjustment:
    """Combined adjustment result for a shot."""
    raw_distance: float
    effective_distance: float
    wind_adjust: float
    temp_adjust: float
    altitude_adjust: float
    lie_adjust: float
    lateral_drift: float
    breakdown: str


def wind_relative_to_shot(
    wind_deg: float, shot_bearing: float, wind_speed_mph: float
) -> WindEffect:
    """Convert compass wind direction to headwind/crosswind relative to shot.

    Args:
        wind_deg: Wind COMING FROM direction in degrees (meteorological convention).
                  0=North, 90=East, 180=South, 270=West.
        shot_bearing: Direction golfer is hitting TOWARD in degrees.
        wind_speed_mph: Wind speed in mph.
    """
    # Wind is coming FROM wind_deg, so it's going TO (wind_deg + 180)
    # Angle between wind direction and shot direction
    relative_angle = math.radians(wind_deg - shot_bearing)

    # Headwind component: positive = into the wind
    headwind = wind_speed_mph * math.cos(relative_angle)

    # Crosswind component: positive = left-to-right (for right-handed golfer facing shot_bearing)
    crosswind = wind_speed_mph * math.sin(relative_angle)

    carry_adj = adjust_for_wind_component(headwind)
    # Crosswind lateral drift: ~1 yard per 1mph of crosswind per 150 yards
    lateral = crosswind * 0.7

    if abs(headwind) < 2:
        hw_desc = "calm"
    elif headwind > 0:
        hw_desc = f"{abs(headwind):.0f}mph into"
    else:
        hw_desc = f"{abs(headwind):.0f}mph helping"

    if abs(crosswind) < 2:
        cw_desc = ""
    elif crosswind > 0:
        cw_desc = f", {abs(crosswind):.0f}mph left-to-right"
    else:
        cw_desc = f", {abs(crosswind):.0f}mph right-to-left"

    return WindEffect(
        headwind_component=headwind,
        crosswind_component=crosswind,
        carry_adjustment=carry_adj,
        lateral_drift=lateral,
        description=f"{hw_desc}{cw_desc}",
    )


def adjust_for_wind_component(headwind_mph: float) -> float:
    """Calculate carry distance adjustment from headwind/tailwind.

    Based on TrackMan data:
    - Headwind: ~1% distance loss per 1mph for mid-irons
    - Tailwind: ~0.5% distance gain per 1mph (less effect than headwind)

    Returns yards to ADD to effective distance (negative = ball goes shorter).
    Positive headwind = into the wind.
    """
    if headwind_mph > 0:
        return headwind_mph * 1.0  # add yards to effective (need more club)
    else:
        return headwind_mph * 0.5  # subtract from effective (need less club)


def adjust_for_altitude(distance: float, altitude_ft: float) -> float:
    """Adjust distance for altitude. Thinner air = less drag = more carry.

    Rule of thumb: ~2% more distance per 1,000ft above sea level.
    Returns yards to SUBTRACT from effective distance (ball goes further).
    """
    adjustment = distance * (altitude_ft / 1000.0) * 0.02
    return -adjustment  # negative because ball goes further, need less club


def adjust_for_temperature(distance: float, temp_f: float) -> float:
    """Adjust for temperature. Cold air is denser = more drag.

    Rule of thumb: ~1 yard per 5°F deviation from 75°F baseline.
    Below 75 = ball goes shorter. Above 75 = ball goes further.
    Returns yards to ADD to effective distance.
    """
    deviation = 75.0 - temp_f
    return deviation / 5.0


LIE_ADJUSTMENTS = {
    "fairway": 0.0,
    "tee": 0.0,
    "light_rough": -0.05,
    "rough": -0.10,
    "deep_rough": -0.20,
    "fairway_bunker": -0.10,
    "greenside_bunker": 0.0,  # different shot entirely
    "hardpan": 0.0,
    "divot": -0.08,
    "pine_straw": -0.05,
    "uphill": 0.05,  # per degree, but simplified as flat percentage
    "downhill": -0.05,
    "sidehill_above": -0.03,
    "sidehill_below": -0.03,
}


def adjust_for_lie(distance: float, lie_type: str) -> float:
    """Adjust distance for ball lie.

    Returns yards to ADD to effective distance (positive = need more club).
    """
    factor = LIE_ADJUSTMENTS.get(lie_type, 0.0)
    return distance * (-factor)  # negate: rough makes ball go shorter, so add yards


def effective_distance(
    raw_distance: float,
    wind_speed_mph: float = 0,
    wind_deg: float = 0,
    shot_bearing: float = 0,
    temp_f: float = 75,
    altitude_ft: float = 0,
    lie_type: str = "fairway",
) -> ShotAdjustment:
    """Calculate effective "plays-like" distance combining all factors.

    The effective distance tells you how far the shot PLAYS LIKE,
    so you pick a club that carries that effective distance.
    """
    wind_effect = wind_relative_to_shot(wind_deg, shot_bearing, wind_speed_mph)
    wind_adj = wind_effect.carry_adjustment
    temp_adj = adjust_for_temperature(raw_distance, temp_f)
    alt_adj = adjust_for_altitude(raw_distance, altitude_ft)
    lie_adj = adjust_for_lie(raw_distance, lie_type)

    eff = raw_distance + wind_adj + temp_adj + alt_adj + lie_adj

    parts = [f"Raw: {raw_distance:.0f}yd"]
    if abs(wind_adj) >= 1:
        parts.append(f"Wind: {wind_adj:+.0f}yd ({wind_effect.description})")
    if abs(temp_adj) >= 1:
        parts.append(f"Temp ({temp_f:.0f}°F): {temp_adj:+.0f}yd")
    if abs(alt_adj) >= 1:
        parts.append(f"Altitude ({altitude_ft:.0f}ft): {alt_adj:+.0f}yd")
    if abs(lie_adj) >= 1:
        parts.append(f"Lie ({lie_type}): {lie_adj:+.0f}yd")
    parts.append(f"Effective: {eff:.0f}yd")

    if abs(wind_effect.lateral_drift) >= 1:
        drift_dir = "right" if wind_effect.lateral_drift > 0 else "left"
        parts.append(
            f"Lateral drift: {abs(wind_effect.lateral_drift):.0f}yd {drift_dir}"
        )

    return ShotAdjustment(
        raw_distance=raw_distance,
        effective_distance=eff,
        wind_adjust=wind_adj,
        temp_adjust=temp_adj,
        altitude_adjust=alt_adj,
        lie_adjust=lie_adj,
        lateral_drift=wind_effect.lateral_drift,
        breakdown=" | ".join(parts),
    )
