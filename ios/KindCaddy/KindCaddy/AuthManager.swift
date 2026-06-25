import AuthenticationServices
import Foundation
import GoogleSignIn
import SwiftUI

@MainActor
class AuthManager: ObservableObject {
    enum AuthProvider: String, Codable {
        case apple, google, unknown
    }

    struct AuthUser: Codable {
        let id: String
        let email: String?
        let displayName: String?
        let provider: AuthProvider
    }

    @Published var isAuthenticated: Bool = false
    @Published var currentUser: AuthUser? = nil
    @Published var isLoading: Bool = false
    @Published var errorMessage: String? = nil

    private let userDefaultsKey = "kindcaddy_auth_user"
    private let keychainTokenKey = "access_token"
    private let api = APIClient.shared

    init() {
        restoreSession()
    }

    // MARK: - Apple Sign In

    func handleAppleSignIn(result: Result<ASAuthorization, Error>) {
        switch result {
        case .success(let auth):
            guard let credential = auth.credential as? ASAuthorizationAppleIDCredential else {
                errorMessage = "Invalid Apple credential."
                return
            }
            guard let tokenData = credential.identityToken,
                  let identityToken = String(data: tokenData, encoding: .utf8) else {
                errorMessage = "Could not extract Apple identity token."
                return
            }

            let email = credential.email
            let name = [credential.fullName?.givenName, credential.fullName?.familyName]
                .compactMap { $0 }
                .joined(separator: " ")

            Task {
                await authenticateWithApple(
                    identityToken: identityToken,
                    displayName: name.isEmpty ? nil : name,
                    email: email
                )
            }

        case .failure(let error):
            if (error as NSError).code == ASAuthorizationError.canceled.rawValue {
                return
            }
            errorMessage = error.localizedDescription
        }
    }

    private func authenticateWithApple(identityToken: String, displayName: String?, email: String?) async {
        isLoading = true
        errorMessage = nil
        await api.setBaseURL(Config.backendBaseURL)

        do {
            let response = try await api.authApple(
                identityToken: identityToken,
                displayName: displayName,
                email: email
            )
            handleAuthResponse(response)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    // MARK: - Google Sign In

    func signInWithGoogle() {
        guard !Config.googleClientID.isEmpty else {
            errorMessage = "Google Sign-In not configured. Add GOOGLE_CLIENT_ID to Secrets.xcconfig."
            return
        }

        isLoading = true
        errorMessage = nil

        let config = GIDConfiguration(clientID: Config.googleClientID)
        GIDSignIn.sharedInstance.configuration = config

        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let rootVC = windowScene.windows.first?.rootViewController else {
            errorMessage = "Could not find root view controller."
            isLoading = false
            return
        }

        GIDSignIn.sharedInstance.signIn(withPresenting: rootVC) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                if let error {
                    if (error as NSError).code == GIDSignInError.canceled.rawValue {
                        self.isLoading = false
                        return
                    }
                    self.errorMessage = error.localizedDescription
                    self.isLoading = false
                    return
                }

                guard let user = result?.user,
                      let idToken = user.idToken?.tokenString else {
                    self.errorMessage = "Could not get Google ID token."
                    self.isLoading = false
                    return
                }

                let email = user.profile?.email
                let displayName = user.profile?.name

                await self.authenticateWithGoogle(
                    idToken: idToken,
                    displayName: displayName,
                    email: email
                )
            }
        }
    }

    private func authenticateWithGoogle(idToken: String, displayName: String?, email: String?) async {
        await api.setBaseURL(Config.backendBaseURL)

        do {
            let response = try await api.authGoogle(
                idToken: idToken,
                displayName: displayName,
                email: email
            )
            handleAuthResponse(response)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    // MARK: - Auth response handling

    private func handleAuthResponse(_ response: AuthResponse) {
        KeychainHelper.save(key: keychainTokenKey, value: response.access_token)

        let provider: AuthProvider
        switch response.user.provider {
        case "apple": provider = .apple
        case "google": provider = .google
        default: provider = .unknown
        }

        let user = AuthUser(
            id: response.user.id,
            email: response.user.email,
            displayName: response.user.display_name,
            provider: provider
        )
        signIn(user: user)
        Task {
            await api.trackEvent(
                name: "auth_success",
                properties: ["provider": provider.rawValue]
            )
        }
    }

    // MARK: - Session

    func signIn(user: AuthUser) {
        currentUser = user
        isAuthenticated = true
        isLoading = false
        persistSession(user)

        Task { await api.setAuthToken(
            KeychainHelper.load(key: keychainTokenKey) ?? ""
        ) }
    }

    func signOut() {
        Task {
            await api.trackEvent(
                name: "auth_sign_out",
                properties: ["provider": currentUser?.provider.rawValue ?? "unknown"]
            )
        }
        currentUser = nil
        isAuthenticated = false
        UserDefaults.standard.removeObject(forKey: userDefaultsKey)
        UserDefaults.standard.removeObject(forKey: "kindcaddy.seenMicTip")
        UserDefaults.standard.removeObject(forKey: "kindcaddy.seenScorecardTip")
        KeychainHelper.delete(key: keychainTokenKey)
        Task { await api.clearAuthToken() }
    }

    private func persistSession(_ user: AuthUser) {
        if let data = try? JSONEncoder().encode(user) {
            UserDefaults.standard.set(data, forKey: userDefaultsKey)
        }
    }

    private func restoreSession() {
        guard let data = UserDefaults.standard.data(forKey: userDefaultsKey),
              let user = try? JSONDecoder().decode(AuthUser.self, from: data) else { return }

        guard let token = KeychainHelper.load(key: keychainTokenKey), !token.isEmpty else {
            UserDefaults.standard.removeObject(forKey: userDefaultsKey)
            return
        }

        currentUser = user
        isAuthenticated = true

        Task {
            await api.setBaseURL(Config.backendBaseURL)
            await api.setAuthToken(token)
        }
    }
}
