import Foundation

/// Reads backend URL and API key from Info.plist, which pulls values
/// from Secrets.xcconfig at build time. Never hardcode secrets here.
enum Config {
    static let backendBaseURL: String = {
        guard let value = Bundle.main.infoDictionary?["KindCaddyBackendURL"] as? String,
              !value.isEmpty else {
            fatalError("KindCaddyBackendURL not set — copy Secrets.xcconfig.example to Secrets.xcconfig and fill in your values.")
        }
        return value
    }()

    static let apiKey: String = {
        (Bundle.main.infoDictionary?["KindCaddyAPIKey"] as? String) ?? ""
    }()

    static let googleClientID: String = {
        (Bundle.main.infoDictionary?["GoogleClientID"] as? String) ?? ""
    }()

    /// Master switch for the subscription/paywall feature. When `false`, every
    /// user is treated as fully entitled, the paywall is never shown, and
    /// StoreKit lookups are skipped. Flip to `true` to re-enable the trial +
    /// subscription gating end-to-end (also requires `KINDCADDY_SUBSCRIPTIONS_ENABLED=1`
    /// on the backend).
    static let subscriptionsEnabled: Bool = true
}
