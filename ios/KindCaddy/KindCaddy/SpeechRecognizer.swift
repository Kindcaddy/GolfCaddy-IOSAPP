import AVFoundation
import Speech

@MainActor
class SpeechRecognizer: ObservableObject {
    @Published var transcript: String = ""
    @Published var isRecording: Bool = false
    @Published var isAvailable: Bool = false

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var audioEngine: AVAudioEngine?

    /// Vocabulary biasing for SFSpeechRecognizer. These strings push the recognizer
    /// toward golf-specific phrasing so short utterances like "135 to the pin" or
    /// "smooth 7-iron" transcribe accurately instead of degrading into generic guesses.
    private static let golfContextualStrings: [String] = [
        "yards", "yard", "feet", "pin", "flag", "green", "fairway", "rough",
        "bunker", "fringe", "tee", "fade", "draw", "hook", "slice", "cut",
        "headwind", "tailwind", "crosswind", "into the wind", "downwind", "uphill", "downhill",
        "driver", "3-wood", "5-wood", "7-wood", "hybrid",
        "3-iron", "4-iron", "5-iron", "6-iron", "7-iron", "8-iron", "9-iron",
        "pitching wedge", "gap wedge", "sand wedge", "lob wedge",
        "PW", "AW", "GW", "SW", "LW",
        "par 3", "par 4", "par 5", "birdie", "bogey", "eagle",
        "100 yards", "120 yards", "135 yards", "150 yards", "165 yards", "175 yards",
        "180 yards", "200 yards", "220 yards", "250 yards", "300 yards",
    ]

    init() {
        isAvailable = speechRecognizer?.isAvailable ?? false
    }

    func requestAuthorization() {
        SFSpeechRecognizer.requestAuthorization { [weak self] status in
            Task { @MainActor in
                self?.isAvailable = (status == .authorized)
            }
        }
    }

    func teardownAudioSession() {
        stopRecording()
    }

    /// Caller must have already switched the session to `.playAndRecord` and waited for
    /// the route to settle before calling this.
    func startRecording() throws {
        guard !isRecording else { return }

        if let engine = audioEngine {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        transcript = ""

        let engine = AVAudioEngine()
        audioEngine = engine

        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        print("[SpeechRec] input format: \(format.sampleRate) Hz, \(format.channelCount) ch")

        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw SpeechError.requestUnavailable
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Apple's cloud recognizer is much more accurate on mid-sentence numbers
        // ("135 yards"), club names, and golf vocab than the on-device model. The
        // app already requires network for the OpenAI calls, so this is no extra
        // privacy regression.
        request.requiresOnDeviceRecognition = false
        request.taskHint = .dictation
        request.contextualStrings = Self.golfContextualStrings
        recognitionRequest = request

        guard let speechRecognizer, speechRecognizer.isAvailable else {
            throw SpeechError.recognizerUnavailable
        }

        recognitionTask = speechRecognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                if let result {
                    self?.transcript = result.bestTranscription.formattedString
                }
                if error != nil || (result?.isFinal ?? false) {
                    self?.stopRecording()
                }
            }
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        engine.prepare()
        try engine.start()
        isRecording = true
        print("[SpeechRec] recording started")
    }

    func stopRecording() {
        audioEngine?.stop()
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine = nil
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask = nil
        isRecording = false
    }
}

enum SpeechError: LocalizedError {
    case requestUnavailable
    case recognizerUnavailable

    var errorDescription: String? {
        switch self {
        case .requestUnavailable: return "Speech recognition request unavailable"
        case .recognizerUnavailable: return "Speech recognizer unavailable"
        }
    }
}
