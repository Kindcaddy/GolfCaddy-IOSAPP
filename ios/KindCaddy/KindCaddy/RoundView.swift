import AVFoundation
import MediaPlayer
import SwiftUI
import UIKit
import UserNotifications

@MainActor
final class HeadsetAudioRecorder: ObservableObject {
    @Published private(set) var isRecording = false

    private var audioEngine: AVAudioEngine?
    private var audioFile: AVAudioFile?
    private var recordingURL: URL?
    private let meterQueue = DispatchQueue(label: "KindCaddy.HeadsetAudioRecorder.meter")
    private var lastVoiceTime: CFAbsoluteTime = CFAbsoluteTimeGetCurrent()
    private var recordingStartTime: CFAbsoluteTime = CFAbsoluteTimeGetCurrent()

    private let voiceThresholdRMS: Float = 0.01

    func startRecording() throws {
        discardRecordingFile()
        stopRecording()

        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw RecorderError.inputUnavailable
        }

        let filename = "kindcaddy_headset_\(UUID().uuidString).wav"
        let fileURL = FileManager.default.temporaryDirectory.appendingPathComponent(filename)
        let file = try AVAudioFile(forWriting: fileURL, settings: format.settings)

        meterQueue.sync {
            let now = CFAbsoluteTimeGetCurrent()
            lastVoiceTime = now
            recordingStartTime = now
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self else { return }
            do {
                try file.write(from: buffer)
            } catch {
                print("[HeadsetAudioRecorder] file write failed: \(error)")
            }
            if Self.hasVoice(in: buffer, threshold: self.voiceThresholdRMS) {
                self.meterQueue.async {
                    self.lastVoiceTime = CFAbsoluteTimeGetCurrent()
                }
            }
        }

        engine.prepare()
        try engine.start()
        audioEngine = engine
        audioFile = file
        recordingURL = fileURL
        isRecording = true
        print("[HeadsetAudioRecorder] recording started")
    }

    func stopRecording() {
        guard isRecording else { return }
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil
        audioFile = nil
        isRecording = false
        print("[HeadsetAudioRecorder] recording stopped")
    }

    func stopRecordingAndGetURL() -> URL? {
        guard isRecording else { return nil }
        stopRecording()
        return recordingURL
    }

    func discardRecordingFile() {
        guard let url = recordingURL else { return }
        try? FileManager.default.removeItem(at: url)
        recordingURL = nil
    }

    func secondsSinceVoice() -> TimeInterval {
        meterQueue.sync {
            CFAbsoluteTimeGetCurrent() - lastVoiceTime
        }
    }

    func elapsedRecordingSeconds() -> TimeInterval {
        meterQueue.sync {
            CFAbsoluteTimeGetCurrent() - recordingStartTime
        }
    }

    private static func hasVoice(in buffer: AVAudioPCMBuffer, threshold: Float) -> Bool {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return false }
        let channels = Int(buffer.format.channelCount)
        if let channelData = buffer.floatChannelData {
            var sumSquares: Float = 0
            for ch in 0..<channels {
                let samples = channelData[ch]
                var channelSum: Float = 0
                for i in 0..<frameCount {
                    let sample = samples[i]
                    channelSum += sample * sample
                }
                sumSquares += channelSum / Float(frameCount)
            }
            let rms = sqrt(sumSquares / Float(max(channels, 1)))
            return rms >= threshold
        }
        if let channelData = buffer.int16ChannelData {
            var sumSquares: Float = 0
            for ch in 0..<channels {
                let samples = channelData[ch]
                var channelSum: Float = 0
                for i in 0..<frameCount {
                    let normalized = Float(samples[i]) / Float(Int16.max)
                    channelSum += normalized * normalized
                }
                sumSquares += channelSum / Float(frameCount)
            }
            let rms = sqrt(sumSquares / Float(max(channels, 1)))
            return rms >= threshold
        }
        return false
    }
}

enum RecorderError: LocalizedError {
    case inputUnavailable

    var errorDescription: String? {
        switch self {
        case .inputUnavailable:
            return "Microphone input unavailable"
        }
    }
}

struct RoundView: View {
    @EnvironmentObject var appState: AppState
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var speechRecognizer = SpeechRecognizer()
    @StateObject private var headsetRemote = HeadsetRemoteCoordinator()
    @StateObject private var headsetAudioRecorder = HeadsetAudioRecorder()
    @State private var audioPlayer: AVAudioPlayer?
    @State private var fallbackSynthesizer: AVSpeechSynthesizer?
    @State private var isSpeaking = false
    /// Monotonic counter incremented on every speakResponse() invocation.
    /// In-flight TTS Tasks compare their captured generation to the latest
    /// before playing — stale tasks (because a newer response arrived during
    /// network round-trip) abort silently instead of overlapping audio.
    @State private var ttsGeneration: Int = 0
    @State private var showingSettings = false
    @State private var micPulse = false
    @State private var headsetRecordingActive = false
    @State private var silenceTimer: Task<Void, Never>?
    @State private var pendingStartTask: Task<Void, Never>?
    @State private var showScorecard = false
    @State private var showChatHistory = false

    // Phone-mic tap-to-toggle silence detection. Mirrors the AirPods squeeze
    // pattern but watches transcript stability instead of mic RMS, since
    // SFSpeechRecognizer manages its own audio engine and doesn't expose level.
    @State private var phoneMicStartTime: Date = Date()
    @State private var phoneMicLastTranscriptChange: Date = Date()
    @State private var phoneMicLastTranscript: String = ""

    // Just-in-time tooltips. Each shows once per device, gated by UserDefaults.
    @State private var showMicTip = false
    @State private var showScorecardTip = false
    // AirPods tutorial uses a 2-step walkthrough: 0 = hidden, 1 = card 1, 2 = card 2.
    // Auto-fires once per device on first appearance; the dedicated button on
    // the right of the mic re-opens it on demand for users who want a refresher.
    @State private var airpodsTipStep: Int = 0

