import SwiftUI

struct WeatherAttributionView: View {
    let source: WeatherDataSource
    var compact = false

    var body: some View {
        Group {
            if let url = source.attributionURL {
                Link(destination: url) {
                    attributionLabel
                }
            } else {
                attributionLabel
            }
        }
        .accessibilityLabel(source == .weatherKit
                            ? "Weather data source: Apple Weather"
                            : "Weather data source: Open-Meteo")
    }

    private var attributionLabel: some View {
        HStack(spacing: 4) {
            Text(compact ? source.displayName : "Weather data: \(source.displayName)")
            if source.attributionURL != nil {
                Image(systemName: "arrow.up.right")
                    .font(.system(size: compact ? 7 : 8, weight: .semibold))
            }
        }
        .font(.system(size: compact ? 10 : 11, weight: .medium, design: .serif))
        .foregroundStyle(Theme.textTertiary)
        .underline(source == .weatherKit, color: Theme.textTertiary)
    }
}

struct HistoricalWeatherAttributionView: View {
    var body: some View {
        HStack(spacing: 4) {
            Text("Weather data:")
            Link(destination: URL(string: "https://weatherkit.apple.com/legal-attribution.html")!) {
                Text("\u{F8FF} Weather")
                    .underline(color: Theme.textTertiary)
            }
            Text("or")
            Link(destination: URL(string: "https://open-meteo.com/")!) {
                Text("Open-Meteo")
            }
            Image(systemName: "arrow.up.right")
                .font(.system(size: 7, weight: .semibold))
        }
        .font(.system(size: 10, weight: .medium, design: .serif))
        .foregroundStyle(Theme.textTertiary)
        .accessibilityLabel("Historical weather data source may be Apple Weather or Open-Meteo")
    }
}

