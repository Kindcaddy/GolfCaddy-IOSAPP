import SwiftUI

struct StatsView: View {
    @State private var stats: StatsResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                if isLoading && stats == nil {
                    ProgressView().tint(Theme.accent)
                } else if let stats, stats.total_rounds > 0 {
                    ScrollView {
                        VStack(spacing: 20) {
                            overviewCard(stats)
                            scoringDistCard(stats.scoring_distribution)
                            if stats.miss_tendencies.left + stats.miss_tendencies.right +
                               stats.miss_tendencies.short + stats.miss_tendencies.long > 0 {
                                missTendenciesCard(stats.miss_tendencies)
                            }
                            if !stats.recent_rounds.isEmpty {
                                trendCard(stats.recent_rounds)
                            }
                        }
                        .padding()
                    }
                } else {
                    emptyState
                }
            }
            .navigationTitle("Stats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadStats() }
            .refreshable { await loadStats() }
            .alert("Error", isPresented: .init(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
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
                Image(systemName: "chart.bar.fill")
                    .font(.system(size: 36, weight: .medium))
                    .foregroundStyle(Theme.accentDimmed)
            }
            VStack(spacing: 8) {
                Text("No Stats Yet")
                    .font(Theme.sectionTitle)
                    .foregroundStyle(Theme.textPrimary)
                Text("Complete rounds to see your trends and statistics")
                    .font(Theme.captionSerif)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
            }
            Spacer()
        }
        .padding(.horizontal, 40)
    }

    // MARK: - Overview

    private func overviewCard(_ s: StatsResponse) -> some View {
        VStack(spacing: 12) {
            Text("Overview")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)

            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible()),
                GridItem(.flexible()),
            ], spacing: 16) {
                overviewStat(value: "\(s.total_rounds)", label: "Rounds")
                overviewStat(value: "\(s.total_holes)", label: "Holes")
                overviewStat(
                    value: formatVsPar(s.avg_score_vs_par),
                    label: "Avg vs Par",
                    color: s.avg_score_vs_par < 0 ? Theme.success : s.avg_score_vs_par == 0 ? Theme.accent : Theme.error
                )
                if let best = s.best_score_vs_par {
                    overviewStat(value: formatVsPar(Double(best)), label: "Best", color: Theme.success)
                }
                if let worst = s.worst_score_vs_par {
                    overviewStat(value: formatVsPar(Double(worst)), label: "Worst", color: Theme.error)
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func overviewStat(value: String, label: String, color: Color = Theme.textPrimary) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(size: 24, weight: .bold, design: .serif).monospacedDigit())
                .foregroundStyle(color)
            Text(label.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .tracking(1.0)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Theme.surfaceBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Scoring Distribution

    private func scoringDistCard(_ dist: ScoringDistribution) -> some View {
        let items: [(String, Int, Color)] = [
            ("Eagle+", dist.eagle_or_better, .yellow),
            ("Birdie", dist.birdie, .green),
            ("Par", dist.par, Theme.accent),
            ("Bogey", dist.bogey, Theme.error.opacity(0.7)),
            ("Double", dist.double_bogey, Theme.error),
            ("Triple+", dist.triple_or_worse, Theme.error),
        ]
        let total = items.reduce(0) { $0 + $1.1 }

        return VStack(alignment: .leading, spacing: 12) {
            Text("Scoring Distribution")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            ForEach(items, id: \.0) { label, count, color in
                HStack(spacing: 10) {
                    Text(label)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 52, alignment: .leading)

                    GeometryReader { geo in
                        let fraction = total > 0 ? CGFloat(count) / CGFloat(total) : 0
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 4)
                                .fill(Theme.surfaceBackground)
                            RoundedRectangle(cornerRadius: 4)
                                .fill(color)
                                .frame(width: max(fraction * geo.size.width, count > 0 ? 4 : 0))
                        }
                    }
                    .frame(height: 20)

                    Text("\(count)")
                        .font(.system(size: 14, weight: .semibold).monospacedDigit())
                        .foregroundStyle(Theme.textPrimary)
                        .frame(width: 28, alignment: .trailing)

                    if total > 0 {
                        Text("\(Int(Double(count) / Double(total) * 100))%")
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.textTertiary)
                            .frame(width: 30, alignment: .trailing)
                    }
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Miss Tendencies

    private func missTendenciesCard(_ miss: MissTendencies) -> some View {
        let items: [(String, Int, String)] = [
            ("Left", miss.left, "arrow.left"),
            ("Right", miss.right, "arrow.right"),
            ("Short", miss.short, "arrow.down"),
            ("Long", miss.long, "arrow.up"),
        ]
        let total = items.reduce(0) { $0 + $1.1 }

        return VStack(alignment: .leading, spacing: 12) {
            Text("Miss Tendencies")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                ForEach(items, id: \.0) { label, count, icon in
                    HStack {
                        Image(systemName: icon)
                            .foregroundStyle(Theme.accent)
                        Text(label)
                            .font(.subheadline)
                            .foregroundStyle(Theme.textSecondary)
                        Spacer()
                        Text("\(count)")
                            .font(.title3.weight(.bold).monospacedDigit())
                            .foregroundStyle(Theme.textPrimary)
                        if total > 0 {
                            Text("(\(Int(Double(count) / Double(total) * 100))%)")
                                .font(.caption)
                                .foregroundStyle(Theme.textSecondary)
                        }
                    }
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Score Trend

    private func trendCard(_ rounds: [RecentRoundStat]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Trend")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            let values = rounds.map { $0.score_vs_par }
            let minVal = (values.min() ?? 0) - 2
            let maxVal = (values.max() ?? 0) + 2
            let range = max(CGFloat(maxVal - minVal), 1)

            GeometryReader { geo in
                let w = geo.size.width
                let h = geo.size.height
                let stepX = values.count > 1 ? w / CGFloat(values.count - 1) : w / 2

                // Zero line (par)
                let zeroY = h * CGFloat(maxVal) / range
                Path { path in
                    path.move(to: CGPoint(x: 0, y: zeroY))
                    path.addLine(to: CGPoint(x: w, y: zeroY))
                }
                .stroke(Theme.accent.opacity(0.35), style: StrokeStyle(lineWidth: 1, dash: [5, 4]))

                // Area fill beneath line
                Path { path in
                    for (i, v) in values.enumerated() {
                        let x = values.count > 1 ? CGFloat(i) * stepX : w / 2
                        let y = h * CGFloat(maxVal - v) / range
                        if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                        else { path.addLine(to: CGPoint(x: x, y: y)) }
                    }
                    if let lastV = values.last {
                        let lastX = values.count > 1 ? CGFloat(values.count - 1) * stepX : w / 2
                        let lastY = h * CGFloat(maxVal - lastV) / range
                        path.addLine(to: CGPoint(x: lastX, y: h))
                        path.addLine(to: CGPoint(x: 0, y: h))
                        path.closeSubpath()
                    }
                }
                .fill(Theme.accent.opacity(0.07))

                // Stroke line
                Path { path in
                    for (i, v) in values.enumerated() {
                        let x = values.count > 1 ? CGFloat(i) * stepX : w / 2
                        let y = h * CGFloat(maxVal - v) / range
                        if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                        else { path.addLine(to: CGPoint(x: x, y: y)) }
                    }
                }
                .stroke(Theme.accent, lineWidth: 2)

                // Dots
                ForEach(Array(values.enumerated()), id: \.offset) { i, v in
                    let x = values.count > 1 ? CGFloat(i) * stepX : w / 2
                    let y = h * CGFloat(maxVal - v) / range
                    ZStack {
                        Circle()
                            .fill(Theme.cardBackground)
                            .frame(width: 10, height: 10)
                        Circle()
                            .fill(v < 0 ? Theme.success : v == 0 ? Theme.accent : Theme.error)
                            .frame(width: 7, height: 7)
                    }
                    .position(x: x, y: y)
                }
            }
            .frame(height: 150)
            .padding(.vertical, 4)

            HStack {
                Text("← Oldest")
                    .font(.caption2)
                    .foregroundStyle(Theme.textSecondary)
                Spacer()
                Text("Most Recent →")
                    .font(.caption2)
                    .foregroundStyle(Theme.textSecondary)
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Helpers

    private func loadStats() async {
        isLoading = true
        do {
            stats = try await api.getRoundStats()
        } catch let error as APIError {
            if case .unauthorized = error {
                errorMessage = error.localizedDescription
            }
            // 404 or other errors: leave stats nil → empty state shown
        } catch {
            // Network errors: leave stats nil → empty state shown
        }
        isLoading = false
    }

    private func formatVsPar(_ v: Double) -> String {
        if abs(v) < 0.05 { return "E" }
        if v > 0 { return String(format: "+%.1f", v) }
        return String(format: "%.1f", v)
    }
}

#Preview {
    StatsView()
        .preferredColorScheme(.dark)
}
