import SwiftUI

struct HomeView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @Environment(\.openURL) private var openURL
    @Binding var deepLinkedRoundId: String?
    @State private var showingProfile = false
    @State private var showingStartConflict = false
    @State private var showingExpiredAlert = false
    @State private var showingPaywall = false
    @State private var isResuming = false

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                VStack(spacing: 8) {
                    Image("KCLogo")
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(height: 72)
                    Text("Your AI Caddy")
                        .font(Theme.captionSerif)
                        .tracking(1.5)
                        .foregroundStyle(Theme.textSecondary)
                }

                Spacer()

                VStack(spacing: 14) {
                    if let active = appState.activeRound {
                        continueRoundCard(active)
                    }

                    Button {
                        handleStartNewTapped()
                    } label: {
                        HStack {
                            Spacer()
                            if appState.isLoading {
                                VStack(spacing: 5) {
                                    ProgressView().tint(Theme.background)
                                    if !appState.loadingMessage.isEmpty {
                                        Text(appState.loadingMessage)
                                            .font(.caption2)
                                            .foregroundStyle(Theme.background.opacity(0.8))
                                    }
                                }
                            } else {
                                Label("Start New Round", systemImage: "figure.golf")
                                    .font(Theme.headline)
                                    .foregroundStyle(Theme.background)
                            }
                            Spacer()
                        }
                        .frame(height: Theme.minTouchHeight)
                        .background(Theme.accent)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                    .disabled(appState.isLoading || isResuming)

                    Button {
                        Task { await openProfileAndStats() }
                    } label: {
                        Label("Profile & Stats", systemImage: "person.crop.circle")
                            .font(Theme.headline)
                            .foregroundStyle(Theme.accent)
                            .frame(maxWidth: .infinity)
                            .frame(height: Theme.minTouchHeight)
                            .background(Theme.cardBackground)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .overlay(
                                RoundedRectangle(cornerRadius: 14)
                                    .strokeBorder(Theme.border, lineWidth: 1)
                            )
                    }
                    .disabled(appState.isLoading || isResuming)

                    Button {
                        sendFeedback()
                    } label: {
                        Label("Report Issue / Send Feedback", systemImage: "paperplane")
                            .font(Theme.captionSerif)
                            .foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity)
                            .frame(height: 44)
                    }
                }
                .padding(.horizontal, Theme.spacingLG)

                if let error = appState.errorMessage {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(Theme.error)
                                .font(.system(size: 13))
                            Text(error)
                                .foregroundStyle(Theme.error)
                                .font(.system(size: 13))
                        }
                        HStack(spacing: 10) {
                            Button("Retry") {
                                Task { await appState.retryLastOperation() }
                            }
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                            Button("Dismiss") {
                                appState.errorMessage = nil
                            }
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.textSecondary)
                        }
                    }
                    .padding(12)
                    .background(Theme.error.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .padding(.horizontal)
                    .padding(.top, 12)
                }

                if let recoveryMessage = appState.recoveryMessage {
                    Text(recoveryMessage)
                        .font(.system(size: 12, weight: .regular, design: .serif))
                        .foregroundStyle(Theme.success)
                        .padding(10)
                        .background(Theme.success.opacity(0.10))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .padding(.horizontal)
                        .padding(.top, 8)
                }

                Spacer()
                    .frame(height: 60)
            }
        }
        .task { await appState.refreshActiveRound() }
        .fullScreenCover(isPresented: $showingProfile) {
            FullProfileView()
                .environmentObject(appState)
                .environmentObject(authManager)
                .environmentObject(subscriptionManager)
        }
        .fullScreenCover(isPresented: Binding(
            get: { showingPaywall || appState.paywallRequired },
            set: { isPresented in
                showingPaywall = isPresented
                appState.paywallRequired = isPresented
            }
        )) {
            PaywallView()
                .environmentObject(subscriptionManager)
        }
        .sheet(item: Binding(
            get: { deepLinkedRoundId.map { RoundDeepLink(id: $0) } },
            set: { deepLinkedRoundId = $0?.id }
        )) { link in
            NavigationStack {
                RoundDetailView(roundId: link.id)
                    .environmentObject(authManager)
                    .environmentObject(appState)
            }
        }
        .confirmationDialog(
            "You have a round in progress",
            isPresented: $showingStartConflict,
            titleVisibility: .visible
        ) {
            Button("Resume Round") {
                Task { await handleContinueTapped() }
            }
            Button("Finish & Start New", role: .destructive) {
                Task { await finishAndStartNewIfAllowed() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let active = appState.activeRound {
                Text(conflictMessage(for: active))
            }
        }
        .alert("Round Session Expired", isPresented: $showingExpiredAlert) {
            Button("View in History") {
                if let rid = appState.activeRound?.id {
                    deepLinkedRoundId = rid
                }
            }
            Button("OK", role: .cancel) {}
        } message: {
            Text(
                "Your live caddy session timed out and automatic recovery did not complete. "
                + "You can review this round in History and retry resume from Home."
            )
        }
    }

    // MARK: - Continue Round card

    @ViewBuilder
    private func continueRoundCard(_ active: RoundSummary) -> some View {
        Button {
            Task { await handleContinueTapped() }
        } label: {
            HStack(spacing: 14) {
                ZStack {
                    Circle()
                        .fill(Theme.accentSubtle)
                        .frame(width: 44, height: 44)
                    Image(systemName: "figure.golf")
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(Theme.accent)
                }

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(Theme.accent)
                            .frame(width: 6, height: 6)
                        Text("CONTINUE ROUND")
                            .font(.system(size: 10, weight: .semibold))
                            .tracking(1.2)
                            .foregroundStyle(Theme.accent)
                    }
                    Text(continueTitle(for: active))
                        .font(Theme.headline)
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                    Text(continueSubtitle(for: active))
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                if isResuming {
                    ProgressView().tint(Theme.accent)
                } else {
                    Image(systemName: "arrow.right.circle.fill")
                        .font(.system(size: 24))
                        .foregroundStyle(Theme.accent)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity)
            .background(Theme.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(Theme.accent.opacity(0.4), lineWidth: 1)
            )
        }
        .disabled(isResuming || appState.isLoading)
    }

    // MARK: - Actions

    private func handleStartNewTapped() {
        if appState.activeRound != nil {
            showingStartConflict = true
        } else {
            Task { await startNewRoundIfAllowed() }
        }
    }

    private func startNewRoundIfAllowed() async {
        if await subscriptionManager.ensureCanStartRound() {
            await appState.startSession()
            await subscriptionManager.refreshStatus()
        } else {
            appState.errorMessage = nil
            showingPaywall = true
        }
    }

    private func finishAndStartNewIfAllowed() async {
        if await subscriptionManager.ensureCanStartRound() {
            await appState.finishActiveRoundAndStartNew()
            await subscriptionManager.refreshStatus()
        } else {
            appState.errorMessage = nil
            showingPaywall = true
        }
    }

    private func openProfileAndStats() async {
        showingProfile = true
    }

    private func handleContinueTapped() async {
        guard !isResuming else { return }
        isResuming = true
        defer { isResuming = false }

        let result = await appState.continueRound()
        switch result {
        case .live:
            // Router (KindCaddyApp) flips to RoundView automatically.
            break
        case .recovered:
            break
        case .expired:
            showingExpiredAlert = true
        case .noRound:
            await appState.refreshActiveRound()
        }
    }

    private func sendFeedback() {
        let url = SupportLinks.feedbackURL(
            userId: authManager.currentUser?.id,
            sessionId: appState.sessionId,
            roundId: appState.roundId
        )
        if let url {
            openURL(url)
            Task {
                await APIClient.shared.trackEvent(
                    name: "feedback_tapped",
                    sessionId: appState.sessionId,
                    roundId: appState.roundId
                )
            }
        } else {
            appState.errorMessage = "Unable to open email app for feedback right now."
        }
    }

    // MARK: - Formatting helpers

    private func continueTitle(for r: RoundSummary) -> String {
        if let course = r.course_name, !course.isEmpty { return course }
        if r.holes_played > 0 { return "Round in progress" }
        return "Round started"
    }

    private func continueSubtitle(for r: RoundSummary) -> String {
        var parts: [String] = []
        if r.holes_played > 0 {
            parts.append("Through \(r.holes_played) \(r.holes_played == 1 ? "hole" : "holes")")
        }
        parts.append(relativeTime(from: r.started_at))
        return parts.joined(separator: " · ")
    }

    private func conflictMessage(for r: RoundSummary) -> String {
        let where_ = r.course_name.flatMap { $0.isEmpty ? nil : " at \($0)" } ?? ""
        let when = relativeTime(from: r.started_at)
        if r.holes_played > 0 {
            return "You're \(r.holes_played) holes into a round\(where_) (\(when)). Resume it, or wrap it up to start fresh?"
        }
        return "You started a round\(where_) \(when). Resume it, or wrap it up to start fresh?"
    }

    private func relativeTime(from iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = formatter.date(from: iso)
        if date == nil {
            formatter.formatOptions = [.withInternetDateTime]
            date = formatter.date(from: iso)
        }
        guard let d = date else { return "earlier" }
        let rel = RelativeDateTimeFormatter()
        rel.unitsStyle = .abbreviated
        return rel.localizedString(for: d, relativeTo: Date())
    }
}

private struct RoundDeepLink: Identifiable {
    let id: String
}

#Preview {
    HomeView(deepLinkedRoundId: .constant(nil))
        .environmentObject(AppState())
        .environmentObject(AuthManager())
        .preferredColorScheme(.dark)
}
