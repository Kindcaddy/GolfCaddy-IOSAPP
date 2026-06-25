import Foundation
// llama.cpp symbols are exposed via llama-bridge.h (bridging header)

/// Manages on-device Qwen 2.5 4B Instruct inference via llama.cpp.
/// The model is downloaded once to the Documents directory (~2.5 GB).
@MainActor
final class LocalLLMService: ObservableObject {
    static let shared = LocalLLMService()

    // MARK: - Published state

    @Published private(set) var isModelReady: Bool = false
    @Published private(set) var isDownloading: Bool = false
    @Published private(set) var downloadProgress: Double = 0       // 0.0 – 1.0
    @Published private(set) var downloadedBytes: Int64 = 0
    @Published private(set) var totalBytes: Int64 = 0
    @Published private(set) var loadError: String? = nil

    // MARK: - Private state

    private var modelPtr: OpaquePointer?     // llama_model *
    private var downloadTask: Task<Void, Never>?

    // MARK: - Constants

    private static let modelFilename = "qwen2.5-4b-instruct-q4_k_m.gguf"
    private static let modelDownloadURL = URL(
        string: "https://huggingface.co/Qwen/Qwen2.5-4B-Instruct-GGUF/resolve/main/qwen2.5-4b-instruct-q4_k_m.gguf"
    )!

    // MARK: - Computed

