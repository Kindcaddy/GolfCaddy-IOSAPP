"""Club distance estimation from handicap and swing speed.

Provides "good enough" starting distances for new users so they can start
playing without manually entering every club. Users can refine later.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Lookup table: (min_hcp, max_hcp, ref_speed_mph, {club: (carry, total)})
# ---------------------------------------------------------------------------
# Distances are approximate amateur averages sourced from published studies.
# Reference speeds are typical driver clubhead speeds for each bracket.
# ---------------------------------------------------------------------------
_BRACKETS: list[tuple[int, int, float, dict[str, tuple[int, int]]]] = [
    (0, 5, 110.0, {
        "Driver": (250, 270), "3W": (225, 242), "4H": (212, 228),
        "5i": (185, 198), "6i": (174, 186), "7i": (161, 172),
        "8i": (149, 159), "9i": (136, 145), "PW": (122, 130),
        "52": (104, 109), "56": (84, 88), "60": (65, 68),
    }),
    (6, 10, 100.0, {
        "Driver": (235, 254), "3W": (210, 226), "4H": (197, 212),
        "5i": (174, 186), "6i": (163, 174), "7i": (151, 161),
        "8i": (139, 149), "9i": (127, 136), "PW": (114, 122),
        "52": (97, 102), "56": (78, 82), "60": (60, 63),
    }),
    (11, 15, 95.0, {
        "Driver": (220, 238), "3W": (195, 210), "4H": (183, 197),
        "5i": (164, 176), "6i": (154, 165), "7i": (142, 152),
        "8i": (131, 140), "9i": (119, 127), "PW": (107, 114),
        "52": (91, 96), "56": (73, 77), "60": (56, 59),
    }),
    (16, 20, 90.0, {
        "Driver": (205, 222), "3W": (182, 196), "4H": (170, 183),
        "5i": (153, 164), "6i": (143, 153), "7i": (133, 142),
        "8i": (122, 131), "9i": (111, 119), "PW": (100, 107),
        "52": (85, 90), "56": (68, 72), "60": (52, 55),
    }),
    (21, 25, 85.0, {
        "Driver": (188, 204), "3W": (167, 180), "4H": (156, 168),
        "5i": (141, 151), "6i": (132, 141), "7i": (122, 130),
        "8i": (113, 121), "9i": (103, 110), "PW": (92, 99),
        "52": (78, 82), "56": (63, 66), "60": (48, 51),
    }),
    (26, 54, 80.0, {
        "Driver": (172, 187), "3W": (153, 165), "4H": (143, 154),
        "5i": (129, 138), "6i": (120, 129), "7i": (111, 119),
        "8i": (103, 110), "9i": (94, 100), "PW": (84, 90),
        "52": (71, 75), "56": (57, 60), "60": (43, 46),
    }),
]


def estimate_distances(
    handicap: float,
    driver_speed_mph: float | None = None,
    gender: str = "male",
) -> dict[str, dict[str, int]]:
    """Return estimated carry and total distances for a standard club bag.

    Args:
        handicap: Golfer's handicap index (0–54).
        driver_speed_mph: Driver clubhead speed in mph.  When provided,
            all distances are scaled proportionally vs the bracket reference
            speed, so faster/slower swingers get appropriate numbers.
        gender: "male" or "female".  Female distances are reduced 15%.

    Returns:
        Dict mapping club name → {"carry": int, "total": int}.
    """
    hcp = max(0, min(54, handicap))

    # Find matching bracket
    base_clubs: dict[str, tuple[int, int]] = _BRACKETS[-1][3]
    ref_speed: float = _BRACKETS[-1][2]
    for lo, hi, speed, clubs in _BRACKETS:
        if lo <= hcp <= hi:
            base_clubs = clubs
            ref_speed = speed
            break

    # Speed scaling factor
    if driver_speed_mph is not None and driver_speed_mph > 0 and ref_speed > 0:
        scale = driver_speed_mph / ref_speed
        # Clamp scale to ±35% to avoid wild extrapolation
        scale = max(0.65, min(1.35, scale))
    else:
        scale = 1.0

    # Gender adjustment
    gender_factor = 0.85 if gender.lower() == "female" else 1.0

    factor = scale * gender_factor

    result: dict[str, dict[str, int]] = {}
    for club, (carry_base, total_base) in base_clubs.items():
        result[club] = {
            "carry": max(1, round(carry_base * factor)),
            "total": max(1, round(total_base * factor)),
        }

    return result
