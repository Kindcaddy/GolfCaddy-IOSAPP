import Combine
import Foundation
import SwiftUI
import UIKit
import UserNotifications

@MainActor
class AppState: ObservableObject {
    @Published var profile: GolferProfile {
        didSet { saveProfile() }
    }
    @Published var sessionId: String? = nil {
        didSet { persistSessionRefs() }
    }
    @Published var roundId: String? = nil {
        didSet { persistSessionRefs() }
    }
    @Published var currentHole: Int? = nil
    @Published var isActive: Bool = false
    @Published var conditions: String = ""
    @Published var lastResponse: String = ""
    @Published var isLoading: Bool = false
    @Published var errorMessage: String? = nil
    @Published var paywallRequired: Bool = false
    @Published var recoveryMessage: String? = nil
    @Published var weatherSummary: String = ""
    @Published var liveScores: [RoundScoreEntry] = []
    @Published var livePars: [Int] = []
    @Published var liveYardages: [Int: Int] = [:]
    @Published var pendingScore: Int? = nil
    @Published var loadingMessage: String = ""
    @Published var isBeginnerMode: Bool {
        didSet { UserDefaults.standard.set(isBeginnerMode, forKey: "isBeginnerMode") }
    }
    @Published var showingAIDataConsent = false

    /// The user's in-progress round on the server, surfaced on Home as a
    /// "Continue Round" card. Refreshed on Home appear and after start/finish.
    @Published var activeRound: RoundSummary? = nil

    /// Set when the user finishes (or has the server finish) a round so the
    /// round-end UI can surface a "View Chat History" affordance pointing at
    /// the just-completed round_id. Cleared on next start_session.
    @Published var lastCompletedRoundId: String? = nil
    @Published var roundJustCompleted: Bool = false

    private static let sessionIdKey = "kindcaddy.activeSessionId"
    private static let roundIdKey = "kindcaddy.activeRoundId"

    let locationManager = LocationManager()
    let networkMonitor = NetworkMonitor.shared
    @Published var currentWeather: WeatherKitData? = nil

    private let api = APIClient.shared
    private let weatherService = WeatherKitService()
    private var weatherTimer: AnyCancellable?
    private var locationObserver: AnyCancellable?
    private var lastWeatherLat: Double?
    private var lastWeatherLon: Double?

    private enum LastOperation {
        case startSession
        case getAdvice(text: String)
        case runCommand(command: String, args: String)
        case editScore(hole: Int, strokes: Int)
        case updateHoleStats(hole: Int, par: Int, yards: Int?)
        case refreshState
    }

    private var lastOperation: LastOperation?

    /// True when network is unavailable.
    var isOffline: Bool { !networkMonitor.isConnected }

    init() {
        if let data = UserDefaults.standard.data(forKey: "golferProfile"),
           let saved = try? JSONDecoder().decode(GolferProfile.self, from: data) {
            self.profile = saved
        } else {
            self.profile = .default
        }
        self.isBeginnerMode = UserDefaults.standard.bool(forKey: "isBeginnerMode")

        // Restore session refs from a previous launch. The router (KindCaddyApp)
        // re-enters RoundView automatically; refreshState() will clear these
        // if the server has since dropped the session (404/410), bouncing the
        // user safely back to Home where the Continue Round card takes over.
        let defaults = UserDefaults.standard
        self.sessionId = defaults.string(forKey: Self.sessionIdKey)
        self.roundId = defaults.string(forKey: Self.roundIdKey)
    }

    private func persistSessionRefs() {
        let defaults = UserDefaults.standard
        if let sid = sessionId {
            defaults.set(sid, forKey: Self.sessionIdKey)
        } else {
            defaults.removeObject(forKey: Self.sessionIdKey)
        }
        if let rid = roundId {
            defaults.set(rid, forKey: Self.roundIdKey)
        } else {
            defaults.removeObject(forKey: Self.roundIdKey)
        }
    }

    /// True when the user hasn't completed first-time setup (name or clubs missing).
    var isFirstLaunch: Bool {
        profile.name.trimmingCharacters(in: .whitespaces).isEmpty || profile.clubs.isEmpty
    }

    var hasAIDataConsent: Bool {
        AIDataConsent.hasAcceptedCurrentVersion
    }

    func acceptAIDataConsent() {
        AIDataConsent.acceptCurrentVersion()
        showingAIDataConsent = false
        errorMessage = nil
    }

    func declineAIDataConsent() {
        showingAIDataConsent = false
        loadingMessage = ""
        isLoading = false
        errorMessage = "KindCaddy needs your permission before sending profile, round, voice, or advice data to OpenAI for AI caddy features."
    }