    var modelFileURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(Self.modelFilename)
    }

    var isModelDownloaded: Bool {
        FileManager.default.fileExists(atPath: modelFileURL.path)
    }

    var downloadedMB: String {
        String(format: "%.0f MB", Double(downloadedBytes) / 1_048_576)
    }

    var totalMB: String {
        totalBytes > 0 ? String(format: "%.0f MB", Double(totalBytes) / 1_048_576) : "~2500 MB"
    }

    // MARK: - Init

    private init() {
        llama_backend_init()
        if isModelDownloaded {
            loadModel()
        }
    }

    // MARK: - Download

    func startDownload() {
        guard !isDownloading && !isModelReady else { return }
        downloadTask = Task { await runDownload() }
    }

    func cancelDownload() {
        downloadTask?.cancel()
        downloadTask = nil
        isDownloading = false
        downloadProgress = 0
    }

    private func runDownload() async {
        isDownloading = true
        loadError = nil

        do {
            var request = URLRequest(url: Self.modelDownloadURL)
            // Resume partial download if file exists but is incomplete
            if let existingSize = try? modelFileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                request.setValue("bytes=\(existingSize)-", forHTTPHeaderField: "Range")
                downloadedBytes = Int64(existingSize)
            }

            let (asyncBytes, response) = try await URLSession.shared.bytes(for: request)

            if let httpResponse = response as? HTTPURLResponse {
                let contentLength = Int64(httpResponse.value(forHTTPHeaderField: "Content-Length") ?? "0") ?? 0
                totalBytes = downloadedBytes + contentLength
            }

            // Stream bytes to disk
            let fileHandle: FileHandle
            if FileManager.default.fileExists(atPath: modelFileURL.path) {
                fileHandle = try FileHandle(forWritingTo: modelFileURL)
                fileHandle.seekToEndOfFile()
            } else {
                FileManager.default.createFile(atPath: modelFileURL.path, contents: nil)
                fileHandle = try FileHandle(forWritingTo: modelFileURL)
            }
            defer { try? fileHandle.close() }

            var chunk = Data()
            chunk.reserveCapacity(65_536)

            for try await byte in asyncBytes {
                chunk.append(byte)
                if chunk.count >= 65_536 {
                    fileHandle.write(chunk)
                    downloadedBytes += Int64(chunk.count)
                    if totalBytes > 0 {
                        downloadProgress = Double(downloadedBytes) / Double(totalBytes)
                    }
                    chunk.removeAll(keepingCapacity: true)
                }
            }
            if !chunk.isEmpty {
                fileHandle.write(chunk)
                downloadedBytes += Int64(chunk.count)
            }

            downloadProgress = 1.0
            isDownloading = false
            loadModel()

        } catch is CancellationError {
            isDownloading = false
        } catch {
            isDownloading = false
            loadError = "Download failed: \(error.localizedDescription)"
        }
    }

    // MARK: - Load

    private func loadModel() {
        guard isModelDownloaded else { return }
        guard modelPtr == nil else { return }

        var params = llama_model_default_params()
        params.n_gpu_layers = 99   // Use Metal GPU on iPhone

        let ptr = llama_load_model_from_file(modelFileURL.path, params)
        if let ptr {
            modelPtr = ptr
            isModelReady = true
        } else {
            loadError = "Failed to load model. The file may be corrupted — try re-downloading."
            try? FileManager.default.removeItem(at: modelFileURL)
        }
    }

    func deleteModel() {
        if let ptr = modelPtr {
            llama_free_model(ptr)
            modelPtr = nil
        }
        isModelReady = false
        downloadProgress = 0
        downloadedBytes = 0
        try? FileManager.default.removeItem(at: modelFileURL)
    }

    // MARK: - Inference

    /// Generate a response from the on-device model.
    /// Runs on a background thread; safe to call from async context.
    func generate(systemPrompt: String, userMessage: String) async throws -> String {
        guard let model = modelPtr else {
            throw LLMError.modelNotLoaded
        }

        return try await Task.detached(priority: .userInitiated) {
            try Self.runInference(model: model, system: systemPrompt, user: userMessage)
        }.value
    }

    // MARK: - Core inference (runs off main thread)

    private static func runInference(
        model: OpaquePointer,
        system: String,
        user: String
    ) throws -> String {

        // Build Qwen 2.5 ChatML prompt
        let prompt = "<|im_start|>system\n\(system)<|im_end|>\n<|im_start|>user\n\(user)<|im_end|>\n<|im_start|>assistant\n"

        // Context parameters
        var ctxParams = llama_context_default_params()
        ctxParams.n_ctx = 2048
        ctxParams.n_batch = 512
        ctxParams.n_threads = max(1, Int32(ProcessInfo.processInfo.processorCount / 2))
        ctxParams.n_threads_batch = ctxParams.n_threads

        guard let ctx = llama_new_context_with_model(model, ctxParams) else {
            throw LLMError.contextCreationFailed
        }
        defer { llama_free(ctx) }

        // Tokenize prompt
        let maxPromptTokens = 1024
        var tokens = [llama_token](repeating: 0, count: maxPromptTokens)
        let promptCStr = prompt.cString(using: .utf8)!
        let nPromptTokens = llama_tokenize(
            model, promptCStr, Int32(promptCStr.count - 1),
            &tokens, Int32(maxPromptTokens),
            true, true
        )
        guard nPromptTokens > 0 else { throw LLMError.tokenizationFailed }

        // Encode prompt
        let promptTokenSlice = Array(tokens.prefix(Int(nPromptTokens)))
        var promptTokensMut = promptTokenSlice
        var batch = llama_batch_get_one(&promptTokensMut, nPromptTokens)
        guard llama_decode(ctx, batch) == 0 else { throw LLMError.decodeFailed }

        // Set up sampler
        let sparams = llama_sampler_chain_default_params()
        guard let sampler = llama_sampler_chain_init(sparams) else {
            throw LLMError.samplerInitFailed
        }
        defer { llama_sampler_free(sampler) }
        llama_sampler_chain_add(sampler, llama_sampler_init_top_p(0.9, 1))
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(0.7))
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(UInt32.random(in: 0...UInt32.max)))

        // Generate tokens
        var output = ""
        let maxNewTokens = 512

        for _ in 0..<maxNewTokens {
            let newToken = llama_sampler_sample(sampler, ctx, -1)

            if llama_token_is_eog(model, newToken) { break }

            // Convert token to text
            var buf = [CChar](repeating: 0, count: 256)
            let nChars = llama_token_to_piece(model, newToken, &buf, 256, 0, false)
            if nChars > 0 {
                let bytes = buf.prefix(Int(nChars)).map { UInt8(bitPattern: $0) }
                if let piece = String(bytes: bytes, encoding: .utf8) {
                    output += piece
                    // Stop at Qwen end-of-turn token
                    if output.hasSuffix("<|im_end|>") {
                        output = String(output.dropLast("<|im_end|>".count))
                        break
                    }
                }
            }

            llama_sampler_accept(sampler, newToken)

            // Decode next token
            var nextToken = newToken
            batch = llama_batch_get_one(&nextToken, 1)
            guard llama_decode(ctx, batch) == 0 else { break }
        }

        return output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Errors

    enum LLMError: LocalizedError {
        case modelNotLoaded
        case contextCreationFailed
        case tokenizationFailed
        case decodeFailed
        case samplerInitFailed

        var errorDescription: String? {
            switch self {
            case .modelNotLoaded:        return "Qwen model is not loaded. Please download it first."
            case .contextCreationFailed: return "Failed to create inference context."
            case .tokenizationFailed:    return "Failed to tokenize the prompt."
            case .decodeFailed:          return "Inference failed."
            case .samplerInitFailed:     return "Failed to initialize sampler."
            }
        }
    }
}
