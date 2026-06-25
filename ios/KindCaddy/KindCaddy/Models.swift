import Foundation

// MARK: - Profile models (match Python api_models.py)

struct ClubDistance: Codable {
    var carry: Int
    var total: Int
}

struct Tendencies: Codable {
    var under_pressure: String = ""
    var back_nine: String = ""
    var wind: String = ""
    var general: String = ""
}

struct PhysicalProfile: Codable {
    var gender: String = ""
    var age_group: String = ""
    var driver_clubhead_speed_mph: Double? = nil
    var workout_frequency: String = ""
    var practice_frequency: String = ""
}

struct GolferProfile: Codable {
    var name: String
    var handicap: Double
    var shot_shape: String
    var handed: String
    var chat_style: String
    var model_selection: String? = "gpt_wrapper"
    var target_score: Int?
    var clubs: [String: ClubDistance]
    var tendencies: Tendencies
    var physical: PhysicalProfile

    static let `default` = GolferProfile(
        name: "",
        handicap: 15,
        shot_shape: "fade",
        handed: "right",
        chat_style: "casual",
        model_selection: "gpt_wrapper",
        target_score: nil,
        clubs: [:],
        tendencies: Tendencies(),
        physical: PhysicalProfile(gender: "male")
    )
}

// MARK: - Auth models

struct AppleAuthRequest: Codable {
    let identity_token: String
    var display_name: String?
    var email: String?
}

struct GoogleAuthRequest: Codable {
    let id_token: String
    var display_name: String?
    var email: String?
}

struct AuthUserInfo: Codable {
    let id: String
    let email: String?
    let display_name: String?
    let provider: String
    /// Whether the caddy is allowed to recall past Q/A pairs for this user.
    /// Optional in the decode path so older builds that pre-date the field
    /// keep working — defaults to ``true`` when the server omits it.
    let memory_enabled: Bool?
}

struct AuthResponse: Codable {
    let access_token: String
    let token_type: String
    let user: AuthUserInfo
}

// MARK: - Subscription models

struct SubscriptionStatusResponse: Codable, Equatable {
    let trial_round_starts: Int
    let trial_round_limit: Int
    let trial_rounds_remaining: Int
    let is_trial_available: Bool
    let profile_stats_allowed: Bool
    let can_start_round: Bool
    let subscription_status: String
    let is_subscribed: Bool
    let product_id: String?
    let expires_at: String?
    let environment: String?

    static let empty = SubscriptionStatusResponse(
        trial_round_starts: 0,
        trial_round_limit: 5,
        trial_rounds_remaining: 5,
        is_trial_available: true,
        profile_stats_allowed: true,
        can_start_round: true,
        subscription_status: "none",
        is_subscribed: false,
        product_id: nil,
        expires_at: nil,
        environment: nil
    )
}

struct SubscriptionVerifyRequest: Codable {
    let signed_transaction_info: String
}

struct SubscriptionVerifyResponse: Codable {
    let status: SubscriptionStatusResponse
}

struct PaymentRequiredPayload: Codable {
    let code: String?
    let message: String
    let subscription: SubscriptionStatusResponse?
}

struct PaymentRequiredErrorResponse: Codable {
    let detail: PaymentRequiredPayload
}

// MARK: - API request/response models

struct CreateSessionRequest: Codable {
    let profile: GolferProfile
    var model: String = "gpt-4o"
    var max_tokens: Int = 1024
}

struct CreateSessionResponse: Codable {
    let session_id: String
    let briefing: String?
}

struct RecoverSessionRequest: Codable {
    let round_id: String?
}

struct RecoverSessionResponse: Codable {
    let session_id: String
    let round_id: String
    let recovered: Bool
    let holes_played: Int
}

struct AdviceRequest: Codable {
    let session_id: String
    let text: String
}

struct AdviceResponse: Codable {
    let text: String
}

struct TranscribeResponse: Codable {
    let transcript: String
    let advice_text: String
}

struct CommandRequest: Codable {
    let session_id: String
    let command: String
    let args: String
}

struct CommandResponse: Codable {
    let message: String
}

struct WeatherUpdateRequest: Codable {
    let session_id: String
    let lat: Double
    let lon: Double
    var temp_f: Double?
    var wind_speed_mph: Double?
    var wind_deg: Double?
    var wind_gust_mph: Double?
    var humidity: Int?
    var description: String?
}

struct WeatherUpdateResponse: Codable {
    let temp_f: Double
    let wind_speed_mph: Double
    let wind_deg: Double
    let wind_gust_mph: Double
    let humidity: Int
    let description: String
    let summary: String
}