    func requestAIDataConsent() {
        loadingMessage = ""
        isLoading = false
        errorMessage = nil
        showingAIDataConsent = true
    }

    private func requireAIDataConsent() -> Bool {
        guard !hasAIDataConsent else { return true }
        requestAIDataConsent()
        return false
    }

    func retryLastOperation() async {
        guard let op = lastOperation else { return }
        switch op {
        case .startSession:
            await startSession()
        case .getAdvice(let text):
            await getAdvice(text: text)
        case .runCommand(let command, let args):
            await runCommand(command: command, args: args)
        case .editScore(let hole, let strokes):
            await editScore(hole: hole, strokes: strokes)
        case .updateHoleStats(let hole, let par, let yards):
            await updateHoleStats(hole: hole, par: par, yards: yards)
        case .refreshState:
            await refreshState()
        }
    }

    private func presentError(_ error: Error, fallback: String) {
        Task {
            await api.trackEvent(
                name: "app_error_presented",
                sessionId: sessionId,
                roundId: roundId,
                properties: ["fallback": fallback]
            )
        }
        if let apiError = error as? APIError {
            switch apiError {
            case .unauthorized:
                errorMessage = "Your login expired. Please sign in again."
            case .subscriptionRequired:
                errorMessage = nil
                paywallRequired = true
            case .serverError(let status, let detail):
                if status == 404 || status == 410 {
                    errorMessage = "Your live caddy session expired. We are trying to recover your round."
                } else if status == 429 {
                    errorMessage = "KindCaddy is busy right now. Please retry in a few seconds."
                } else if status >= 500 {
                    errorMessage = "KindCaddy's server is temporarily unavailable. Please try again."
                } else {
                    errorMessage = detail
                }
            default:
                errorMessage = apiError.localizedDescription
            }
            return
        }
        if (error as NSError).domain == NSURLErrorDomain {
            errorMessage = "Network connection looks unstable. Please check signal and retry."
            return
        }
        errorMessage = fallback
    }

    @discardableResult
    private func recoverSessionIfPossible(reason: String) async -> Bool {
        guard activeRound != nil || roundId != nil else { return false }
        do {
            let recovery = try await api.recoverSession(roundId: activeRound?.id ?? roundId)
            sessionId = recovery.session_id
            roundId = recovery.round_id
            recoveryMessage = "Recovered your round through hole \(recovery.holes_played)."
            await api.trackEvent(
                name: "session_recovered_client",
                sessionId: recovery.session_id,
                roundId: recovery.round_id,
                properties: ["reason": reason, "holes_played": "\(recovery.holes_played)"]
            )
            return true
        } catch {
            await api.trackEvent(
                name: "session_recover_failed",
                sessionId: sessionId,
                roundId: activeRound?.id ?? roundId,
                properties: ["reason": reason]
            )
            return false
        }
    }

    // MARK: - Session

    func startSession() async {
        lastOperation = .startSession
        guard requireAIDataConsent() else { return }
        isLoading = true
        loadingMessage = "Setting up your bag…"
        errorMessage = nil
        recoveryMessage = nil
        roundJustCompleted = false
        lastCompletedRoundId = nil

        await api.setBaseURL(Config.backendBaseURL)
        do {
            let resp = try await api.createSession(profile: profile)
            sessionId = resp.session_id
            await api.trackEvent(name: "round_start_tapped", sessionId: resp.session_id)
            loadingMessage = "Starting your round…"
            let holeResp = try await api.runCommand(sessionId: resp.session_id, command: "newround", args: "")
            lastResponse = holeResp.message
            startWeatherUpdates()
            await refreshState()
        } catch {
            presentError(error, fallback: "Unable to start a round right now.")
            await api.trackEvent(name: "round_start_failed", properties: ["error": "start_session"])
        }
        loadingMessage = ""
        isLoading = false
    }

