import SwiftUI

struct InsightsView: View {
    @State private var insights: UserInsightsResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?

    private let api = APIClient.shared

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            if isLoading && insights == nil {
                ProgressView().tint(Theme.accent)
            } else if let insights, insights.rounds_analyzed > 0 {
                ScrollView {
                    VStack(spacing: 20) {
                        if !insights.club_insights.isEmpty {
                            clubDistancesSection(insights.club_insights)
                        }
                        if let sp = insights.scoring_patterns {
                            scoringPatternsSection(sp)
                        }
                        missTendenciesSection(insights.miss_tendencies)
                        if insights.improvement_trend != nil
                            || (insights.fatigue_yards_lost.map { abs($0) >= 3 } ?? false)
                            || (insights.pressure_scoring_delta.map { abs($0) >= 0.3 } ?? false) {
                            trendsSection(insights)
                        }
                    }
                    .padding()
                }
                .refreshable { await loadInsights() }
            } else if !isLoading {
                VStack(spacing: 20) {
                    Spacer()
                    ZStack {
                        Circle()
                            .fill(Theme.accentSubtle)
                            .frame(width: 88, height: 88)
                        Image(systemName: "sparkles")
                            .font(.system(size: 36, weight: .medium))
                            .foregroundStyle(Theme.accentDimmed)
                    }
                    VStack(spacing: 8) {
                        Text("No Insights Yet")
                            .font(Theme.sectionTitle)
                            .foregroundStyle(Theme.textPrimary)
                        Text("Play a few rounds and your caddy will surface patterns in your game")
                            .font(Theme.captionSerif)
                            .foregroundStyle(Theme.textSecondary)
                            .multilineTextAlignment(.center)
                    }
                    Spacer()
                }
                .padding(.horizontal, 40)
            }
        }
        .navigationTitle("Caddy Insights")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await loadInsights() }
        .alert("Error", isPresented: .init(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: - Club Distances

    private func clubDistancesSection(_ clubs: [ClubInsight]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Club Distances")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            HStack {
                Text("Club")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 50, alignment: .leading)
                Spacer()
                Text("Your Avg")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 64, alignment: .trailing)
                Text("Profile")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 56, alignment: .trailing)
                Text("Delta")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 50, alignment: .trailing)
            }

            Divider().overlay(Theme.border)

            ForEach(clubs.sorted { $0.avg_carry > $1.avg_carry }) { club in
                HStack {
                    Text(club.club)
                        .font(.body.weight(.medium))
                        .foregroundStyle(Theme.textPrimary)
                        .frame(width: 50, alignment: .leading)
                    Spacer()
                    Text("\(Int(club.avg_carry))yd")
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(Theme.textPrimary)
                        .frame(width: 64, alignment: .trailing)
                    if let profile = club.profile_carry {
                        Text("\(Int(profile))yd")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 56, alignment: .trailing)
                    } else {
                        Text("--")
                            .font(.subheadline)
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 56, alignment: .trailing)
                    }
                    if let delta = club.delta {
                        let isLong = delta > 0
                        Text("\(isLong ? "+" : "")\(Int(delta))yd")
                            .font(.caption.monospacedDigit().weight(.medium))
                            .foregroundStyle(
                                abs(delta) >= 5
                                    ? (isLong ? Theme.success : Theme.error)
                                    : Theme.textSecondary
                            )
                            .frame(width: 50, alignment: .trailing)
                    } else {
                        Text("--")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 50, alignment: .trailing)
                    }
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Scoring Patterns

    private func scoringPatternsSection(_ sp: ScoringPatterns) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Scoring Patterns")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            HStack(spacing: 8) {
                if let par3 = sp.par3_avg {
                    parAvgPill(label: "Par 3", avg: par3)
                }
                if let par4 = sp.par4_avg {
                    parAvgPill(label: "Par 4", avg: par4)
                }
                if let par5 = sp.par5_avg {
                    parAvgPill(label: "Par 5", avg: par5)
                }
            }

            if let front = sp.front9_avg, let back = sp.back9_avg {
                Divider().overlay(Theme.border)
                HStack(spacing: 8) {
                    nineAvgPill(label: "Front 9", avg: front)
                    nineAvgPill(label: "Back 9", avg: back)
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func parAvgPill(label: String, avg: Double) -> some View {
        VStack(spacing: 6) {
            let sign = avg >= 0 ? "+" : ""
            Text("\(sign)\(String(format: "%.2f", avg))")
                .font(.system(size: 20, weight: .bold, design: .serif).monospacedDigit())
                .foregroundStyle(avg < 0 ? Theme.success : avg == 0 ? Theme.accent : Theme.error)
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.0)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Theme.surfaceBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func nineAvgPill(label: String, avg: Double) -> some View {
        VStack(spacing: 6) {
            Text(String(format: "%.1f", avg))
                .font(.system(size: 20, weight: .bold, design: .serif).monospacedDigit())
                .foregroundStyle(Theme.textPrimary)
            Text("\(label) avg/hole")
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.0)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Theme.surfaceBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Miss Tendencies

    private func missTendenciesSection(_ miss: MissTendencies) -> some View {
        let total = miss.left + miss.right + miss.short + miss.long

        return VStack(alignment: .leading, spacing: 12) {
            Text("Miss Tendencies")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            if total == 0 {
                Text("No miss direction data recorded yet")
                    .font(Theme.captionSerif)
                    .foregroundStyle(Theme.textSecondary)
            } else {
                HStack(spacing: 8) {
                    missPill(label: "Left", count: miss.left, total: total)
                    missPill(label: "Right", count: miss.right, total: total)
                    missPill(label: "Short", count: miss.short, total: total)
                    missPill(label: "Long", count: miss.long, total: total)
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func missPill(label: String, count: Int, total: Int) -> some View {
        let pct = total > 0 ? Double(count) / Double(total) : 0.0
        return VStack(spacing: 6) {
            Text("\(Int(pct * 100))%")
                .font(.system(size: 20, weight: .bold, design: .serif).monospacedDigit())
                .foregroundStyle(pct >= 0.4 ? Theme.error : Theme.textPrimary)
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.0)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Theme.surfaceBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Trends

    private func trendsSection(_ insights: UserInsightsResponse) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Performance Trends")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)

            if let trend = insights.improvement_trend {
                HStack(spacing: 12) {
                    Image(systemName: trendIcon(trend))
                        .foregroundStyle(trendColor(trend))
                    Text(trendLabel(trend))
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)
                    Spacer()
                    Text("\(insights.rounds_analyzed) rounds")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
            }

            if let fatigue = insights.fatigue_yards_lost, abs(fatigue) >= 3 {
                if insights.improvement_trend != nil { Divider().overlay(Theme.border) }
                HStack(spacing: 12) {
                    Image(systemName: "battery.50")
                        .foregroundStyle(Theme.textSecondary)
                    Text("Back 9 fatigue: \(String(format: "%.0f", abs(fatigue)))yd shorter on average")
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)
                    Spacer()
                }
            }

            if let pressure = insights.pressure_scoring_delta, abs(pressure) >= 0.3 {
                let isWorse = pressure > 0
                Divider().overlay(Theme.border)
                HStack(spacing: 12) {
                    Image(systemName: isWorse ? "exclamationmark.triangle" : "checkmark.circle")
                        .foregroundStyle(isWorse ? Theme.error : Theme.success)
                    Text("Holes 15-18: \(String(format: "%.1f", abs(pressure))) strokes/hole \(isWorse ? "worse" : "better") than earlier holes")
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)
                    Spacer()
                }
            }
        }
        .padding()
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func trendIcon(_ trend: String) -> String {
        switch trend {
        case "improving": return "arrow.up.right"
        case "declining": return "arrow.down.right"
        default: return "arrow.right"
        }
    }

    private func trendColor(_ trend: String) -> Color {
        switch trend {
        case "improving": return Theme.success
        case "declining": return Theme.error
        default: return Theme.accent
        }
    }

    private func trendLabel(_ trend: String) -> String {
        switch trend {
        case "improving": return "Scoring is improving"
        case "declining": return "Scoring is declining"
        default: return "Scoring is stable"
        }
    }

    // MARK: - Data loading

    private func loadInsights() async {
        isLoading = true
        do {
            insights = try await api.getInsights()
        } catch let error as APIError {
            if case .unauthorized = error {
                errorMessage = error.localizedDescription
            }
            // 404 or other API errors: leave insights nil → empty state shown
        } catch {
            // Network errors: leave insights nil → empty state shown
        }
        isLoading = false
    }
}

#Preview {
    NavigationStack {
        InsightsView()
    }
    .preferredColorScheme(.dark)
}
