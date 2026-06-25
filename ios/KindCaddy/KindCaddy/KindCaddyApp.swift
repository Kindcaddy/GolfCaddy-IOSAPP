import GoogleSignIn
import SwiftData
import SwiftUI
import UserNotifications

// MARK: - App Delegate (APNs token registration)

class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { try? await APIClient.shared.registerDeviceToken(token) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("[APNs] Registration failed: \(error.localizedDescription)")
    }

    // Show notification banner even when app is in foreground
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    // Handle tap on notification → deep link to round detail
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let info = response.notification.request.content.userInfo
        if let roundId = info["round_id"] as? String {
            NotificationCenter.default.post(
                name: .openRoundDetail,
                object: nil,
                userInfo: ["round_id": roundId]
            )
        }
        completionHandler()
    }
}

extension Notification.Name {
    static let openRoundDetail = Notification.Name("openRoundDetail")
}

// MARK: - App

@main
struct KindCaddyApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var appState = AppState()
    @StateObject private var authManager = AuthManager()
    @StateObject private var subscriptionManager = SubscriptionManager()
    @State private var deepLinkedRoundId: String? = nil

    var body: some Scene {
        WindowGroup {
            Group {
                if authManager.isAuthenticated {
                    if appState.sessionId != nil {
                        RoundView()
                            .environmentObject(appState)
                            .environmentObject(authManager)
                            .environmentObject(subscriptionManager)
                    } else if appState.isFirstLaunch {
                        OnboardingView()
                            .environmentObject(appState)
                            .environmentObject(authManager)
                            .environmentObject(subscriptionManager)
                    } else {
                        HomeView(deepLinkedRoundId: $deepLinkedRoundId)
                            .environmentObject(appState)
                            .environmentObject(authManager)
                            .environmentObject(subscriptionManager)
                    }
                } else {
                    LoginView()
                        .environmentObject(authManager)
                        .environmentObject(subscriptionManager)
                }
            }
            .preferredColorScheme(.dark)
            .tint(Theme.accent)
            .modelContainer(ChatCache.shared.container)
            .animation(.easeInOut(duration: 0.3), value: authManager.isAuthenticated)
            .sheet(isPresented: $appState.showingAIDataConsent) {
                AIDataConsentSheet(
                    onAccept: {
                        appState.acceptAIDataConsent()
                        Task { await appState.retryLastOperation() }
                    },
                    onDecline: {
                        appState.declineAIDataConsent()
                    }
                )
                .preferredColorScheme(.dark)
            }
            .onOpenURL { url in
                GIDSignIn.sharedInstance.handle(url)
            }
            .onReceive(NotificationCenter.default.publisher(for: .openRoundDetail)) { note in
                if let roundId = note.userInfo?["round_id"] as? String {
                    deepLinkedRoundId = roundId
                }
            }
            .task(id: authManager.isAuthenticated) {
                if authManager.isAuthenticated {
                    await subscriptionManager.configure()
                } else {
                    subscriptionManager.reset()
                }
            }
        }
    }
}