    func requestNotificationPermissionIfNeeded() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .notDetermined else { return }
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
                if granted {
                    DispatchQueue.main.async {
                        UIApplication.shared.registerForRemoteNotifications()
                    }
                }
            }
        }
    }

    /// Pause the current round: clear local in-memory session state but leave
    /// the round status as "active" on the server. Used by the "x" button in
    /// `RoundView` so a user stepping away does not have their round marked
    /// "completed" prematurely. The user can resume from Home (Continue Round
    /// card) or from History (Resume Round button).
    func pauseSession() {
        let endedSessionId = sessionId
        let endedRoundId = roundId
        stopWeatherUpdates()
        Task {
            await api.trackEvent(name: "round_paused", sessionId: endedSessionId, roundId: endedRoundId)
        }
        sessionId = nil
        isActive = false
        currentHole = nil
        conditions = ""
        weatherSummary = ""
        currentWeather = nil
        lastWeatherLat = nil
        lastWeatherLon = nil
        lastResponse = ""
        pendingScore = nil
        liveScores = []
        livePars = []
        liveYardages = [:]
        recoveryMessage = nil
        // Deliberately keep `roundId` and `activeRound` so HomeView shows the
        // Continue Round card immediately after pause.
    }

    /// Explicitly end the current round on the server. Default status is
    /// "abandoned" — used by voice commands ("end round" / "quit" / "exit"),
    /// which signal the user is done but did not actually finish 18 holes.
    /// Pass `status: "completed"` only if all 18 holes are scored.
    func endSession(status: String = "abandoned") {
        requestNotificationPermissionIfNeeded()
        let endedSessionId = sessionId
        let endedRoundId = roundId
        if let rid = roundId {
            // Preserve the id so the post-round UI can deep-link into chat
            // history. ``roundJustCompleted`` is the trigger for the affordance.
            lastCompletedRoundId = rid
            roundJustCompleted = (status == "completed")
            let canGenerateRecap = status == "completed" && hasAIDataConsent
            Task {
                try? await api.finishRound(roundId: rid, status: status)
                // Only generate a recap when the round was actually completed —
                // an abandoned round usually doesn't have enough data to recap
                // and burning a GPT call on every "x" tap was wasteful.
                if canGenerateRecap {
                    try? await api.generateRecap(roundId: rid)
                }
            }
        }
        stopWeatherUpdates()
        Task {
            await api.trackEvent(
                name: "round_end",
                sessionId: endedSessionId,
                roundId: endedRoundId,
                properties: ["status": status]
            )
        }
        sessionId = nil
        roundId = nil
        activeRound = nil
        currentHole = nil
        isActive = false
        conditions = ""
        weatherSummary = ""
        currentWeather = nil
        lastWeatherLat = nil
        lastWeatherLon = nil
        lastResponse = ""
        pendingScore = nil
        liveScores = []
        livePars = []
        liveYardages = [:]
        recoveryMessage = nil
    }

    // MARK: - Advice

    func getAdvice(text: String) async {
        guard sessionId != nil else { return }
        lastOperation = .getAdvice(text: text)
        guard requireAIDataConsent() else { return }
        isLoading = true
        errorMessage = nil

        await getAdviceCloud(text: text)
        isLoading = false
    }

    private func getAdviceCloud(text: String) async {
        guard let sid = sessionId else { return }
        // Push latest cached weather before advice — use cached lat/lon so a
        // temporary GPS outage doesn't block weather from reaching the backend
        if let wkData = currentWeather,
           let lat = locationManager.latitude ?? lastWeatherLat,
           let lon = locationManager.longitude ?? lastWeatherLon {
            try? await api.updateWeather(sessionId: sid, lat: lat, lon: lon, weatherKit: wkData)
        }
        do {
            await api.trackEvent(name: "advice_requested", sessionId: sid, roundId: roundId)
            let resp = try await api.getAdvice(sessionId: sid, text: text)
            lastResponse = resp.text
            cacheAdviceExchange(userText: text, assistantText: resp.text)
            await api.trackEvent(name: "advice_completed", sessionId: sid, roundId: roundId)
        } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
            if await recoverSessionIfPossible(reason: "advice") {
                await refreshState()
                if let recoveredSid = sessionId {
                    do {
                        let resp = try await api.getAdvice(sessionId: recoveredSid, text: text)
                        lastResponse = resp.text
                        cacheAdviceExchange(userText: text, assistantText: resp.text)
                        return
                    } catch {
                        presentError(error, fallback: "Unable to fetch advice after recovering your round.")
                        return
                    }
                }
            }
            await handleSessionLost()
            errorMessage = "Your round session expired and could not be recovered."
        } catch {
            presentError(error, fallback: "Unable to fetch advice right now.")
        }
    }

    /// Mirror successful advice exchanges into the on-device chat cache so the
    /// History → Round Detail screen can replay them instantly and offline.
    func cacheAdviceExchange(userText: String, assistantText: String) {
        guard let rid = roundId else { return }
        let trimmedUser = userText.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedAsst = assistantText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedUser.isEmpty, !trimmedAsst.isEmpty else { return }
        ChatCache.shared.appendLiveExchange(
            roundId: rid,
            userText: trimmedUser,
            assistantText: trimmedAsst,
            hole: currentHole
        )
    }

    // MARK: - Commands

    func runCommand(command: String, args: String = "") async {
        guard let sid = sessionId else { return }
        lastOperation = .runCommand(command: command, args: args)
        guard requireAIDataConsent() else { return }
        isLoading = true
        errorMessage = nil

        do {
            let resp = try await api.runCommand(sessionId: sid, command: command, args: args)
            lastResponse = resp.message
            await refreshState()
            if command == "newround" {
                await fetchWeather()
                roundJustCompleted = false
                lastCompletedRoundId = nil
            } else if command == "summary" {
                if let rid = roundId {
                    lastCompletedRoundId = rid
                    roundJustCompleted = true
                }
            }
        } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
            if await recoverSessionIfPossible(reason: "command_\(command)") {
                if let recoveredSid = sessionId {
                    do {
                        let resp = try await api.runCommand(sessionId: recoveredSid, command: command, args: args)
                        lastResponse = resp.message
                        await refreshState()
                        isLoading = false
                        return
                    } catch {
                        presentError(error, fallback: "Unable to run that command after recovery.")
                    }
                }
            }
            await handleSessionLost()
            errorMessage = "Your round session expired and could not be recovered."
        } catch {
            presentError(error, fallback: "Unable to run that command.")
        }
        isLoading = false
    }

    @discardableResult
    func editScore(hole: Int, strokes: Int) async -> Bool {
        guard let sid = sessionId else { return false }
        lastOperation = .editScore(hole: hole, strokes: strokes)
        isLoading = true
        errorMessage = nil
        do {
            let resp = try await api.editScore(sessionId: sid, hole: hole, strokes: strokes)
            lastResponse = resp.message
            upsertLiveScore(hole: hole, strokes: strokes)
            await refreshState()
            // Logging a score from the live scorecard is an explicit hole selection.
            // If the server's refreshState didn't advance currentHole to match
            // (e.g. user edited a past hole), reflect the user's intent locally
            // so the yellow scorecard indicator follows their tap.
            if currentHole != hole { currentHole = hole }
            isLoading = false
            return true
        } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
            if await recoverSessionIfPossible(reason: "edit_score") {
                if let recoveredSid = sessionId {
                    do {
                        let resp = try await api.editScore(sessionId: recoveredSid, hole: hole, strokes: strokes)
                        lastResponse = resp.message
                        upsertLiveScore(hole: hole, strokes: strokes)
                        await refreshState()
                        if currentHole != hole { currentHole = hole }
                        isLoading = false
                        return true
                    } catch {
                        presentError(error, fallback: "Unable to update score after recovery.")
                    }
                }
            }
            await handleSessionLost()
            errorMessage = "Your round session expired and could not be recovered."
        } catch {
            presentError(error, fallback: "Unable to update this hole score.")
        }
        isLoading = false
        return false
    }

    /// User-initiated hole selection from the live scorecard. Updates
    /// `currentHole` locally for instant visual feedback (the yellow indicator
    /// follows the tap immediately) and syncs the change to the server in the
    /// background. Unlike `runCommand("hole", ...)`, this intentionally does
    /// NOT toggle `isLoading` or overwrite `lastResponse` — a tap in the
    /// scorecard is a navigation gesture, not an advice request.
    func selectHole(_ hole: Int) {
        guard let sid = sessionId else { return }
        guard hole != currentHole else { return }
        currentHole = hole
        Task {
            do {
                _ = try await api.runCommand(sessionId: sid, command: "hole", args: String(hole))
                await refreshState()
            } catch {
                print("[selectHole] background sync failed: \(error.localizedDescription)")
            }
        }
    }

    @discardableResult
    func updateHoleStats(hole: Int, par: Int, yards: Int?) async -> Bool {
        guard let sid = sessionId else { return false }
        lastOperation = .updateHoleStats(hole: hole, par: par, yards: yards)
        let args = yards != nil ? "\(par) \(yards!)" : "\(par)"
        do {
            // holestats uses current hole; switch to the edited hole and STAY there.
            // Editing a hole in the live scorecard is an explicit hole selection by
            // the user — they expect the "current hole" indicator to follow their
            // edit. The previous swap-back to prevHole was protective when this
            // codepath was driven by passive parsing; with the tap-to-edit
            // scorecard sheet it just hid the user's intent.
            if currentHole != hole {
                _ = try await api.runCommand(sessionId: sid, command: "hole", args: "\(hole)")
            }
            _ = try await api.runCommand(sessionId: sid, command: "holestats", args: args)
            await refreshState()
            if currentHole != hole { currentHole = hole }
            return true
        } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
            if await recoverSessionIfPossible(reason: "update_hole_stats"), let recoveredSid = sessionId {
                do {
                    _ = try await api.runCommand(sessionId: recoveredSid, command: "hole", args: "\(hole)")
                    _ = try await api.runCommand(sessionId: recoveredSid, command: "holestats", args: args)
                    await refreshState()
                    if currentHole != hole { currentHole = hole }
                    return true
                } catch {
                    presentError(error, fallback: "Unable to update hole details after recovery.")
                }
            }
            await handleSessionLost()
            errorMessage = "Your round session expired and could not be recovered."
        } catch {
            presentError(error, fallback: "Unable to update hole details.")
        }
        return false
    }

    private func upsertLiveScore(hole: Int, strokes: Int) {
        let par = livePars.indices.contains(hole - 1) ? livePars[hole - 1] : 4
        let entry = RoundScoreEntry(
            hole: hole,
            strokes: strokes,
            par: par,
            yardage: liveYardages[hole]
        )
        if let index = liveScores.firstIndex(where: { $0.hole == hole }) {
            liveScores[index] = entry
        } else {
            liveScores.append(entry)
            liveScores.sort { $0.hole < $1.hole }
        }
    }

    func refreshState() async {
        guard let sid = sessionId else { return }
        lastOperation = .refreshState
        do {
            let state = try await api.getSessionState(sessionId: sid)
            currentHole = state.hole
            isActive = state.is_active
            conditions = state.conditions
            roundId = state.round_id
            liveScores = state.scores
            livePars = state.pars
            liveYardages = state.yardages

            // Cold-launched into RoundView from a persisted sessionId — make sure
            // the location/weather pipeline that startSession() normally kicks off
            // is running, otherwise the caddy gets no fresh wind data.
            if weatherTimer == nil {
                startWeatherUpdates()
            }
        } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
            if await recoverSessionIfPossible(reason: "refresh_state"), let recoveredSid = sessionId {
                do {
                    let state = try await api.getSessionState(sessionId: recoveredSid)
                    currentHole = state.hole
                    isActive = state.is_active
                    conditions = state.conditions
                    roundId = state.round_id
                    liveScores = state.scores
                    livePars = state.pars
                    liveYardages = state.yardages
                    return
                } catch {
                    await handleSessionLost()
                    errorMessage = "Session expired and recovery failed."
                }
            } else {
                await handleSessionLost()
                errorMessage = "Session expired and recovery failed."
            }
        } catch {
            // Network errors — don't drop the session, let the user retry.
            presentError(error, fallback: "Unable to refresh round state.")
        }
    }

    private func handleSessionLost() async {
        stopWeatherUpdates()
        sessionId = nil
        isActive = false
        currentHole = nil
        conditions = ""
        weatherSummary = ""
        currentWeather = nil
        liveScores = []
        livePars = []
        liveYardages = [:]
        recoveryMessage = nil
        await refreshActiveRound()
    }

    // MARK: - Continue Round (P0 cross-launch / accidental-exit recovery)

    /// Refresh the `activeRound` published value so HomeView can show or hide
    /// the Continue Round card. Safe to call repeatedly (e.g. on Home appear).
    func refreshActiveRound() async {
        do {
            let result = try await api.getActiveRound()
            activeRound = result
            if let r = result {
                roundId = r.id
                print("[AppState] refreshActiveRound → found active round id=\(r.id) holes=\(r.holes_played)")
            } else {
                print("[AppState] refreshActiveRound → no active round on server")
            }
        } catch {
            print("[AppState] refreshActiveRound failed: \(error.localizedDescription)")
            // Network errors: leave the card in its last-known state rather
            // than flicker it off on a transient failure.
        }
    }

    /// Attempt to re-enter the in-progress round.
    ///
    /// Returns `.live` if the in-memory caddy session is still warm and the
    /// router will hand the user straight back to RoundView. Returns
    /// `.expired` if the session has aged out and we need a true server-side
    /// resume (P1) — the UI should surface a friendly explanation.
    enum ContinueResult { case live, recovered, expired, noRound }

    func continueRound() async -> ContinueResult {
        guard let active = activeRound else { return .noRound }

        if let sid = sessionId {
            do {
                _ = try await api.getSessionState(sessionId: sid)
                roundId = active.id
                isActive = true
                await refreshState()
                return .live
            } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
                sessionId = nil
            } catch {
                return .expired
            }
        }
        do {
            let recovered = try await api.recoverSession(roundId: active.id)
            sessionId = recovered.session_id
            roundId = recovered.round_id
            isActive = true
            recoveryMessage = "Recovered your round through hole \(recovered.holes_played)."
            await refreshState()
            await api.trackEvent(
                name: "continue_round_recovered",
                sessionId: recovered.session_id,
                roundId: recovered.round_id,
                properties: ["holes_played": "\(recovered.holes_played)"]
            )
            return .recovered
        } catch {
            return .expired
        }
    }

    /// Resume a specific round by id (used from the History detail screen).
    ///
    /// Unlike `continueRound()`, this does not depend on `activeRound` — the user
    /// is explicitly picking a round from history, so we go straight to the
    /// server-side recovery path. On success, sets `sessionId` so the root
    /// router (KindCaddyApp) automatically transitions into RoundView.
    func resumeRound(roundId targetId: String) async -> ContinueResult {
        if let sid = sessionId, roundId == targetId {
            do {
                _ = try await api.getSessionState(sessionId: sid)
                isActive = true
                await refreshState()
                return .live
            } catch APIError.serverError(let status, _) where status == 404 || status == 410 {
                sessionId = nil
            } catch {
                return .expired
            }
        }
        do {
            let recovered = try await api.recoverSession(roundId: targetId)
            sessionId = recovered.session_id
            roundId = recovered.round_id
            isActive = true
            recoveryMessage = "Recovered your round through hole \(recovered.holes_played)."
            await refreshState()
            await api.trackEvent(
                name: "resume_round_from_history",
                sessionId: recovered.session_id,
                roundId: recovered.round_id,
                properties: ["holes_played": "\(recovered.holes_played)"]
            )
            return .recovered
        } catch {
            return .expired
        }
    }

    /// Finish the current active round on the server, then start a fresh one.
    /// Used by the "Finish & Start New" path of the Start-New conflict alert.
    func finishActiveRoundAndStartNew() async {
        if let rid = activeRound?.id ?? roundId {
            _ = try? await api.finishRound(roundId: rid, status: "completed")
        }
        activeRound = nil
        roundId = nil
        sessionId = nil
        await startSession()
    }

    // MARK: - Voice dispatch

    func handleVoiceInput(_ text: String) async {
        let lower = text.lowercased().trimmingCharacters(in: .whitespaces)

        // Pending score confirmation takes priority over all other input
        if let pending = pendingScore {
            if isConfirmation(lower) {
                pendingScore = nil
                await runCommand(command: "score", args: String(pending))
            } else if isCancellation(lower) {
                pendingScore = nil
                lastResponse = "Score cancelled."
            } else {
                let hole = currentHole.map { "hole \($0)" } ?? "this hole"
                lastResponse = "Still waiting — say yes to log \(pending) on \(hole), or no to cancel."
            }
            return
        }

        if let scoreLog = extractScoreLog(lower) {
            await editScore(hole: scoreLog.hole, strokes: scoreLog.strokes)
        } else if lower.hasPrefix("new round") || lower.hasPrefix("new around") {
            await runCommand(command: "newround")
        } else if lower.hasPrefix("hole ") {
            let holeRemainder = lower.dropFirst(5).trimmingCharacters(in: .whitespaces)
            let holeNumStr = String(holeRemainder.prefix(while: { $0.isNumber }))
            if let num = Int(holeNumStr) {
                await runCommand(command: "hole", args: String(num))
                // Apply par/yardage before refreshState so scorecard shows correct values
                if let sid = sessionId, let (par, yards) = extractHoleStats(holeRemainder) {
                    let statsArgs = yards != nil ? "\(par) \(yards!)" : "\(par)"
                    try? await api.runCommand(sessionId: sid, command: "holestats", args: statsArgs)
                    await refreshState()
                }
                // If the utterance also contains an advice question, answer it
                let hasQuestion = holeRemainder.contains("club") || holeRemainder.contains("what")
                    || holeRemainder.contains("should") || holeRemainder.contains("recommend")
                    || holeRemainder.contains("advice") || holeRemainder.contains("help")
                if hasQuestion {
                    await getAdvice(text: text)
                }
            } else {
                await getAdvice(text: text)
            }
        } else if lower.hasPrefix("weather ") {
            let args = String(lower.dropFirst(8))
            await runCommand(command: "weather", args: args)
        } else if lower.hasPrefix("altitude ") {
            let args = String(lower.dropFirst(9))
            await runCommand(command: "altitude", args: args)
        } else if lower.hasPrefix("score "), let num = Int(lower.dropFirst(6).trimmingCharacters(in: .whitespaces)) {
            let normalized = normalizeSpeechScore(num)
            let hole = currentHole.map { "hole \($0)" } ?? "this hole"
            pendingScore = normalized
            lastResponse = "Got it — logging a \(normalized) on \(hole). Say yes to confirm or no to cancel."
        } else if lower.hasPrefix("shot ") {
            let args = String(lower.dropFirst(5))
            await runCommand(command: "shot", args: args)
        } else if lower.contains("scorecard") {
            await runCommand(command: "scorecard")
        } else if lower.contains("summary") {
            await runCommand(command: "summary")
        } else if lower == "end round" || lower == "quit" || lower == "exit" {
            endSession()
        } else if let reminder = extractReminder(lower, original: text) {
            await runCommand(command: "remind", args: reminder)
        } else {
            // Silently capture hole par/yardage if mentioned in passing — await before advice
            if let sid = sessionId, let (par, yards) = extractHoleStats(lower) {
                let statsArgs = yards != nil ? "\(par) \(yards!)" : "\(par)"
                try? await api.runCommand(sessionId: sid, command: "holestats", args: statsArgs)
                await refreshState()
            }
            // Silently update wind/weather state if a wind description is detected
            if let sid = sessionId, containsWindDescription(lower) {
                Task { try? await api.runCommand(sessionId: sid, command: "weather", args: text) }
            }
            await getAdvice(text: text)
        }
    }

    private func isConfirmation(_ lower: String) -> Bool {
        let confirmWords = ["yes", "yeah", "yep", "yup", "correct", "confirm", "right", "sure"]
        return confirmWords.contains(where: { lower == $0 || lower.hasPrefix("\($0) ") })
    }

    private func isCancellation(_ lower: String) -> Bool {
        let cancelWords = ["no", "nope", "nah", "cancel", "wrong", "stop"]
        return cancelWords.contains(where: { lower == $0 || lower.hasPrefix("\($0) ") })
    }

    // MARK: - Score normalization

    /// Corrects common STT errors where a single-digit score is transcribed with
    /// trailing zeros (e.g. Whisper hears "six" → "600" or "60"). Valid hole
    /// scores are 1–15; anything outside that range is reduced by stripping
    /// trailing zeros until it fits or we run out of zeros to strip.
    private func normalizeSpeechScore(_ raw: Int) -> Int {
        var value = raw
        while value > 15 && value % 10 == 0 {
            value /= 10
        }
        return value
    }

    /// Handles explicit score statements such as "I scored 4 on hole 4" or
    /// "hole 4 score 4". These should persist directly instead of falling
    /// through to generic advice.
    private func extractScoreLog(_ text: String) -> (hole: Int, strokes: Int)? {
        let normalizedText = replaceSpokenNumbers(in: text)
        let patterns: [(pattern: String, strokesGroup: Int, holeGroup: Int)] = [
            (#"\b(?:i\s+)?(?:scored|score|shot|made|got|had|carded)\s+(?:a\s+)?(\d{1,2})\b.*?\b(?:on|for)\s+hole\s+(\d{1,2})\b"#, 1, 2),
            (#"\bhole\s+(\d{1,2})\b.*?\b(?:score|scored|shot|made|got|had|carded)\s+(?:a\s+)?(\d{1,2})\b"#, 2, 1)
        ]

        for item in patterns {
            guard let match = firstRegexMatch(item.pattern, in: normalizedText),
                  match.indices.contains(item.strokesGroup),
                  match.indices.contains(item.holeGroup),
                  let rawStrokes = Int(match[item.strokesGroup]),
                  let hole = Int(match[item.holeGroup])
            else { continue }

            let strokes = normalizeSpeechScore(rawStrokes)
            if (1...18).contains(hole), (1...15).contains(strokes) {
                return (hole, strokes)
            }
        }
        return nil
    }

    private func replaceSpokenNumbers(in text: String) -> String {
        var result = text
        let replacements = [
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18
        ]
        for (word, value) in replacements {
            result = result.replacingOccurrences(
                of: #"\b\#(word)\b"#,
                with: "\(value)",
                options: .regularExpression
            )
        }
        return result
    }

    private func firstRegexMatch(_ pattern: String, in text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return nil }
        let nsText = text as NSString
        let fullRange = NSRange(location: 0, length: nsText.length)
        guard let match = regex.firstMatch(in: text, range: fullRange) else { return nil }
        return (0..<match.numberOfRanges).map { index in
            let range = match.range(at: index)
            return range.location == NSNotFound ? "" : nsText.substring(with: range)
        }
    }

    /// Extracts par and optional yardage from natural speech.
    /// Returns nil if no par is detected (yardage alone is not enough to act on).
    private func extractHoleStats(_ text: String) -> (par: Int, yards: Int?)? {
        let parWords = ["three": 3, "four": 4, "five": 5, "3": 3, "4": 4, "5": 5]
        var detectedPar: Int? = nil
        for (word, val) in parWords {
            if text.range(of: "par ?\(word)", options: .regularExpression) != nil {
                detectedPar = val
                break
            }
        }
        guard let par = detectedPar else { return nil }

        var detectedYards: Int? = nil
        if let match = text.range(of: #"(\d{2,3})\s*yards?"#, options: .regularExpression) {
            let digits = text[match].filter(\.isNumber)
            if let y = Int(digits), (50...700).contains(y) {
                detectedYards = y
            }
        }
        return (par, detectedYards)
    }

    /// Extracts a reminder note from natural speech.
    /// Strips the trigger phrase and returns the core reminder text,
    /// or nil if no reminder trigger is detected.
    private func extractReminder(_ lower: String, original: String) -> String? {
        let triggers = [
            "remind me that ", "remind me to ", "remind me ",
            "remember that ", "remember to ",
            "note that ", "keep in mind that ", "keep in mind ",
            "don't let me forget ", "make a note that ", "make a note ",
        ]
        for trigger in triggers {
            if lower.hasPrefix(trigger) {
                let remainder = String(original.dropFirst(trigger.count)).trimmingCharacters(in: .whitespaces)
                if !remainder.isEmpty {
                    return remainder
                }
            }
        }
        return nil
    }

    /// Returns true when the text contains a wind/weather description worth capturing.
    /// Used to silently update server state when the user mentions wind mid-sentence.
    private func containsWindDescription(_ text: String) -> Bool {
        let windTriggers = [
            "mph", "mile/hour", "miles per hour", "miles an hour",
            "wind blowing", "gusty", "headwind", "tailwind", "crosswind",
            "into me", "from behind", "left to right", "right to left",
            "from the left", "from the right", "helping wind", "against the wind"
        ]
        return windTriggers.contains(where: { text.contains($0) })
    }

    // MARK: - Weather

    func startWeatherUpdates() {
        locationManager.requestPermission()
        locationManager.startUpdating()

        // Try immediately with cached GPS if available
        Task { @MainActor in await self.fetchWeather() }

        // React the moment GPS first provides a fix — don't wait for the 60s timer
        locationObserver = locationManager.$latitude
            .compactMap { $0 }
            .first()
            .sink { [weak self] _ in
                guard let self else { return }
                Task { @MainActor in
                    if self.currentWeather == nil { await self.fetchWeather() }
                }
            }

        // Refresh every 60 seconds — always try, let fetchWeather guard handle unavailable GPS
        weatherTimer = Timer.publish(every: 60, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self else { return }
                Task { @MainActor in await self.fetchWeather() }
            }
    }

    func stopWeatherUpdates() {
        weatherTimer?.cancel()
        weatherTimer = nil
        locationObserver?.cancel()
        locationObserver = nil
        locationManager.stopUpdating()
    }

    private func fetchWeather() async {
        guard let sid = sessionId else { return }
        let lat = locationManager.latitude ?? lastWeatherLat
        let lon = locationManager.longitude ?? lastWeatherLon
        guard let lat, let lon else { return }

        do {
            let wkData = try await weatherService.fetch(latitude: lat, longitude: lon)
            weatherSummary = wkData.summary
            conditions = wkData.summary
            currentWeather = wkData
            lastWeatherLat = lat
            lastWeatherLon = lon
            do {
                _ = try await api.updateWeather(
                    sessionId: sid, lat: lat, lon: lon, weatherKit: wkData
                )
            } catch {
                try await syncWeatherFromServer(sessionId: sid, lat: lat, lon: lon)
            }
        } catch {
            try? await syncWeatherFromServer(sessionId: sid, lat: lat, lon: lon)
        }
    }

    /// When WeatherKit fails (simulator, missing entitlement) or the push fails, ask the API to
    /// fetch Open-Meteo using GPS — same data the backend uses for `round_state` and the model.
    private func syncWeatherFromServer(sessionId sid: String, lat: Double, lon: Double) async throws {
        let resp = try await api.updateWeather(sessionId: sid, lat: lat, lon: lon, weatherKit: nil)
        let wx = WeatherKitData(fromServer: resp)
        weatherSummary = resp.summary
        conditions = resp.summary
        currentWeather = wx
        lastWeatherLat = lat
        lastWeatherLon = lon
    }

    // MARK: - Persistence

    private func saveProfile() {
        if let data = try? JSONEncoder().encode(profile) {
            UserDefaults.standard.set(data, forKey: "golferProfile")
        }
    }
}