    private static let micTipSeenKey = "kindcaddy.seenMicTip"
    private static let scorecardTipSeenKey = "kindcaddy.seenScorecardTip"
    private static let airpodsTipSeenKey = "kindcaddy.seenAirPodsTip"
    private static let phoneMicSilenceSeconds: TimeInterval = 1.8
    private static let phoneMicMinElapsedSeconds: TimeInterval = 1.0
    private static let phoneMicHardCapSeconds: TimeInterval = 12.0

    // Ambient mode: dim the screen after 30s of no on-screen interaction so a
    // round-long, idle-timer-disabled session does not destroy battery / get hot.
    // Paired with `UIApplication.isIdleTimerDisabled = true` in `.onAppear`.
    @State private var lastInteractionTime: Date = Date()
    @State private var isAmbientMode: Bool = false
    @State private var savedBrightness: CGFloat = UIScreen.main.brightness
    @State private var ambientMonitorTask: Task<Void, Never>?

    private static let ambientIdleSeconds: TimeInterval = 30
    private static let ambientBrightness: CGFloat = 0.05

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    statusBar
                    weatherStrip

                    Divider()
                        .overlay(Theme.border)

                    responseArea

                    liveTranscript

                    Divider()
                        .overlay(Theme.border)

                    controlsBar
                }
            }
            .navigationTitle("KindCaddy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showScorecard = true
                    } label: {
                        Label("Scorecard", systemImage: "list.number")
                            .foregroundStyle(Theme.accent)
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await appState.runCommand(command: "newround") }
                    } label: {
                        Label("New Round", systemImage: "arrow.counterclockwise")
                            .foregroundStyle(Theme.accent)
                    }
                }
            }
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in resetAmbientTimer() }
            )
            .onAppear {
                speechRecognizer.requestAuthorization()
                appState.requestNotificationPermissionIfNeeded()
                // Pre-warm .playAndRecord so the mic route is configured in the audio hardware.
                // The coordinator then switches to .playback for remote commands.
                // Later switches back to .playAndRecord settle instantly because the route is cached.
                headsetRemote.switchToRecordingSession()
                headsetRemote.activate()
                updateNowPlaying()
                Task { await appState.refreshState() }
                UIApplication.shared.isIdleTimerDisabled = true
                startAmbientMonitor()
                showAirPodsTipIfNeeded()
            }
            .onDisappear {
                headsetRemote.deactivate()
                MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
                speechRecognizer.teardownAudioSession()
                headsetAudioRecorder.stopRecording()
                headsetAudioRecorder.discardRecordingFile()
                stopAmbientMonitor()
                UIApplication.shared.isIdleTimerDisabled = false
            }
            .onReceive(NotificationCenter.default.publisher(for: .kindCaddyHeadsetMicToggle)) { _ in
                handleHeadsetMicToggle()
            }
            .onChange(of: appState.currentHole) { _ in
                updateNowPlaying()
            }
            .onChange(of: scenePhase) { newPhase in
                handleScenePhaseChange(newPhase)
            }
            .overlay(alignment: .bottom) {
                if showMicTip {
                    TooltipBubble(
                        text: "Tap again to send. KindCaddy auto-sends after a short silence."
                    ) {
                        dismissMicTip()
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 110)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
            .overlay(alignment: .top) {
                if showScorecardTip {
                    TooltipBubble(
                        text: "Tap any hole to log score, par, and yardage."
                    ) {
                        dismissScorecardTip()
                    }
                    .padding(.horizontal, 24)
                    .padding(.top, 8)
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .overlay(alignment: .bottom) {
                if airpodsTipStep > 0 {
                    TooltipBubble(
                        text: airpodsTipStep == 1
                            ? "Wearing AirPods? Squeeze the stem once — your hands stay on your clubs."
                            : "Ask your question, then pause. KindCaddy auto-sends and replies straight to your ears.",
                        buttonText: airpodsTipStep == 1 ? "Next" : "Got it",
                        stepIndicator: "\(airpodsTipStep) of 2"
                    ) {
                        advanceAirPodsTip()
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 110)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
            }
            .sheet(isPresented: $showScorecard) {
                LiveScorecardSheet(appState: appState)
            }
            .sheet(isPresented: $showChatHistory) {
                if let rid = appState.lastCompletedRoundId {
                    NavigationStack {
                        RoundDetailView(roundId: rid, scrollTo: .chat)
                            .environmentObject(appState)
                            .toolbar {
                                ToolbarItem(placement: .topBarTrailing) {
                                    Button("Done") { showChatHistory = false }
                                        .foregroundStyle(Theme.accent)
                                }
                            }
                    }
                }
            }
            .onChange(of: showScorecard) { isOpen in
                if isOpen {
                    showScorecardTipIfNeeded()
                }
            }
            .onChange(of: speechRecognizer.transcript) { newTranscript in
                if newTranscript != phoneMicLastTranscript {
                    phoneMicLastTranscript = newTranscript
                    phoneMicLastTranscriptChange = Date()
                }
            }
            .alert("Error", isPresented: .init(
                get: { appState.errorMessage != nil },
                set: { if !$0 { appState.errorMessage = nil } }
            )) {
                Button("Retry") {
                    Task { await appState.retryLastOperation() }
                }
                Button("OK") { appState.errorMessage = nil }
            } message: {
                Text(appState.errorMessage ?? "")
            }
        }
    }

    // MARK: - Just-in-time Tooltips
    //
    // The previous big upfront tutorial overlay was replaced with two small
    // contextual tooltips that show ONCE per device:
    //   - Mic tip: appears the first time the user starts a recording
    //   - Scorecard tip: appears the first time the scorecard sheet opens
    // Each is dismissed by tap, by an in-flow auto-timer, or by an explicit
    // user action (sending a recording / closing the sheet).

    private func dismissMicTip() {
        guard showMicTip else { return }
        UserDefaults.standard.set(true, forKey: Self.micTipSeenKey)
        withAnimation { showMicTip = false }
    }

    private func showScorecardTipIfNeeded() {
        guard !UserDefaults.standard.bool(forKey: Self.scorecardTipSeenKey) else { return }
        withAnimation { showScorecardTip = true }
        Task {
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            await MainActor.run { dismissScorecardTip() }
        }
    }

    private func dismissScorecardTip() {
        guard showScorecardTip else { return }
        UserDefaults.standard.set(true, forKey: Self.scorecardTipSeenKey)
        withAnimation { showScorecardTip = false }
    }

    /// Auto-fires once per device when the round screen first appears, so
    /// AirPods users discover the squeeze-to-talk affordance without having to
    /// hunt for it. The earbuds button on the right of the mic re-opens it.
    private func showAirPodsTipIfNeeded() {
        guard !UserDefaults.standard.bool(forKey: Self.airpodsTipSeenKey) else { return }
        // Slight delay so it appears after the screen has settled, not jammed
        // against the slide-in animation of the round view.
        Task {
            try? await Task.sleep(nanoseconds: 600_000_000)
            await MainActor.run {
                guard airpodsTipStep == 0 else { return }
                withAnimation { airpodsTipStep = 1 }
            }
        }
    }

    private func advanceAirPodsTip() {
        if airpodsTipStep == 1 {
            withAnimation { airpodsTipStep = 2 }
        } else {
            withAnimation { airpodsTipStep = 0 }
            UserDefaults.standard.set(true, forKey: Self.airpodsTipSeenKey)
        }
    }

    // MARK: - Status Bar

    private var statusBar: some View {
        HStack(spacing: 0) {
            if let hole = appState.currentHole {
                HStack(spacing: 6) {
                    Image(systemName: "flag.fill")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Theme.accent)
                    Text("HOLE \(hole)")
                        .font(.system(size: 13, weight: .bold, design: .serif))
                        .tracking(1.2)
                        .foregroundStyle(Theme.accent)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Theme.accent.opacity(0.12))
                .clipShape(Capsule())
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Theme.surfaceBackground)
    }

    // MARK: - Weather Strip

    @ViewBuilder
    private var weatherStrip: some View {
        if let wx = appState.currentWeather {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 16) {
                    // Temperature
                    HStack(spacing: 4) {
                        Image(systemName: weatherIcon(wx.description))
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(Theme.accent)
                        Text(String(format: "%.0f°F", wx.tempF))
                            .font(.system(size: 13, weight: .semibold, design: .serif))
                            .foregroundStyle(Theme.textPrimary)
                    }

                    // Wind
                    if wx.windSpeedMph > 1 {
                        HStack(spacing: 4) {
                            Image(systemName: "wind")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundStyle(Theme.textSecondary)
                            Text(String(format: "%.0f mph %@", wx.windSpeedMph, windCompass(wx.windDeg)))
                                .font(.system(size: 13, design: .serif))
                                .foregroundStyle(Theme.textSecondary)
                            if wx.windGustMph > wx.windSpeedMph + 3 {
                                Text(String(format: "gusts %.0f", wx.windGustMph))
                                    .font(.system(size: 11, design: .serif))
                                    .foregroundStyle(Theme.textTertiary)
                            }
                        }
                    }

                    Spacer()

                    // Condition label
                    Text(wx.description.capitalized)
                        .font(.system(size: 11, design: .serif))
                        .foregroundStyle(Theme.textTertiary)
                }

                WeatherAttributionView(source: wx.source, compact: true)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(Theme.surfaceBackground)
        } else if appState.isActive {
            HStack(spacing: 6) {
                Image(systemName: "location.slash")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.textTertiary)
                Text(appState.locationManager.locationError ?? "Waiting for GPS to load weather…")
                    .font(.system(size: 12, design: .serif))
                    .foregroundStyle(Theme.textTertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(Theme.surfaceBackground)
        }
    }

    private func weatherIcon(_ description: String) -> String {
        let d = description.lowercased()
        if d.contains("thunder") { return "cloud.bolt.fill" }
        if d.contains("snow") || d.contains("flurr") { return "snow" }
        if d.contains("rain") || d.contains("drizzle") { return "cloud.rain.fill" }
        if d.contains("fog") || d.contains("haze") { return "cloud.fog.fill" }
        if d.contains("overcast") || d.contains("cloudy") { return "cloud.fill" }
        if d.contains("partly") { return "cloud.sun.fill" }
        if d.contains("clear") || d.contains("mainly") { return "sun.max.fill" }
        if d.contains("wind") || d.contains("breez") { return "wind" }
        return "cloud.sun.fill"
    }

    private func windCompass(_ deg: Double) -> String {
        let directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return directions[Int((deg / 45).rounded()) % 8]
    }

    // MARK: - Response Area

    private var responseArea: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if appState.lastResponse.isEmpty {
                    VStack(spacing: 16) {
                        ZStack {
                            Circle()
                                .fill(Theme.accent.opacity(0.08))
                                .frame(width: 80, height: 80)
                            Image(systemName: "mic.fill")
                                .font(.system(size: 32, weight: .medium))
                                .foregroundStyle(Theme.accentDimmed)
                        }
                        VStack(spacing: 6) {
                            Text("Your Caddy Is Ready")
                                .font(Theme.sectionTitle)
                                .foregroundStyle(Theme.textPrimary)
                            Text("Tap the mic and ask about your next shot")
                                .font(Theme.captionSerif)
                                .foregroundStyle(Theme.textSecondary)
                                .multilineTextAlignment(.center)
                        }
                    }
                    .padding(.horizontal, 32)
                    .frame(maxWidth: .infinity)
                    .frame(maxHeight: .infinity)
                    .padding(.top, 80)
                } else {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(Self.stripMarkdown(appState.lastResponse))
                            .font(Theme.bodySerif)
                            .foregroundStyle(Theme.textPrimary)
                            .lineSpacing(6)
                            .padding(.horizontal, 20)
                            .padding(.vertical, 16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id("response")

                        if appState.roundJustCompleted, appState.lastCompletedRoundId != nil {
                            viewChatHistoryButton
                                .padding(.horizontal, 20)
                                .padding(.bottom, 8)
                        }
                    }
                }
            }
            .onChange(of: appState.lastResponse) {
                withAnimation {
                    proxy.scrollTo("response", anchor: .top)
                }
            }
        }
    }

    /// Post-round affordance: opens the just-finished round in `RoundDetailView`,
    /// scrolled to the conversation section. Visible after the recap arrives
    /// (via "summary" command) so the user can review the back-and-forth that
    /// led to their score before they walk off the 18th green.
    private var viewChatHistoryButton: some View {
        Button {
            showChatHistory = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "bubble.left.and.bubble.right")
                    .font(.system(size: 14, weight: .semibold))
                Text("View Chat History")
                    .font(.system(size: 14, weight: .semibold, design: .serif))
            }
            .foregroundStyle(Theme.accent)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Theme.accent.opacity(0.12))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Theme.accent.opacity(0.35), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }

    // MARK: - Live Transcript

    @ViewBuilder
    private var liveTranscript: some View {
        if speechRecognizer.isRecording && !speechRecognizer.transcript.isEmpty {
            HStack(spacing: 8) {
                Circle()
                    .fill(Theme.micRecording)
                    .frame(width: 6, height: 6)
                Text(speechRecognizer.transcript)
                    .font(.system(size: 14, weight: .regular, design: .serif).italic())
                    .foregroundStyle(Theme.textPrimary.opacity(0.85))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Theme.surfaceBackground)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    // MARK: - Controls Bar

    private var controlsBar: some View {
        HStack(spacing: 0) {
            // Exit/Pause: leaves the round in the "active" state on the server
            // so the user can resume from Home or History. Use voice "end round"
            // to actually finish (mark abandoned), or "Complete Round" from the
            // round-detail screen to mark completed and generate a recap.
            Button {
                appState.pauseSession()
            } label: {
                VStack(spacing: 4) {
                    Image(systemName: "xmark")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                }
                .frame(width: 52, height: 52)
                .background(Theme.cardBackground)
                .clipShape(Circle())
                .overlay(Circle().strokeBorder(Theme.border, lineWidth: 1))
            }

            Button {
                sendFeedback()
            } label: {
                VStack(spacing: 4) {
                    Image(systemName: "paperplane")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                }
                .frame(width: 44, height: 44)
                .background(Theme.cardBackground)
                .clipShape(Circle())
                .overlay(Circle().strokeBorder(Theme.border, lineWidth: 1))
            }

            Spacer()

            micButton

            Spacer()

            // Right-side slot: AirPods tutorial button (mirrors the X on the left
            // for symmetry). When a request is in-flight, a loading spinner overlays
            // the button — the icon dims but stays visible so the slot doesn't
            // collapse and the mic stays optically centered. The manual
            // replay/speaker button was removed in favor of auto-speak on every
            // response — see speakResponse() — to eliminate double-voice.
            airpodsTutorialButton
                .overlay {
                    if appState.isLoading {
                        ZStack {
                            Circle()
                                .fill(Theme.cardBackground.opacity(0.85))
                                .frame(width: 52, height: 52)
                            ProgressView()
                                .tint(Theme.accent)
                                .scaleEffect(0.8)
                        }
                    }
                }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 20)
        .background(Theme.surfaceBackground)
    }

    private func sendFeedback() {
        let url = SupportLinks.feedbackURL(
            userId: nil,
            sessionId: appState.sessionId,
            roundId: appState.roundId
        )
        guard let url else {
            appState.errorMessage = "Unable to open email app for feedback."
            return
        }
        openURL(url)
        Task {
            await APIClient.shared.trackEvent(
                name: "feedback_tapped_in_round",
                sessionId: appState.sessionId,
                roundId: appState.roundId
            )
        }
    }

    // MARK: - AirPods Tutorial Button

    /// Sits opposite the X (pause) button. Tap to (re)open the 2-step AirPods
    /// walkthrough. Mirrors the X dimensions (52pt circle) so the controls bar
    /// stays visually balanced around the mic.
    private var airpodsTutorialButton: some View {
        Button {
            withAnimation { airpodsTipStep = 1 }
        } label: {
            Image(systemName: "earbuds")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 52, height: 52)
                .background(Theme.cardBackground)
                .clipShape(Circle())
                .overlay(Circle().strokeBorder(Theme.border, lineWidth: 1))
        }
        .accessibilityLabel("AirPods tips")
        .disabled(appState.isLoading)
    }

    // MARK: - Mic Button

    private var micButton: some View {
        // Reflect both on-screen long-press recording (`speechRecognizer.isRecording`) AND
        // AirPods squeeze recording (`headsetRecordingActive`). The latter is set
        // synchronously the moment a squeeze fires, so the user gets immediate visual
        // confirmation that their squeeze was registered, well before the orange dot
        // status-bar indicator could tell them the same thing.
        let isRecording = speechRecognizer.isRecording || headsetRecordingActive
        let ringColor = isRecording ? Theme.micRecording : Theme.micIdle

        return ZStack {
            Circle()
                .stroke(ringColor.opacity(micPulse && isRecording ? 0.2 : 0.4), lineWidth: micPulse && isRecording ? 24 : 4)
                .frame(width: 80, height: 80)
                .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: micPulse)

            Circle()
                .fill(Theme.cardBackground)
                .frame(width: 72, height: 72)

            Circle()
                .stroke(ringColor, lineWidth: 3)
                .frame(width: 72, height: 72)

            Image(systemName: isRecording ? "mic.fill" : "mic")
                .font(.title)
                .foregroundStyle(isRecording ? Theme.micRecording : Theme.textPrimary)
        }
        .scaleEffect(isRecording ? 1.08 : 1.0)
        .animation(.easeInOut(duration: 0.2), value: isRecording)
        .shadow(color: ringColor.opacity(isRecording ? 0.3 : 0.1), radius: isRecording ? 12 : 4)
        .contentShape(Circle())
        .onTapGesture {
            handleOnScreenMicToggle()
        }
    }

    /// Tap-to-toggle on-screen mic. Mirrors the AirPods squeeze flow:
    /// first tap starts recording with auto-stop on silence (1.8s) and a 12s
    /// hard cap; second tap stops and sends immediately.
    private func handleOnScreenMicToggle() {
        guard ensureAIConsentBeforeVoice() else { return }
        if speechRecognizer.isRecording {
            cancelPendingStart()
            cancelSilenceTimer()
            micPulse = false
            stopListeningAndSend()
            return
        }
        showMicTipIfNeeded()
        micPulse = true
        startListeningWithRouteSettle(delayNs: 180_000_000) {
            startPhoneMicSilenceTimer()
        }
    }

    private func showMicTipIfNeeded() {
        guard !UserDefaults.standard.bool(forKey: Self.micTipSeenKey) else { return }
        withAnimation { showMicTip = true }
        Task {
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            await MainActor.run { dismissMicTip() }
        }
    }

    /// Auto-send when the live transcript stops changing for ~1.8s, mirroring
    /// the AirPods silence-detection behavior. Hard caps recording at 12s
    /// so a stuck mic does not silently drain battery / get stuck on screen.
    private func startPhoneMicSilenceTimer() {
        silenceTimer?.cancel()
        let now = Date()
        phoneMicStartTime = now
        phoneMicLastTranscriptChange = now
        phoneMicLastTranscript = ""
        silenceTimer = Task { @MainActor in
            // Initial settle window — give the user time to start speaking before
            // the silence detector has any teeth.
            try? await Task.sleep(nanoseconds: 1_400_000_000)
            while !Task.isCancelled, speechRecognizer.isRecording {
                let elapsed = Date().timeIntervalSince(phoneMicStartTime)
                let sinceChange = Date().timeIntervalSince(phoneMicLastTranscriptChange)
                let exceededSilence = sinceChange >= Self.phoneMicSilenceSeconds
                    && elapsed >= Self.phoneMicMinElapsedSeconds
                let exceededHardCap = elapsed >= Self.phoneMicHardCapSeconds
                if exceededSilence || exceededHardCap {
                    print("[PhoneMic] silence/cap reached — auto-sending (elapsed=\(elapsed), sinceChange=\(sinceChange))")
                    cancelPendingStart()
                    micPulse = false
                    stopListeningAndSend()
                    return
                }
                try? await Task.sleep(nanoseconds: 400_000_000)
            }
        }
    }

    // MARK: - Headset / AirPods remote

    /// Phone mic: hold to talk, release to send. AirPods: no press/release API — squeeze once to start, again to stop.
    private func handleHeadsetMicToggle() {
        print("[MicToggle] called — headsetRecordingActive: \(headsetRecordingActive), scenePhase: \(scenePhase)")
        guard ensureAIConsentBeforeVoice() else { return }

        // iOS does not allow third-party apps to start mic capture while the device is locked.
        // If we attempt it, the failure cascade leaves our audio session in a stuck state and
        // the user has to restart the entire session before squeeze works again. Short-circuit
        // here BEFORE touching the audio session, and surface a tap-to-resume notification.
        if scenePhase != .active {
            print("[MicToggle] app not active — skipping mic start, posting resume notification")
            postResumeNotification(reason: .squeezeWhileLocked)
            return
        }

        if headsetRecordingActive {
            cancelPendingStart()
            cancelSilenceTimer()
            micPulse = false
            headsetRecordingActive = false
            finishHeadsetRecording()
        } else {
            micPulse = true
            headsetRecordingActive = true
            startHeadsetRecordingWithRouteSettle(delayNs: 250_000_000) { restartSilenceTimer() }
        }
    }

    // MARK: - Headset silence detection

    private func restartSilenceTimer() {
        silenceTimer?.cancel()
        silenceTimer = Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled, headsetRecordingActive, headsetAudioRecorder.isRecording else { return }

            while !Task.isCancelled, headsetRecordingActive, headsetAudioRecorder.isRecording {
                let elapsed = headsetAudioRecorder.elapsedRecordingSeconds()
                let sinceVoice = headsetAudioRecorder.secondsSinceVoice()
                let exceededSilenceWindow = sinceVoice >= 1.8 && elapsed >= 1.0
                let exceededHardCap = elapsed >= 12.0
                if exceededSilenceWindow || exceededHardCap {
                    print("[Headset] silence timer fired — auto-sending")
                    headsetRecordingActive = false
                    micPulse = false
                    finishHeadsetRecording()
                    return
                }
                try? await Task.sleep(nanoseconds: 400_000_000)
            }
        }
    }

    private func cancelSilenceTimer() {
        silenceTimer?.cancel()
        silenceTimer = nil
    }

    private func cancelPendingStart() {
        pendingStartTask?.cancel()
        pendingStartTask = nil
    }

    private func startListeningWithRouteSettle(delayNs: UInt64, afterStart: (() -> Void)? = nil) {
        cancelPendingStart()
        _ = headsetRemote.switchToRecordingSession()
        pendingStartTask = Task {
            try? await Task.sleep(nanoseconds: delayNs)
            guard !Task.isCancelled else { return }
            attemptStartListeningWithRetry(retriesRemaining: 2, retryDelayNs: 700_000_000, afterStart: afterStart)
        }
    }

    private func attemptStartListeningWithRetry(
        retriesRemaining: Int,
        retryDelayNs: UInt64,
        afterStart: (() -> Void)?
    ) {
        let shouldShowError = retriesRemaining == 0
        if startListening(showError: shouldShowError) {
            afterStart?()
            return
        }
        guard retriesRemaining > 0 else {
            headsetRecordingActive = false
            micPulse = false
            return
        }
        print("[StartListening] retrying after route settle")
        pendingStartTask = Task {
            _ = headsetRemote.switchToRecordingSession()
            try? await Task.sleep(nanoseconds: retryDelayNs)
            guard !Task.isCancelled else { return }
            attemptStartListeningWithRetry(
                retriesRemaining: retriesRemaining - 1,
                retryDelayNs: retryDelayNs,
                afterStart: afterStart
            )
        }
    }

    private func finishHeadsetRecording() {
        cancelSilenceTimer()
        let recordedFileURL = headsetAudioRecorder.stopRecordingAndGetURL()
        headsetRemote.switchToPlaybackSession()
        guard let sessionId = appState.sessionId else {
            headsetAudioRecorder.discardRecordingFile()
            return
        }
        guard let recordedFileURL else {
            headsetAudioRecorder.discardRecordingFile()
            return
        }

        Task {
            await sendHeadsetAudioForAdvice(sessionId: sessionId, recordedFileURL: recordedFileURL)
        }
    }

    private func sendHeadsetAudioForAdvice(sessionId: String, recordedFileURL: URL) async {
        defer { headsetAudioRecorder.discardRecordingFile() }
        guard appState.hasAIDataConsent else {
            appState.requestAIDataConsent()
            return
        }
        appState.errorMessage = nil
        appState.isLoading = true
        do {
            let response = try await APIClient.shared.transcribeAudio(
                sessionId: sessionId,
                audioFileURL: recordedFileURL
            )
            appState.lastResponse = response.advice_text
            appState.cacheAdviceExchange(
                userText: response.transcript,
                assistantText: response.advice_text
            )
            print("[Headset] transcript: \(response.transcript)")
            updateNowPlaying()
            if !response.advice_text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                dismissMicTip()
                speakResponse()
            }
        } catch {
            appState.errorMessage = error.localizedDescription
        }
        appState.isLoading = false
    }

    private func startHeadsetRecordingWithRouteSettle(delayNs: UInt64, afterStart: (() -> Void)? = nil) {
        cancelPendingStart()
        _ = headsetRemote.switchToRecordingSession()
        pendingStartTask = Task {
            try? await Task.sleep(nanoseconds: delayNs)
            guard !Task.isCancelled else { return }
            attemptStartHeadsetRecordingWithRetry(retriesRemaining: 2, retryDelayNs: 700_000_000, afterStart: afterStart)
        }
    }

    private func attemptStartHeadsetRecordingWithRetry(
        retriesRemaining: Int,
        retryDelayNs: UInt64,
        afterStart: (() -> Void)?
    ) {
        let shouldShowError = retriesRemaining == 0
        if startHeadsetRecording(showError: shouldShowError) {
            afterStart?()
            return
        }
        guard retriesRemaining > 0 else {
            headsetRecordingActive = false
            micPulse = false
            return
        }
        print("[Headset] retrying raw recorder start after route settle")
        pendingStartTask = Task {
            _ = headsetRemote.switchToRecordingSession()
            try? await Task.sleep(nanoseconds: retryDelayNs)
            guard !Task.isCancelled else { return }
            attemptStartHeadsetRecordingWithRetry(
                retriesRemaining: retriesRemaining - 1,
                retryDelayNs: retryDelayNs,
                afterStart: afterStart
            )
        }
    }

    @discardableResult
    private func startHeadsetRecording(showError: Bool = true) -> Bool {
        print("[Headset] start raw recording")
        audioPlayer?.stop()
        audioPlayer = nil
        fallbackSynthesizer?.stopSpeaking(at: .immediate)
        isSpeaking = false
        do {
            try headsetAudioRecorder.startRecording()
            return true
        } catch {
            print("[Headset] start raw recording FAILED: \(error)")
            if showError {
                appState.errorMessage = "Mic error: \(error.localizedDescription)"
            }
            headsetRemote.switchToPlaybackSession()
            return false
        }
    }

    private func updateNowPlaying() {
        let hole = appState.currentHole.map { "Hole \($0)" } ?? "Round Active"
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: "KindCaddy",
            MPMediaItemPropertyArtist: "AI Caddy",
            MPMediaItemPropertyAlbumTitle: hole,
        ]
    }

    // MARK: - Ambient Mode (auto-dim while idle-timer is disabled)

    /// Called on any on-screen touch. Restores brightness if dimmed and resets the idle timer.
    /// Intentionally NOT called on AirPods squeeze — the user can interact via audio with the
    /// phone in their pocket and we should not flash the screen back to full brightness.
    private func resetAmbientTimer() {
        lastInteractionTime = Date()
        if isAmbientMode {
            exitAmbientMode()
        }
    }

    private func enterAmbientMode() {
        guard !isAmbientMode else { return }
        savedBrightness = UIScreen.main.brightness
        UIScreen.main.brightness = Self.ambientBrightness
        isAmbientMode = true
        print("[Ambient] entered (brightness \(savedBrightness) → \(Self.ambientBrightness))")
    }

    private func exitAmbientMode() {
        guard isAmbientMode else { return }
        UIScreen.main.brightness = savedBrightness
        isAmbientMode = false
        print("[Ambient] exited (brightness restored to \(savedBrightness))")
    }

    private func startAmbientMonitor() {
        ambientMonitorTask?.cancel()
        lastInteractionTime = Date()
        ambientMonitorTask = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard !Task.isCancelled else { return }
                let idleFor = Date().timeIntervalSince(lastInteractionTime)
                if !isAmbientMode, idleFor >= Self.ambientIdleSeconds {
                    enterAmbientMode()
                }
            }
        }
    }

    private func stopAmbientMonitor() {
        ambientMonitorTask?.cancel()
        ambientMonitorTask = nil
        if isAmbientMode {
            exitAmbientMode()
        }
    }

    /// On manual lock (or any backgrounding), restore the user's brightness so a locked phone
    /// is not stuck at our 5% setting. On return to foreground, restart the idle countdown.
    private func handleScenePhaseChange(_ newPhase: ScenePhase) {
        switch newPhase {
        case .background:
            ambientMonitorTask?.cancel()
            if isAmbientMode {
                exitAmbientMode()
            }
            postResumeNotification(reason: .roundBackgrounded)
        case .inactive:
            ambientMonitorTask?.cancel()
            if isAmbientMode {
                exitAmbientMode()
            }
        case .active:
            startAmbientMonitor()
            dismissResumeNotification()
        @unknown default:
            break
        }
    }

    // MARK: - Resume notification

    private static let resumeNotificationId = "kindcaddy.resumeRound"

    private enum ResumeNotificationReason {
        /// User just locked the phone or backgrounded the app — passive reminder.
        case roundBackgrounded
        /// User squeezed AirPods while we were backgrounded — actionable, with sound.
        case squeezeWhileLocked
    }

    private func postResumeNotification(reason: ResumeNotificationReason) {
        let content = UNMutableNotificationContent()
        switch reason {
        case .roundBackgrounded:
            content.title = "KindCaddy round in progress"
            content.body = "Tap to return — AirPods squeeze only works while KindCaddy is open."
            content.sound = nil
        case .squeezeWhileLocked:
            content.title = "Tap to resume your round"
            content.body = "AirPods squeeze can't start the mic while the phone is locked. Open KindCaddy to ask your question."
            content.sound = .default
        }

        let request = UNNotificationRequest(
            identifier: Self.resumeNotificationId,
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                print("[ResumeNotification] failed: \(error)")
            }
        }
    }

    private func dismissResumeNotification() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.resumeNotificationId])
        center.removeDeliveredNotifications(withIdentifiers: [Self.resumeNotificationId])
    }

    // MARK: - Speech

    @discardableResult
    private func startListening(showError: Bool = true) -> Bool {
        print("[StartListening] called")
        audioPlayer?.stop()
        audioPlayer = nil
        fallbackSynthesizer?.stopSpeaking(at: .immediate)
        isSpeaking = false
        do {
            try speechRecognizer.startRecording()
            print("[StartListening] recording started OK")
            return true
        } catch {
            print("[StartListening] FAILED: \(error)")
            if showError {
                appState.errorMessage = "Mic error: \(error.localizedDescription)"
            }
            headsetRemote.switchToPlaybackSession()
            return false
        }
    }

    private func stopListeningAndSend() {
        speechRecognizer.stopRecording()
        headsetRemote.switchToPlaybackSession()
        let text = speechRecognizer.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        Task {
            let previousResponse = appState.lastResponse
            await appState.handleVoiceInput(text)
            updateNowPlaying()
            if appState.lastResponse != previousResponse && appState.errorMessage == nil {
                dismissMicTip()
                speakResponse()
            }
        }
    }

    private func speakResponse() {
        let text = Self.stripMarkdown(appState.lastResponse)
        guard !text.isEmpty else { return }
        guard appState.hasAIDataConsent else {
            appState.requestAIDataConsent()
            return
        }

        // Stop any in-flight TTS before starting new playback. Without this,
        // a new advice response arriving while an old one is still playing
        // will overlap (the "two voices" beta-user complaint).
        audioPlayer?.stop()
        audioPlayer = nil
        fallbackSynthesizer?.stopSpeaking(at: .immediate)
        ttsGeneration += 1
        let generation = ttsGeneration
        isSpeaking = true

        Task {
            do {
                let mp3Data = try await APIClient.shared.synthesizeSpeech(
                    text: text,
                    voice: "nova"
                )
                guard generation == ttsGeneration else { return }
                let player = try AVAudioPlayer(data: mp3Data)
                audioPlayer = player
                player.play()
            } catch {
                guard generation == ttsGeneration else { return }
                print("[TTS] OpenAI TTS failed, falling back to Apple: \(error)")
                speakWithAppleTTS(text)
            }
            if generation == ttsGeneration {
                isSpeaking = false
            }
        }
    }

    private func speakWithAppleTTS(_ text: String) {
        let synth = AVSpeechSynthesizer()
        fallbackSynthesizer = synth
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = 0.52
        synth.speak(utterance)
    }

    private static func stripMarkdown(_ text: String) -> String {
        var result = text
        result = result.replacingOccurrences(of: "**", with: "")
        result = result.replacingOccurrences(of: "__", with: "")
        result = result.replacingOccurrences(of: "##", with: "")
        result = result.replacingOccurrences(of: "#", with: "")
        result = result.replacingOccurrences(of: "* ", with: "")
        result = result.replacingOccurrences(of: "- ", with: "")
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func ensureAIConsentBeforeVoice() -> Bool {
        guard appState.hasAIDataConsent else {
            appState.requestAIDataConsent()
            return false
        }
        return true
    }
}