struct SessionStateResponse: Codable {
    let session_id: String
    let round_id: String?
    let hole: Int?
    let is_active: Bool
    let conditions: String
    let round_summary: String
    let scores: [RoundScoreEntry]
    let pars: [Int]
    let yardages: [Int: Int]
}

// MARK: - Round history & stats models

struct RoundScoreEntry: Codable, Identifiable {
    var id: Int { hole }
    let hole: Int
    let strokes: Int
    let par: Int
    let yardage: Int?
}

struct RoundShotEntry: Codable, Identifiable {
    let hole: Int
    let club: String
    let intended_distance: Double?
    let actual_distance: Double?
    let miss_direction: String?
    let lie: String?
    let notes: String?
    let profile_carry: Double?

    var id: String { "\(hole)-\(club)-\(actual_distance ?? 0)" }
}

struct RoundSummary: Codable, Identifiable, Hashable {
    let id: String
    let status: String
    let course_name: String?
    let started_at: String
    let finished_at: String?
    let target_score: Int?
    let total_strokes: Int
    let total_par: Int
    let score_vs_par: Int?
    let holes_played: Int
    let weather_summary: String?
    let summary_text: String?
}

struct RoundMessage: Codable, Identifiable {
    let role: String
    let content: String
    let hole: Int?
    let created_at: String

    var id: String { "\(created_at)-\(role)" }
    var isUser: Bool { role == "user" }
}

struct RoundDetail: Codable {
    let id: String
    let status: String
    let course_name: String?
    let started_at: String
    let finished_at: String?
    let target_score: Int?
    let total_strokes: Int
    let total_par: Int
    let score_vs_par: Int?
    let holes_played: Int
    let weather_summary: String?
    let summary_text: String?
    let pars: [Int]?
    let scores: [RoundScoreEntry]
    let shots: [RoundShotEntry]
    let messages: [RoundMessage]?
}

struct RoundListResponse: Codable {
    let rounds: [RoundSummary]
    let total: Int
}

struct ScoringDistribution: Codable {
    let eagle_or_better: Int
    let birdie: Int
    let par: Int
    let bogey: Int
    let double_bogey: Int
    let triple_or_worse: Int
}

struct MissTendencies: Codable {
    let left: Int
    let right: Int
    let short: Int
    let long: Int
}

struct RecentRoundStat: Codable, Identifiable {
    let round_id: String
    let date: String
    let total_strokes: Int
    let holes_played: Int
    let score_vs_par: Int
    let target_score: Int?
    let hit_target: Bool?

    var id: String { round_id }
}

struct StatsResponse: Codable {
    let total_rounds: Int
    let total_holes: Int
    let avg_score_vs_par: Double
    let best_score_vs_par: Int?
    let worst_score_vs_par: Int?
    let scoring_distribution: ScoringDistribution
    let miss_tendencies: MissTendencies
    let recent_rounds: [RecentRoundStat]
}

struct FinishRoundRequest: Codable {
    let status: String
}

struct GenericMessageResponse: Codable {
    let message: String
}

struct AnalyticsEventRequest: Codable {
    let event_name: String
    let session_id: String?
    let round_id: String?
    let platform: String
    let properties: [String: String]
}

// MARK: - Profile update models

struct UpdateProfileRequest: Codable {
    let display_name: String
}

// MARK: - Calibration models

struct CalibrationSuggestion: Codable, Identifiable {
    let club: String
    let profile_carry: Int
    let avg_carry: Int
    let delta: Int
    let shot_count: Int

    var id: String { club }
}

struct CalibrationResponse: Codable {
    let suggestions: [CalibrationSuggestion]
}

// MARK: - Distance estimation models

struct EstimateDistancesRequest: Codable {
    let handicap: Double
    let driver_speed_mph: Double?
    let gender: String
}

struct EstimateDistancesResponse: Codable {
    let clubs: [String: ClubDistance]
}

// MARK: - User Insights models

struct ClubInsight: Codable, Identifiable {
    let club: String
    let avg_carry: Double
    let profile_carry: Double?
    let delta: Double?
    let shot_count: Int
    let dominant_miss: String?

    var id: String { club }
}

struct ScoringPatterns: Codable {
    let par3_avg: Double?
    let par4_avg: Double?
    let par5_avg: Double?
    let front9_avg: Double?
    let back9_avg: Double?
}

struct UserInsightsResponse: Codable {
    let club_insights: [ClubInsight]
    let scoring_patterns: ScoringPatterns?
    let miss_tendencies: MissTendencies
    let fatigue_yards_lost: Double?
    let pressure_scoring_delta: Double?
    let improvement_trend: String?
    let rounds_analyzed: Int
    let updated_at: String?
}
