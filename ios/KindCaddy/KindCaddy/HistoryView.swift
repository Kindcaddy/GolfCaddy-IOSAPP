import SwiftUI

struct HistoryView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var appState: AppState
    @State private var rounds: [RoundSummary] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var selectedRound: RoundSummary?

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                if isLoading && rounds.isEmpty {
                    ProgressView()
                        .tint(Theme.accent)
                } else if rounds.isEmpty {
                    emptyState
                } else {
                    roundsList
                }
            }
            .navigationTitle("Round History")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadRounds() }
            .refreshable { await loadRounds() }
            .alert("Error", isPresented: .init(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .navigationDestination(item: $selectedRound) { round in
                RoundDetailView(roundId: round.id)
                    .environmentObject(authManager)
                    .environmentObject(appState)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            Spacer()
            ZStack {
                Circle()
                    .fill(Theme.accentSubtle)
                    .frame(width: 88, height: 88)
                Image(systemName: "flag.fill")
                    .font(.system(size: 36, weight: .medium))
                    .foregroundStyle(Theme.accentDimmed)
            }
            VStack(spacing: 8) {
                Text("No Rounds Yet")
                    .font(Theme.sectionTitle)
                    .foregroundStyle(Theme.textPrimary)
                Text("Complete a round to see your history here")
                    .font(Theme.captionSerif)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
            }
            Spacer()
        }
        .padding(.horizontal, 40)
    }

    private var roundsList: some View {
        List(rounds) { round in
            Button {
                selectedRound = round
            } label: {
                RoundRowView(round: round)
            }
            .listRowBackground(Theme.cardBackground)
            .listRowSeparatorTint(Theme.border)
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive) {
                    Task { await deleteRound(round) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        }
        .scrollContentBackground(.hidden)
        .listStyle(.insetGrouped)
    }

    private func deleteRound(_ round: RoundSummary) async {
        do {
            try await api.deleteRound(roundId: round.id)
            rounds.removeAll { $0.id == round.id }
            ChatCache.shared.deleteRound(roundId: round.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadRounds() async {
        isLoading = true
        do {
            let resp = try await api.getRounds()
            rounds = resp.rounds
        } catch let error as APIError {
            if case .unauthorized = error {
                errorMessage = error.localizedDescription
            }
            // 404 or other errors: leave rounds empty → empty state shown
        } catch {
            // Network errors: leave rounds empty → empty state shown
        }
        isLoading = false
    }
}

// MARK: - Round Row

struct RoundRowView: View {
    let round: RoundSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(formattedDate)
                        .font(Theme.headline)
                        .foregroundStyle(Theme.textPrimary)
                    if let course = round.course_name, !course.isEmpty {
                        Label(course, systemImage: "mappin")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer()
                statusBadge
            }

            if round.holes_played > 0 {
                HStack(spacing: 0) {
                    scoreChip(value: "\(round.total_strokes)", label: "Score")
                    if let vs = round.score_vs_par {
                        scoreChip(value: scoreLabelText(vs), label: "vs Par", valueColor: scoreColor(vs))
                    }
                    scoreChip(value: "\(round.holes_played)", label: "Holes")
                    if let target = round.target_score {
                        scoreChip(value: "\(target)", label: "Target")
                    }
                }
            }
        }
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private func scoreChip(value: String, label: String, valueColor: Color = Theme.textPrimary) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: 16, weight: .bold).monospacedDigit())
                .foregroundStyle(valueColor)
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
    }

    private var formattedDate: String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: round.started_at) {
            let display = DateFormatter()
            display.dateStyle = .medium
            display.timeStyle = .short
            return display.string(from: date)
        }
        // Fallback: try without fractional seconds
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: round.started_at) {
            let display = DateFormatter()
            display.dateStyle = .medium
            display.timeStyle = .short
            return display.string(from: date)
        }
        return round.started_at
    }

    private var statusBadge: some View {
        let (text, color): (String, Color) = {
            switch round.status {
            case "active": return ("In Progress", Theme.accent)
            case "completed": return ("Completed", Theme.success)
            case "abandoned": return ("Abandoned", Theme.textSecondary)
            default: return (round.status, Theme.textSecondary)
            }
        }()
        return Text(text)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .clipShape(Capsule())
    }

    private func scoreLabelText(_ vs: Int) -> String {
        if vs == 0 { return "Even" }
        return vs > 0 ? "+\(vs)" : "\(vs)"
    }

    private func scoreColor(_ vs: Int) -> Color {
        if vs < 0 { return Theme.success }
        if vs == 0 { return Theme.accent }
        return Theme.error
    }
}

#Preview {
    HistoryView()
        .environmentObject(AuthManager())
        .environmentObject(AppState())
        .preferredColorScheme(.dark)
}
