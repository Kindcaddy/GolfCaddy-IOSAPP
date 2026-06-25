"""Pydantic models for the KindCaddy REST API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .profile import ClubDistance, GolferProfile, PhysicalProfile, Tendencies


# ── Auth ─────────────────────────────────────────────────────────────────────


class AppleAuthRequest(BaseModel):
    identity_token: str = Field(description="JWT from ASAuthorizationAppleIDCredential.identityToken")
    display_name: Optional[str] = None
    email: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(description="JWT from Google Sign-In SDK (GIDGoogleUser.idToken.tokenString)")
    display_name: Optional[str] = None
    email: Optional[str] = None


class AuthUserResponse(BaseModel):
    id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    provider: str
    memory_enabled: bool = True


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserResponse


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class MemoryPreferenceRequest(BaseModel):
    memory_enabled: bool


# ── Subscription / entitlement ───────────────────────────────────────────────


class SubscriptionStatusResponse(BaseModel):
    trial_round_starts: int = 0
    trial_round_limit: int = 5
    trial_rounds_remaining: int = 5
    is_trial_available: bool = True
    profile_stats_allowed: bool = True
    can_start_round: bool = True
    subscription_status: str = "none"
    is_subscribed: bool = False
    product_id: Optional[str] = None
    expires_at: Optional[str] = None
    environment: Optional[str] = None


class SubscriptionVerifyRequest(BaseModel):
    signed_transaction_info: str = Field(min_length=1)


class SubscriptionVerifyResponse(BaseModel):
    status: SubscriptionStatusResponse


# ── Session ──────────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    profile: GolferProfile
    model: str = Field(default="gpt-4o")
    max_tokens: int = Field(default=1024, ge=64, le=4096)


class CreateSessionResponse(BaseModel):
    session_id: str
    briefing: Optional[str] = None


class RecoverSessionRequest(BaseModel):
    round_id: Optional[str] = Field(
        default=None,
        description="Specific active round to recover. If omitted, server picks the latest active round.",
    )


class RecoverSessionResponse(BaseModel):
    session_id: str
    round_id: str
    recovered: bool = True
    holes_played: int = 0


# ── Advice ───────────────────────────────────────────────────────────────────


class AdviceRequest(BaseModel):
    session_id: str
    text: str = Field(min_length=1)


class AdviceResponse(BaseModel):
    text: str


class TranscribeResponse(BaseModel):
    transcript: str
    advice_text: str


# ── Command ──────────────────────────────────────────────────────────────────


class CommandRequest(BaseModel):
    session_id: str
    command: str = Field(description="newround | hole | weather | altitude | score | shot | scorecard | summary")
    args: str = Field(default="", description="Command arguments as a string, e.g. '7' for hole, '72F wind 12mph SW' for weather")


class CommandResponse(BaseModel):
    message: str


# ── Weather ──────────────────────────────────────────────────────────────────


class WeatherUpdateRequest(BaseModel):
    session_id: str
    lat: float = Field(description="Latitude from device GPS")
    lon: float = Field(description="Longitude from device GPS")
    temp_f: Optional[float] = Field(default=None, description="Temperature in Fahrenheit (from WeatherKit)")
    wind_speed_mph: Optional[float] = Field(default=None, description="Wind speed in mph")
    wind_deg: Optional[float] = Field(default=None, description="Wind direction in degrees")
    wind_gust_mph: Optional[float] = Field(default=None, description="Wind gust speed in mph")
    humidity: Optional[int] = Field(default=None, description="Humidity percentage 0-100")
    description: Optional[str] = Field(default=None, description="Weather condition text")


class WeatherUpdateResponse(BaseModel):
    temp_f: float
    wind_speed_mph: float
    wind_deg: float
    wind_gust_mph: float
    humidity: int
    description: str
    summary: str


# ── Session state (GET) ─────────────────────────────────────────────────────


class SessionStateResponse(BaseModel):
    session_id: str
    round_id: Optional[str] = None
    hole: Optional[int] = None
    is_active: bool = False
    conditions: str = ""
    round_summary: str = ""
    scores: list[RoundScoreEntry] = []
    pars: list[int] = []
    yardages: dict[int, int] = {}


# ── Round history & stats ────────────────────────────────────────────────────


class RoundScoreEntry(BaseModel):
    hole: int
    strokes: int
    par: int
    yardage: Optional[int] = None


class EditRoundScoreRequest(BaseModel):
    strokes: int = Field(ge=1, le=15, description="New stroke count for the hole")


class RoundShotEntry(BaseModel):
    hole: int
    club: str
    intended_distance: Optional[float] = None
    actual_distance: Optional[float] = None
    miss_direction: Optional[str] = None
    lie: str = "fairway"
    notes: str = ""
    profile_carry: Optional[float] = None


class RoundSummaryResponse(BaseModel):
    id: str
    status: str
    course_name: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    target_score: Optional[int] = None
    total_strokes: int = 0
    total_par: int = 0
    score_vs_par: Optional[int] = None
    holes_played: int = 0
    weather_summary: Optional[str] = None
    summary_text: Optional[str] = None


class RoundMessageEntry(BaseModel):
    role: str
    content: str
    hole: Optional[int] = None
    created_at: str


class RoundDetailResponse(RoundSummaryResponse):
    pars: Optional[list[int]] = None
    scores: list[RoundScoreEntry] = []
    shots: list[RoundShotEntry] = []
    messages: list[RoundMessageEntry] = []


class RoundListResponse(BaseModel):
    rounds: list[RoundSummaryResponse]
    total: int


class ScoringDistribution(BaseModel):
    eagle_or_better: int = 0
    birdie: int = 0
    par: int = 0
    bogey: int = 0
    double_bogey: int = 0
    triple_or_worse: int = 0


class MissTendencies(BaseModel):
    left: int = 0
    right: int = 0
    short: int = 0
    long: int = 0


class RecentRoundStat(BaseModel):
    round_id: str
    date: str
    total_strokes: int
    holes_played: int
    score_vs_par: int
    target_score: Optional[int] = None
    hit_target: Optional[bool] = None


class StatsResponse(BaseModel):
    total_rounds: int = 0
    total_holes: int = 0
    avg_score_vs_par: float = 0
    best_score_vs_par: Optional[int] = None
    worst_score_vs_par: Optional[int] = None
    scoring_distribution: ScoringDistribution = ScoringDistribution()
    miss_tendencies: MissTendencies = MissTendencies()
    recent_rounds: list[RecentRoundStat] = []


class FinishRoundRequest(BaseModel):
    status: str = Field(default="completed", description="completed or abandoned")


# ── Calibration ───────────────────────────────────────────────────────────────


class DeviceTokenRequest(BaseModel):
    device_token: str = Field(min_length=64, max_length=200)
    platform: str = Field(default="ios")


class AnalyticsEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=80)
    session_id: Optional[str] = None
    round_id: Optional[str] = None
    platform: str = Field(default="ios")
    properties: dict = Field(default_factory=dict)


class CalibrationSuggestion(BaseModel):
    club: str
    profile_carry: int
    avg_carry: int
    delta: int
    shot_count: int


class CalibrationResponse(BaseModel):
    suggestions: list[CalibrationSuggestion] = []


# ── Distance Estimation ───────────────────────────────────────────────────────

class EstimateDistancesRequest(BaseModel):
    handicap: float = Field(ge=0, le=54)
    driver_speed_mph: Optional[float] = None
    gender: str = "male"


class EstimateDistancesResponse(BaseModel):
    clubs: dict[str, ClubDistance]


# ── User Insights ─────────────────────────────────────────────────────────────


class ClubInsight(BaseModel):
    club: str
    avg_carry: float
    profile_carry: Optional[float] = None
    delta: Optional[float] = None
    shot_count: int
    dominant_miss: Optional[str] = None


class ScoringPatterns(BaseModel):
    par3_avg: Optional[float] = None
    par4_avg: Optional[float] = None
    par5_avg: Optional[float] = None
    front9_avg: Optional[float] = None
    back9_avg: Optional[float] = None


class UserInsightsResponse(BaseModel):
    club_insights: list[ClubInsight] = []
    scoring_patterns: Optional[ScoringPatterns] = None
    miss_tendencies: MissTendencies = MissTendencies()
    fatigue_yards_lost: Optional[float] = None
    pressure_scoring_delta: Optional[float] = None
    improvement_trend: Optional[str] = None
    rounds_analyzed: int = 0
    updated_at: Optional[str] = None
