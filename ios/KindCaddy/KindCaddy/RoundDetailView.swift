import SwiftUI

enum RoundDetailScrollTarget {
    case chat
}

private let chatHistoryAnchorId = "round-chat-history"

struct RoundDetailView: View {
    let roundId: String
    var scrollTo: RoundDetailScrollTarget? = nil
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var appState: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var detail: RoundDetail?
    @State private var cachedMessages: [RoundMessage] = []
    @State private var isLoading = false
    @State private var serverFetchFailed = false
    @State private var isGeneratingRecap = false
    @State private var isCompletingRound = false
    @State private var isResumingRound = false
    @State private var resumeUnavailableMessage: String?
    @State private var errorMessage: String?
    @State private var editingHole: Int? = nil
    @State private var editStrokesText: String = ""
    @State private var isSavingScore = false
    @State private var didApplyScrollTarget = false

    private let api = APIClient.shared

    /// Messages to render in the chat section. Prefer the live server payload;
    /// fall back to the on-device cache so the section is populated instantly
    /// (and stays usable without network).
    private var displayMessages: [RoundMessage] {
        if let serverMessages = detail?.messages, !serverMessages.isEmpty {
            return serverMessages
        }
        return cachedMessages
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            if isLoading && detail == nil && cachedMessages.isEmpty {
                VStack(spacing: 16) {
                    ProgressView()
                        .tint(Theme.accent)
                        .scaleEffect(1.2)
                    Text("Loading round...")
                        .font(Theme.captionSerif)
                        .foregroundStyle(Theme.textSecondary)
                }
            } else if let detail {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(spacing: 20) {
                            if serverFetchFailed {
                                offlineBanner
                            }
                            headerCard(detail)
                            if !detail.scores.isEmpty {
                                scorecardSection(detail)
                            }
                            if !detail.shots.isEmpty {
                                shotsSection(detail)
                            }
                            if !displayMessages.isEmpty {
                                chatHistorySection(displayMessages)
                                    .id(chatHistoryAnchorId)
                            }
                            if (detail.summary_text.map { !$0.isEmpty } ?? false) || detail.status == "completed" {
                                summarySection(detail)
                            }
                            if detail.status != "completed" {
                                resumeRoundButton(detail)
                                completeRoundButton(detail)
                            }
                        }
                        .padding()
                    }
                    .onAppear { applyScrollTargetIfNeeded(proxy: proxy) }
                    .onChange(of: displayMessages.count) { _ in
                        applyScrollTargetIfNeeded(proxy: proxy)
                    }
                }
            } else if !cachedMessages.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(spacing: 20) {
                            offlineBanner
                            chatHistorySection(cachedMessages)
                                .id(chatHistoryAnchorId)
                        }
                        .padding()
                    }
                    .onAppear { applyScrollTargetIfNeeded(proxy: proxy) }
                }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 40))
                        .foregroundStyle(Theme.textTertiary)
                    Text(serverFetchFailed ? "Couldn't Load Round" : "Round Not Found")
                        .font(Theme.sectionTitle)
                        .foregroundStyle(Theme.textPrimary)
                    Text(serverFetchFailed
                         ? "You appear to be offline and we don't have this round cached yet."
                         : "This round may have been deleted or is unavailable.")
                        .font(Theme.captionSerif)
                        .foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 40)
            }
        }
        .navigationTitle("Round Detail")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task {
            cachedMessages = ChatCache.shared.messages(forRound: roundId)
            await loadDetail()
        }
        .alert("Edit Hole \(editingHole ?? 0)", isPresented: Binding(
            get: { editingHole != nil },
            set: { if !$0 { editingHole = nil } }
        )) {
            TextField("Strokes", text: $editStrokesText)
                .keyboardType(.numberPad)
            Button("Save") {
                if let hole = editingHole, let strokes = Int(editStrokesText), (1...15).contains(strokes) {
                    Task { await saveEditedScore(hole: hole, strokes: strokes) }
                }
                editingHole = nil
            }
            Button("Cancel", role: .cancel) { editingHole = nil }
        } message: {
            if let hole = editingHole, let entry = detail?.scores.first(where: { $0.hole == hole }) {
                Text("Par \(entry.par) — current score: \(entry.strokes)")
            }
        }
        .alert("Error", isPresented: .init(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: - Header

    private func headerCard(_ d: RoundDetail) -> some View {
        VStack(spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(formattedDate(d.started_at))
                        .font(Theme.headline)
                        .foregroundStyle(Theme.textPrimary)
                    if let course = d.course_name, !course.isEmpty {
                        Label(course, systemImage: "mappin")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 6) {
                    statusBadge(d.status)
                    if let weather = d.weather_summary, !weather.isEmpty {
                        VStack(alignment: .trailing, spacing: 3) {
                            Label(weather, systemImage: "cloud.sun.fill")
                                .font(.caption)
                                .foregroundStyle(Theme.textSecondary)
                                .lineLimit(1)
                            HistoricalWeatherAttributionView()
                        }
                    }
                }
            }

            Divider().overlay(Theme.border)

            HStack(spacing: 0) {
                statPill(label: "Score", value: d.holes_played > 0 ? "\(d.total_strokes)" : "--")
                if let vs = d.score_vs_par {
                    statPill(label: "vs Par", value: scoreLabelText(vs), color: scoreColor(vs))
                }
                statPill(label: "Holes", value: "\(d.holes_played)")
                if let target = d.target_score {
                    statPill(label: "Target", value: "\(target)")
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func statPill(label: String, value: String, color: Color = Theme.textPrimary) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(size: 22, weight: .bold, design: .serif).monospacedDigit())
                .foregroundStyle(color)
            Text(label.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Scorecard

    private func scorecardSection(_ d: RoundDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Scorecard")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            let front = d.scores.filter { $0.hole <= 9 }.sorted { $0.hole < $1.hole }
            let back = d.scores.filter { $0.hole > 9 }.sorted { $0.hole < $1.hole }

            if !front.isEmpty {
                nineHoleGrid(title: "Front 9", scores: front)
            }
            if !back.isEmpty {
                nineHoleGrid(title: "Back 9", scores: back)
            }

            HStack(spacing: 4) {
                Image(systemName: "hand.tap")
                    .font(.system(size: 10))
                Text("Tap a score to edit")
                    .font(.system(size: 11))
            }
            .foregroundStyle(Theme.textTertiary)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .padding(.top, 2)
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func nineHoleGrid(title: String, scores: [RoundScoreEntry]) -> some View {
        let hasYardage = scores.contains { $0.yardage != nil }

        return VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(Theme.textSecondary)

            // Hole numbers
            HStack(spacing: 0) {
                Text("Hole")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.textTertiary)
                    .frame(width: 40, alignment: .leading)
                ForEach(scores) { s in
                    Text("\(s.hole)")
                        .font(.system(size: 12, weight: .medium).monospacedDigit())
                        .foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity)
                }
                Text("Tot")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 34)
            }

            // Yardage row (only if data is available)
            if hasYardage {
                HStack(spacing: 0) {
                    Text("Yds")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.textTertiary)
                        .frame(width: 40, alignment: .leading)
                    ForEach(scores) { s in
                        Text(s.yardage.map { "\($0)" } ?? "-")
                            .font(.system(size: 12).monospacedDigit())
                            .foregroundStyle(Theme.textTertiary)
                            .frame(maxWidth: .infinity)
                    }
                    Text("\(scores.compactMap(\.yardage).reduce(0, +))")
                        .font(.system(size: 12, weight: .semibold).monospacedDigit())
                        .foregroundStyle(Theme.textTertiary)
                        .frame(width: 34)
                }
            }

            // Par row
            HStack(spacing: 0) {
                Text("Par")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.textTertiary)
                    .frame(width: 40, alignment: .leading)
                ForEach(scores) { s in
                    Text("\(s.par)")
                        .font(.system(size: 13).monospacedDigit())
                        .foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity)
                }
                Text("\(scores.reduce(0) { $0 + $1.par })")
                    .font(.system(size: 13, weight: .semibold).monospacedDigit())
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 34)
            }

            // Score row (tappable for editing)
            HStack(spacing: 0) {
                Text("Score")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.textTertiary)
                    .frame(width: 40, alignment: .leading)
                ForEach(scores) { s in
                    let diff = s.strokes - s.par
                    Text("\(s.strokes)")
                        .font(.system(size: 14, weight: .semibold).monospacedDigit())
                        .foregroundStyle(scoreColor(diff))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 3)
                        .background(
                            diff <= -1 ? scoreColor(diff).opacity(0.14) :
                            diff >= 2 ? scoreColor(diff).opacity(0.14) : Color.clear
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .contentShape(Rectangle())
                        .onTapGesture {
                            editStrokesText = "\(s.strokes)"
                            editingHole = s.hole
                        }
                }
                Text("\(scores.reduce(0) { $0 + $1.strokes })")
                    .font(.system(size: 14, weight: .bold).monospacedDigit())
                    .foregroundStyle(Theme.textPrimary)
                    .frame(width: 34)
            }
        }
    }

    // MARK: - Shots

    private func shotsSection(_ d: RoundDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Shot Log")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            let grouped = Dictionary(grouping: d.shots, by: \.hole)
            let sortedHoles = grouped.keys.sorted()

            ForEach(sortedHoles, id: \.self) { hole in
                VStack(alignment: .leading, spacing: 4) {
                    Text("Hole \(hole)")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.accent)

                    ForEach(grouped[hole]!) { shot in
                        HStack {
                            Text(shot.club)
                                .font(.body.weight(.medium))
                                .foregroundStyle(Theme.textPrimary)
                                .frame(width: 50, alignment: .leading)
                            if let dist = shot.actual_distance {
                                Text("\(Int(dist))yd")
                                    .font(.subheadline.monospacedDigit())
                                    .foregroundStyle(Theme.textSecondary)
                            }
                            if let miss = shot.miss_direction, !miss.isEmpty {
                                Text(miss)
                                    .font(.caption)
                                    .foregroundStyle(Theme.error.opacity(0.8))
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Theme.error.opacity(0.1))
                                    .clipShape(Capsule())
                            }
                            Spacer()
                        }
                    }
                }
                .padding(.bottom, 4)
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Chat History

    private func chatHistorySection(_ messages: [RoundMessage]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "bubble.left.and.bubble.right.fill")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.accent)
                Text("Conversation")
                    .font(Theme.sectionTitle)
                    .foregroundStyle(Theme.textPrimary)
                Spacer()
                Text("\(messages.count) message\(messages.count == 1 ? "" : "s")")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.textTertiary)
            }

            VStack(spacing: 10) {
                ForEach(messages) { msg in
                    chatBubble(msg)
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func chatBubble(_ msg: RoundMessage) -> some View {
        let isUser = msg.isUser
        HStack(alignment: .top, spacing: 10) {
            if isUser { Spacer(minLength: 32) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                HStack(spacing: 6) {
                    if !isUser {
                        Image(systemName: "figure.golf")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                    Text(isUser ? "You" : "Caddy")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.textTertiary)
                    if let hole = msg.hole {
                        Text("· Hole \(hole)")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textTertiary)
                    }
                }

                Text(msg.content)
                    .font(Theme.bodySerif)
                    .foregroundStyle(Theme.textPrimary)
                    .lineSpacing(4)
                    .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
                    .multilineTextAlignment(isUser ? .trailing : .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(isUser ? Theme.accent.opacity(0.12) : Theme.background)
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(Theme.border.opacity(0.6), lineWidth: 1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            if !isUser { Spacer(minLength: 32) }
        }
    }

    // MARK: - Summary

    private func summarySection(_ d: RoundDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "sparkles")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.accent)
                Text("Caddy Recap")
                    .font(Theme.sectionTitle)
                    .foregroundStyle(Theme.textPrimary)
            }

            if let summary = d.summary_text, !summary.isEmpty {
                Text(summary)
                    .font(Theme.bodySerif)
                    .foregroundStyle(Theme.textPrimary)
                    .lineSpacing(6)
            } else if isGeneratingRecap {
                HStack(spacing: 12) {
                    ProgressView().tint(Theme.accent)
                    Text("Generating your recap...")
                        .font(Theme.captionSerif)
                        .foregroundStyle(Theme.textSecondary)
                }
                .padding(.vertical, 4)
            } else {
                Button {
                    Task { await generateRecap() }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 14, weight: .medium))
                        Text("Generate Recap")
                            .font(.system(size: 15, weight: .semibold))
                    }
                    .foregroundStyle(Theme.background)
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
                    .background(Theme.accent)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func statusBadge(_ status: String) -> some View {
        let isComplete = status == "completed"
        HStack(spacing: 4) {
            Circle()
                .fill(isComplete ? Theme.success : Theme.accent)
                .frame(width: 6, height: 6)
            Text(isComplete ? "Completed" : "In Progress")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(isComplete ? Theme.success : Theme.accent)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background((isComplete ? Theme.success : Theme.accent).opacity(0.12))
        .clipShape(Capsule())
    }

    @ViewBuilder
    private func resumeRoundButton(_ d: RoundDetail) -> some View {
        VStack(spacing: 8) {
            Button {
                Task { await resumeRound() }
            } label: {
                HStack(spacing: 8) {
                    if isResumingRound {
                        ProgressView().tint(Theme.background).scaleEffect(0.85)
                    } else {
                        Image(systemName: "play.fill")
                            .font(.system(size: 15, weight: .semibold))
                    }
                    Text(isResumingRound ? "Resuming…" : "Resume Round")
                        .font(.system(size: 16, weight: .semibold, design: .serif))
                }
                .foregroundStyle(Theme.background)
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                .background(isResumingRound ? Theme.accent.opacity(0.6) : Theme.accent)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(isResumingRound || isCompletingRound)

            if let message = resumeUnavailableMessage {
                Text(message)
                    .font(.system(size: 12, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
            }
        }
    }

    private func completeRoundButton(_ d: RoundDetail) -> some View {
        Button {
            Task { await completeRound() }
        } label: {
            HStack(spacing: 8) {
                if isCompletingRound {
                    ProgressView().tint(Theme.background).scaleEffect(0.85)
                } else {
                    Image(systemName: "flag.checkered")
                        .font(.system(size: 15, weight: .semibold))
                }
                Text(isCompletingRound ? "Finishing Round…" : "Complete Round")
                    .font(.system(size: 16, weight: .semibold, design: .serif))
            }
            .foregroundStyle(Theme.background)
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(isCompletingRound ? Theme.accent.opacity(0.6) : Theme.accent)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(isCompletingRound)
    }

    private func resumeRound() async {
        isResumingRound = true
        resumeUnavailableMessage = nil
        let result = await appState.resumeRound(roundId: roundId)
        isResumingRound = false
        switch result {
        case .live, .recovered:
            // Setting sessionId on AppState causes the root router (KindCaddyApp)
            // to switch to RoundView automatically; dismissing here just unwinds
            // the navigation stack/sheet underneath cleanly.
            dismiss()
        case .expired:
            resumeUnavailableMessage =
                "This round is too old to resume live. You can still review and edit scores here."
        case .noRound:
            resumeUnavailableMessage = "Could not find this round on the server."
        }
    }

    private func completeRound() async {
        guard appState.hasAIDataConsent else {
            appState.requestAIDataConsent()
            return
        }
        isCompletingRound = true
        do {
            _ = try await api.finishRound(roundId: roundId, status: "completed")
            // Generate recap if not already present
            if detail?.summary_text == nil || detail?.summary_text?.isEmpty == true {
                isGeneratingRecap = true
                try? await api.generateRecap(roundId: roundId)
                isGeneratingRecap = false
            }
            await loadDetail()
        } catch {
            errorMessage = error.localizedDescription
        }
        isCompletingRound = false
    }

    private func generateRecap() async {
        guard appState.hasAIDataConsent else {
            appState.requestAIDataConsent()
            return
        }
        isGeneratingRecap = true
        do {
            _ = try await api.generateRecap(roundId: roundId)
            await loadDetail()
        } catch {
            errorMessage = error.localizedDescription
        }
        isGeneratingRecap = false
    }

    // MARK: - Helpers

    private func loadDetail() async {
        isLoading = true
        do {
            let fetched = try await api.getRoundDetail(roundId: roundId)
            detail = fetched
            serverFetchFailed = false
            ChatCache.shared.upsertRoundDetail(fetched)
            cachedMessages = ChatCache.shared.messages(forRound: roundId)
        } catch {
            serverFetchFailed = true
            cachedMessages = ChatCache.shared.messages(forRound: roundId)
            // Only surface a popup error if we have nothing to show. With a
            // populated cache, the offline banner is enough — silent fail is
            // a better UX than a redundant alert.
            if cachedMessages.isEmpty {
                errorMessage = error.localizedDescription
            }
        }
        isLoading = false
    }

    private var offlineBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Theme.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Offline view")
                    .font(.system(size: 13, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.textPrimary)
                Text("Showing the last cached chat. Reconnect to see live updates.")
                    .font(.system(size: 12, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(Theme.accent.opacity(0.10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Theme.accent.opacity(0.25), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func applyScrollTargetIfNeeded(proxy: ScrollViewProxy) {
        guard !didApplyScrollTarget, let target = scrollTo else { return }
        guard !displayMessages.isEmpty else { return }
        switch target {
        case .chat:
            withAnimation(.easeInOut(duration: 0.25)) {
                proxy.scrollTo(chatHistoryAnchorId, anchor: .top)
            }
        }
        didApplyScrollTarget = true
    }

    private func saveEditedScore(hole: Int, strokes: Int) async {
        isSavingScore = true
        do {
            _ = try await api.editRoundScore(roundId: roundId, hole: hole, strokes: strokes)
            await loadDetail()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSavingScore = false
    }

    private func formattedDate(_ iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: iso) {
            let display = DateFormatter()
            display.dateStyle = .long
            display.timeStyle = .short
            return display.string(from: date)
        }
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: iso) {
            let display = DateFormatter()
            display.dateStyle = .long
            display.timeStyle = .short
            return display.string(from: date)
        }
        return iso
    }

    private func scoreLabelText(_ vs: Int) -> String {
        if vs == 0 { return "E" }
        return vs > 0 ? "+\(vs)" : "\(vs)"
    }

    private func scoreColor(_ vs: Int) -> Color {
        if vs < 0 { return Theme.success }
        if vs == 0 { return Theme.accent }
        if vs == 1 { return Theme.error.opacity(0.7) }
        return Theme.error
    }
}

#Preview {
    NavigationStack {
        RoundDetailView(roundId: "preview")
            .environmentObject(AuthManager())
            .environmentObject(AppState())
    }
    .preferredColorScheme(.dark)
}
