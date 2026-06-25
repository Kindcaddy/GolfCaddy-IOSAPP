import CoreLocation
import WeatherKit

enum WeatherDataSource: Equatable {
    case weatherKit
    case openMeteo

    var displayName: String {
        switch self {
        case .weatherKit: return "\u{F8FF} Weather"
        case .openMeteo: return "Open-Meteo"
        }
    }

    var attributionURL: URL? {
        switch self {
        case .weatherKit:
            return URL(string: "https://weatherkit.apple.com/legal-attribution.html")
        case .openMeteo:
            return URL(string: "https://open-meteo.com/")
        }
    }
}

struct WeatherKitData {
    let tempF: Double
    let windSpeedMph: Double
    let windDeg: Double
    let windGustMph: Double
    let humidity: Int
    let description: String
    let source: WeatherDataSource

    var summary: String {
        var parts = [String(format: "%.0f°F", tempF)]
        if windSpeedMph > 2 {
            parts.append(String(format: "wind %.0fmph from %@", windSpeedMph, compassLabel(windDeg)))
            if windGustMph > windSpeedMph + 3 {
                parts.append(String(format: "gusts %.0fmph", windGustMph))
            }
        }
        parts.append("\(humidity)% humidity")
        parts.append(description)
        return parts.joined(separator: ", ")
    }

    private func compassLabel(_ deg: Double) -> String {
        let directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        let idx = Int((deg / 45).rounded()) % 8
        return directions[idx]
    }
}

actor WeatherKitService {
    private let service = WeatherService.shared

    func fetch(latitude: Double, longitude: Double) async throws -> WeatherKitData {
        let location = CLLocation(latitude: latitude, longitude: longitude)
        let weather = try await service.weather(for: location)
        let current = weather.currentWeather

        let tempF = current.temperature.converted(to: .fahrenheit).value
        let windMph = current.wind.speed.converted(to: .milesPerHour).value
        let windDeg = current.wind.direction.converted(to: .degrees).value
        let gustMph = current.wind.gust?.converted(to: .milesPerHour).value ?? 0
        let humidity = Int(current.humidity * 100)
        let description = Self.conditionDescription(current.condition)

        return WeatherKitData(
            tempF: tempF,
            windSpeedMph: windMph,
            windDeg: windDeg,
            windGustMph: gustMph,
            humidity: humidity,
            description: description,
            source: .weatherKit
        )
    }

    private static func conditionDescription(_ condition: WeatherCondition) -> String {
        switch condition {
        case .clear:                    return "clear sky"
        case .mostlyClear:              return "mainly clear"
        case .partlyCloudy:             return "partly cloudy"
        case .mostlyCloudy, .cloudy:    return "overcast"
        case .foggy:                    return "foggy"
        case .haze:                     return "hazy"
        case .drizzle:                  return "drizzle"
        case .rain:                     return "rain"
        case .heavyRain:                return "heavy rain"
        case .snow, .heavySnow:         return "snow"
        case .flurries:                 return "flurries"
        case .thunderstorms:            return "thunderstorm"
        case .tropicalStorm, .hurricane: return "severe storm"
        case .windy, .breezy:           return "windy"
        case .hot:                      return "hot"
        case .frigid:                   return "frigid"
        case .blowingDust:              return "blowing dust"
        case .smoky:                    return "smoky"
        default:                        return "unknown"
        }
    }
}

extension WeatherKitData {
    /// Populate UI from `/weather/update` when the server used Open-Meteo (WeatherKit unavailable).
    init(fromServer response: WeatherUpdateResponse) {
        tempF = response.temp_f
        windSpeedMph = response.wind_speed_mph
        windDeg = response.wind_deg
        windGustMph = response.wind_gust_mph
        humidity = response.humidity
        description = response.description
        source = .openMeteo
    }
}