// MARK: - Tooltip Bubble

/// Single-line, dismissable hint bubble used by the just-in-time tutorial flow.
/// Shows once per device and is gated by a UserDefaults flag set by the caller.
private struct TooltipBubble: View {
    let text: String
    var buttonText: String = "Got it"
    var stepIndicator: String? = nil
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "info.circle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(text)
                    .font(.system(size: 13, weight: .regular, design: .serif))
                    .foregroundStyle(Theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if let stepIndicator {
                    Text(stepIndicator)
                        .font(.system(size: 10, weight: .medium, design: .serif))
                        .tracking(0.5)
                        .foregroundStyle(Theme.textTertiary)
                }
            }
            Spacer(minLength: 8)
            Button(action: onDismiss) {
                Text(buttonText)
                    .font(.system(size: 12, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Theme.accent.opacity(0.12))
                    .clipShape(Capsule())
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(Theme.accent.opacity(0.35), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.4), radius: 12, y: 4)
    }
}

// MARK: - Live Scorecard Sheet

private struct LiveScorecardSheet: View {
    @ObservedObject var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var editingHole: Int? = nil

    private let allHoles = Array(1...18)

    var body: some View {
        NavigationStack {
            List(allHoles, id: \.self) { hole in
                holeRow(hole)
                    .contentShape(Rectangle())
                    .onTapGesture {
                        // Move the yellow indicator to this hole immediately
                        // (optimistic local update) and open the edit sheet.
                        // Server sync runs in the background.
                        appState.selectHole(hole)
                        editingHole = hole
                    }
            }
            .listStyle(.plain)
            .navigationTitle("Scorecard")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Theme.accent)
                }
            }
        }
        .sheet(item: Binding(
            get: { editingHole.map { HoleEditTarget(hole: $0) } },
            set: { editingHole = $0?.hole }
        )) { target in
            HoleEditSheet(
                hole: target.hole,
                initialStrokes: scoreFor(target.hole),
                initialPar: parFor(target.hole),
                initialYards: yardsFor(target.hole),
                appState: appState
            )
        }
    }

    @ViewBuilder
    private func holeRow(_ hole: Int) -> some View {
        let par = parFor(hole)
        let strokes = scoreFor(hole)
        let yards = yardsFor(hole)
        let isCurrentHole = appState.currentHole == hole

        HStack(spacing: 12) {
            // Hole number
            ZStack {
                Circle()
                    .fill(isCurrentHole ? Theme.accent : Theme.cardBackground)
                    .frame(width: 32, height: 32)
                Text("\(hole)")
                    .font(.system(size: 13, weight: .bold, design: .serif))
                    .foregroundStyle(isCurrentHole ? Theme.background : Theme.textPrimary)
            }

            // Par & yardage
            VStack(alignment: .leading, spacing: 2) {
                Text("Par \(par)")
                    .font(.system(size: 14, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.textPrimary)
                if let y = yards {
                    Text("\(y) yds")
                        .font(.system(size: 12, design: .serif))
                        .foregroundStyle(Theme.textSecondary)
                }
            }

            Spacer()

            // Score
            if let s = strokes {
                let diff = s - par
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(s)")
                        .font(.system(size: 18, weight: .bold, design: .serif))
                        .foregroundStyle(Theme.textPrimary)
                    Text(scoreLabel(diff))
                        .font(.system(size: 11, design: .serif))
                        .foregroundStyle(scoreLabelColor(diff))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(scoreLabelColor(diff).opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                Text("—")
                    .font(.system(size: 16, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(.vertical, 4)
    }

    private func parFor(_ hole: Int) -> Int {
        guard appState.livePars.count >= hole else { return 4 }
        return appState.livePars[hole - 1]
    }

    private func scoreFor(_ hole: Int) -> Int? {
        appState.liveScores.first(where: { $0.hole == hole })?.strokes
    }

    private func yardsFor(_ hole: Int) -> Int? {
        appState.liveYardages[hole]
    }

    private func scoreLabel(_ diff: Int) -> String {
        switch diff {
        case ..<(-1): return "Eagle"
        case -1: return "Birdie"
        case 0: return "Par"
        case 1: return "Bogey"
        case 2: return "Double"
        default: return "+\(diff)"
        }
    }

    private func scoreLabelColor(_ diff: Int) -> Color {
        if diff <= -1 { return Theme.success }
        if diff == 0 { return Theme.accent }
        if diff == 1 { return Theme.textSecondary }
        return Theme.error
    }
}

// MARK: - Hole Edit Sheet

private struct HoleEditTarget: Identifiable {
    let hole: Int
    var id: Int { hole }
}

private struct HoleEditSheet: View {
    let hole: Int
    let initialStrokes: Int?
    let initialPar: Int
    let initialYards: Int?
    @ObservedObject var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var strokesText: String
    @State private var selectedPar: Int
    @State private var yardsText: String
    @State private var isSaving = false
    @State private var validationMessage: String? = nil

    init(hole: Int, initialStrokes: Int?, initialPar: Int, initialYards: Int?, appState: AppState) {
        self.hole = hole
        self.initialStrokes = initialStrokes
        self.initialPar = initialPar
        self.initialYards = initialYards
        self.appState = appState
        _strokesText = State(initialValue: initialStrokes.map(String.init) ?? "")
        _selectedPar = State(initialValue: initialPar)
        _yardsText = State(initialValue: initialYards.map(String.init) ?? "")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Hole \(hole)") {
                    HStack {
                        Text("Par")
                        Spacer()
                        Picker("Par", selection: $selectedPar) {
                            Text("3").tag(3)
                            Text("4").tag(4)
                            Text("5").tag(5)
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 150)
                        .tint(Theme.accent)
                    }

                    HStack {
                        Text("Yardage")
                        Spacer()
                        TextField("e.g. 380", text: $yardsText)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 100)
                    }
                }

                Section("Score") {
                    HStack {
                        Text("Strokes")
                        Spacer()
                        TextField("—", text: $strokesText)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 60)
                    }
                    if let validationMessage {
                        Text(validationMessage)
                            .font(.caption)
                            .foregroundStyle(Theme.error)
                    }
                }
            }
            .navigationTitle("Edit Hole \(hole)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(Theme.textSecondary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        save()
                    } label: {
                        if isSaving {
                            ProgressView()
                                .tint(Theme.accent)
                        } else {
                            Text("Save")
                                .fontWeight(.semibold)
                        }
                    }
                    .foregroundStyle(Theme.accent)
                    .disabled(isSaving)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func save() {
        guard !isSaving else { return }

        let trimmedStrokes = strokesText.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedYards = yardsText.trimmingCharacters(in: .whitespacesAndNewlines)

        let newStrokes: Int?
        if trimmedStrokes.isEmpty {
            newStrokes = nil
        } else if let strokes = Int(trimmedStrokes), (1...15).contains(strokes) {
            newStrokes = strokes
        } else {
            validationMessage = "Enter strokes from 1 to 15."
            return
        }

        validationMessage = nil
        isSaving = true

        Task { @MainActor in
            // Update par and yardage if either changed.
            let newYards = Int(trimmedYards)
            let parChanged = selectedPar != initialPar
            let yardsChanged = newYards != initialYards
            var saved = true

            if parChanged || yardsChanged {
                saved = await appState.updateHoleStats(hole: hole, par: selectedPar, yards: newYards)
            }

            // Update strokes if provided and changed.
            if let strokes = newStrokes, strokes != initialStrokes {
                saved = await appState.editScore(hole: hole, strokes: strokes) && saved
            }

            isSaving = false
            if saved {
                dismiss()
            }
        }
    }
}

#Preview {
    RoundView()
        .environmentObject(AppState())
}
