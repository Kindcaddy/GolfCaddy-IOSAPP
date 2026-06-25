import Foundation

actor APIClient {
    static let shared = APIClient()
    var baseURL: String = "http://localhost:8000"

    /// JWT access token (primary auth). Falls back to apiKey when nil.
    var authToken: String?
    var apiKey: String = Config.apiKey

    func setBaseURL(_ url: String) {
        var s = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return }
        // Fix broken xcconfig $(/) expansion: "https:host" → "https://host"
        if s.hasPrefix("https:") && !s.hasPrefix("https://") {
            s = "https://" + s.dropFirst(6).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        } else if s.hasPrefix("http:") && !s.hasPrefix("http://") {
            s = "http://" + s.dropFirst(5).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        } else if !s.hasPrefix("http://") && !s.hasPrefix("https://") {
            s = "https://" + s
        }
        baseURL = s
    }

    func setAuthToken(_ token: String) {
        authToken = token
    }

    func clearAuthToken() {
        authToken = nil
    }

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        return URLSession(configuration: config)
    }()

    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    // MARK: - Auth

    func authApple(identityToken: String, displayName: String?, email: String?) async throws -> AuthResponse {
        let body = AppleAuthRequest(identity_token: identityToken, display_name: displayName, email: email)
        return try await post("/auth/apple", body: body)
    }

    func authGoogle(idToken: String, displayName: String?, email: String?) async throws -> AuthResponse {
        let body = GoogleAuthRequest(id_token: idToken, display_name: displayName, email: email)
        return try await post("/auth/google", body: body)
    }

    func getCurrentUser() async throws -> AuthUserInfo {
        return try await get("/auth/me")
    }

    func updateDisplayName(_ name: String) async throws -> AuthUserInfo {
        let body = UpdateProfileRequest(display_name: name)
        return try await patch("/auth/me", body: body)
    }

    func updateMemoryEnabled(_ enabled: Bool) async throws -> AuthUserInfo {
        let body = ["memory_enabled": enabled]
        return try await patch("/auth/me/memory", body: body)
    }

    func deleteAccount() async throws {
        try await delete("/auth/me")
    }

    // MARK: - Subscription

    func getSubscriptionStatus() async throws -> SubscriptionStatusResponse {
        return try await get("/subscription/status")
    }

    func verifySubscription(signedTransactionInfo: String) async throws -> SubscriptionVerifyResponse {
        let body = SubscriptionVerifyRequest(signed_transaction_info: signedTransactionInfo)
        return try await post("/subscription/verify", body: body)
    }

    // MARK: - Session

    func createSession(profile: GolferProfile) async throws -> CreateSessionResponse {
        let selectedModel = Self.backendModel(for: profile.model_selection)
        let body = CreateSessionRequest(profile: profile, model: selectedModel)
        return try await post("/session", body: body)
    }

    func recoverSession(roundId: String? = nil) async throws -> RecoverSessionResponse {
        let body = RecoverSessionRequest(round_id: roundId)
        return try await post("/session/recover", body: body)
    }

    // MARK: - Advice

    func getAdvice(sessionId: String, text: String) async throws -> AdviceResponse {
        let body = AdviceRequest(session_id: sessionId, text: text)
        return try await post("/advice", body: body)
    }

    func transcribeAudio(sessionId: String, audioFileURL: URL) async throws -> TranscribeResponse {
        guard let url = URL(string: baseURL + "/transcribe") else {
            throw APIError.invalidURL
        }
        let audioData = try Data(contentsOf: audioFileURL)
        let boundary = "Boundary-\(UUID().uuidString)"

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        addAuthHeaders(to: &request)
        request.httpBody = Self.makeTranscribeMultipartBody(
            boundary: boundary,
            sessionId: sessionId,
            audioData: audioData,
            filename: audioFileURL.lastPathComponent
        )

        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try decoder.decode(TranscribeResponse.self, from: data)
    }

    // MARK: - Command

    func runCommand(sessionId: String, command: String, args: String = "") async throws -> CommandResponse {
        let body = CommandRequest(session_id: sessionId, command: command, args: args)
        return try await post("/command", body: body)
    }

    func editScore(sessionId: String, hole: Int, strokes: Int) async throws -> CommandResponse {
        return try await runCommand(sessionId: sessionId, command: "editscore", args: "\(hole) \(strokes)")
    }

    // MARK: - Weather

    func updateWeather(sessionId: String, lat: Double, lon: Double, weatherKit: WeatherKitData? = nil) async throws -> WeatherUpdateResponse {
        let body = WeatherUpdateRequest(
            session_id: sessionId,
            lat: lat,
            lon: lon,
            temp_f: weatherKit?.tempF,
            wind_speed_mph: weatherKit?.windSpeedMph,
            wind_deg: weatherKit?.windDeg,
            wind_gust_mph: weatherKit?.windGustMph,
            humidity: weatherKit?.humidity,
            description: weatherKit?.description
        )
        return try await post("/weather/update", body: body)
    }

    // MARK: - TTS

    func synthesizeSpeech(text: String, voice: String = "nova") async throws -> Data {
        guard let url = URL(string: baseURL + "/tts") else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuthHeaders(to: &request)
        request.httpBody = try encoder.encode(["text": text, "voice": voice])

        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return data
    }

    // MARK: - State

    func getSessionState(sessionId: String) async throws -> SessionStateResponse {
        return try await get("/session/\(sessionId)")
    }

    // MARK: - Round history & stats

    func getRounds(limit: Int = 20, offset: Int = 0, status: String? = nil) async throws -> RoundListResponse {
        var path = "/rounds?limit=\(limit)&offset=\(offset)"
        if let s = status { path += "&status=\(s)" }
        return try await get(path)
    }

    func getRoundDetail(roundId: String) async throws -> RoundDetail {
        return try await get("/rounds/\(roundId)")
    }

    /// Returns the user's currently in-progress round, or nil if none exists.
    /// 404 from the server is treated as "no active round" (not an error).
    func getActiveRound() async throws -> RoundSummary? {
        do {
            let r: RoundSummary = try await get("/rounds/active")
            return r
        } catch APIError.serverError(let status, _) where status == 404 {
            return nil
        }
    }

    func deleteRound(roundId: String) async throws {
        try await delete("/rounds/\(roundId)")
    }

    func getRoundStats() async throws -> StatsResponse {
        return try await get("/rounds/stats")
    }

    func finishRound(roundId: String, status: String = "completed") async throws -> GenericMessageResponse {
        let body = FinishRoundRequest(status: status)
        return try await post("/rounds/\(roundId)/finish", body: body)
    }

    func generateRecap(roundId: String) async throws -> GenericMessageResponse {
        return try await post("/rounds/\(roundId)/recap", body: [String: String]())
    }

    func editRoundScore(roundId: String, hole: Int, strokes: Int) async throws -> GenericMessageResponse {
        let body = ["strokes": strokes]
        return try await patch("/rounds/\(roundId)/scores/\(hole)", body: body)
    }

    func getInsights() async throws -> UserInsightsResponse {
        return try await get("/insights")
    }

    func getCalibration() async throws -> CalibrationResponse {
        return try await get("/calibration")
    }

    func registerDeviceToken(_ token: String) async throws {
        let body = ["device_token": token, "platform": "ios"]
        let _: GenericMessageResponse = try await post("/device-token", body: body)
    }

    func trackEvent(
        name: String,
        sessionId: String? = nil,
        roundId: String? = nil,
        properties: [String: String] = [:]
    ) async {
        let body = AnalyticsEventRequest(
            event_name: name,
            session_id: sessionId,
            round_id: roundId,
            platform: "ios",
            properties: properties
        )
        do {
            try await postNoContent("/events", body: body)
        } catch {
            // KPI tracking must be fire-and-forget and never impact product UX.
        }
    }

    func estimateDistances(handicap: Double, driverSpeed: Double?, gender: String) async throws -> EstimateDistancesResponse {
        let body = EstimateDistancesRequest(handicap: handicap, driver_speed_mph: driverSpeed, gender: gender)
        return try await post("/estimate-distances", body: body)
    }

    // MARK: - HTTP helpers

    private func addAuthHeaders(to request: inout URLRequest) {
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } else if !apiKey.isEmpty {
            request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        }
    }

    private func post<Req: Encodable, Resp: Decodable>(_ path: String, body: Req) async throws -> Resp {
        guard let url = URL(string: baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuthHeaders(to: &request)
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try decoder.decode(Resp.self, from: data)
    }

    private func postNoContent<Req: Encodable>(_ path: String, body: Req) async throws {
        guard let url = URL(string: baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuthHeaders(to: &request)
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
    }

    private func patch<Req: Encodable, Resp: Decodable>(_ path: String, body: Req) async throws -> Resp {
        guard let url = URL(string: baseURL + path) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuthHeaders(to: &request)
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try decoder.decode(Resp.self, from: data)
    }

    private func get<Resp: Decodable>(_ path: String) async throws -> Resp {
        guard let url = URL(string: baseURL + path) else {
            throw APIError.invalidURL
        }
        var urlRequest = URLRequest(url: url)
        addAuthHeaders(to: &urlRequest)
        let (data, response) = try await session.data(for: urlRequest)
        try validateResponse(response, data: data)
        return try decoder.decode(Resp.self, from: data)
    }

    private func delete(_ path: String) async throws {
        guard let url = URL(string: baseURL + path) else {
            throw APIError.invalidURL
        }
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = "DELETE"
        addAuthHeaders(to: &urlRequest)
        let (data, response) = try await session.data(for: urlRequest)
        try validateResponse(response, data: data)
    }

    private func validateResponse(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIError.badResponse
        }
        if http.statusCode == 401 {
            throw APIError.unauthorized
        }
        if http.statusCode == 402 {
            if let payment = try? decoder.decode(PaymentRequiredErrorResponse.self, from: data) {
                throw APIError.subscriptionRequired(
                    status: payment.detail.subscription,
                    message: payment.detail.message
                )
            }
            throw APIError.subscriptionRequired(
                status: nil,
                message: Self.parseErrorDetail(from: data) ?? "Choose a plan to continue."
            )
        }
        guard (200...299).contains(http.statusCode) else {
            let detail = Self.parseErrorDetail(from: data)
            throw APIError.serverError(status: http.statusCode, detail: detail ?? "Unknown error")
        }
    }

    private static func parseErrorDetail(from data: Data) -> String? {
        let decoder = JSONDecoder()
        if let simple = try? decoder.decode(ErrorDetailString.self, from: data) {
            return simple.detail
        }
        if let validation = try? decoder.decode(ErrorDetailArray.self, from: data) {
            return validation.detail.map { item in
                let field = item.loc.dropFirst().joined(separator: ".")
                return field.isEmpty ? item.msg : "\(field): \(item.msg)"
            }.joined(separator: "; ")
        }
        return nil
    }

    private static func backendModel(for modelSelection: String?) -> String {
        switch modelSelection ?? "gpt_wrapper" {
        case "private_model":
            return "qwen3.5:4b"
        default:
            return "gpt-4o"
        }
    }

    private static func makeTranscribeMultipartBody(
        boundary: String,
        sessionId: String,
        audioData: Data,
        filename: String
    ) -> Data {
        var body = Data()
        let lineBreak = "\r\n"

        func append(_ string: String) {
            body.append(Data(string.utf8))
        }

        append("--\(boundary)\(lineBreak)")
        append("Content-Disposition: form-data; name=\"session_id\"\(lineBreak)\(lineBreak)")
        append("\(sessionId)\(lineBreak)")

        append("--\(boundary)\(lineBreak)")
        append("Content-Disposition: form-data; name=\"audio\"; filename=\"\(filename)\"\(lineBreak)")
        append("Content-Type: audio/wav\(lineBreak)\(lineBreak)")
        body.append(audioData)
        append(lineBreak)

        append("--\(boundary)--\(lineBreak)")
        return body
    }
}

enum APIError: LocalizedError {
    case invalidURL
    case badResponse
    case unauthorized
    case subscriptionRequired(status: SubscriptionStatusResponse?, message: String)
    case serverError(status: Int, detail: String)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "KindCaddy couldn't build a valid server URL."
        case .badResponse: return "KindCaddy received an invalid response from the server."
        case .unauthorized: return "Your sign-in expired. Please sign in again."
        case .subscriptionRequired(_, let message): return message
        case .serverError(let status, let detail):
            if status == 404 || status == 410 {
                return "Your live round session expired on the server."
            }
            if status == 429 {
                return "KindCaddy is busy right now. Please wait a moment and retry."
            }
            return "Server error (\(status)): \(detail)"
        }
    }
}

private struct ErrorDetailString: Decodable {
    let detail: String
}

private struct ErrorDetailArray: Decodable {
    let detail: [ValidationItem]

    struct ValidationItem: Decodable {
        let loc: [String]
        let msg: String
    }
}
