import AuthenticationServices
import SwiftUI

private enum LoginTypography {
    /// Primary sign-in actions (Apple, Google)
    static let button = Font.system(size: 16, weight: .semibold)
    static let buttonIcon: CGFloat = 17
    /// Tagline and supporting copy
    static let secondary = Font.system(size: 14, weight: .regular, design: .serif)
    /// “Your AI Golf Caddy” — elegant serif, generous tracking
    static let tagline = Font.system(size: 24, weight: .regular, design: .serif)
    static let buttonCornerRadius: CGFloat = 14
    static let buttonHeight: CGFloat = 54
}

struct LoginView: View {
    @EnvironmentObject var authManager: AuthManager
    @State private var selectedLegalDoc: LegalDocument?

    var body: some View {
        GeometryReader { geo in
            ZStack {
                loginBackground(size: geo.size)

                VStack(spacing: 0) {
                    Spacer()

                    logoMark

                    Spacer().frame(height: 36)

                    Text("Your AI Golf Caddy")
                        .font(LoginTypography.tagline)
                        .foregroundStyle(Theme.textPrimary)
                        .tracking(1.5)
                        .multilineTextAlignment(.center)

                    Spacer().frame(height: 32)

                    signInButtons

                    Spacer().frame(height: 14)

                    legalDisclosure

                    Spacer().frame(height: 16)

                    errorLabel

                    Spacer()
                }
                .padding(.horizontal, 36)
            }
        }
        .sheet(item: $selectedLegalDoc) { doc in
            LegalDocumentSheet(document: doc)
        }
    }

    // MARK: - Background (matches logo art: vignette + grain from asset)

    private func loginBackground(size: CGSize) -> some View {
        let longest = max(size.width, size.height)
        return ZStack {
            Image("KCLoginBackdrop")
                .resizable()
                .scaledToFill()
                .frame(width: size.width, height: size.height)
                .clipped()
                // Softens the centered mark so the sharp `KCLogo` reads clearly on top
                .blur(radius: 36)

            RadialGradient(
                colors: [
                    Color(red: 0.06, green: 0.06, blue: 0.06).opacity(0.25),
                    Color.black.opacity(0.72),
                    Color.black.opacity(0.96),
                ],
                center: .center,
                startRadius: longest * 0.12,
                endRadius: longest * 0.85
            )
        }
        .ignoresSafeArea()
    }

    // MARK: - Logo

    private var logoMark: some View {
        Image("KCLogo")
            .resizable()
            .aspectRatio(contentMode: .fit)
            .frame(height: 300)
    }

    // MARK: - Sign In Buttons

    private var signInButtons: some View {
        VStack(spacing: 14) {
            appleSignInButton
            googleSignInButton
        }
    }

    private var appleSignInButton: some View {
        SignInWithAppleButton(.signIn) { request in
            request.requestedScopes = [.fullName, .email]
        } onCompletion: { result in
            authManager.handleAppleSignIn(result: result)
        }
        .signInWithAppleButtonStyle(.white)
        .frame(maxWidth: .infinity)
        .frame(height: LoginTypography.buttonHeight)
        .clipShape(RoundedRectangle(cornerRadius: LoginTypography.buttonCornerRadius))
        .disabled(authManager.isLoading)
    }

    private var googleSignInButton: some View {
        Button {
            authManager.signInWithGoogle()
        } label: {
            HStack(spacing: 12) {
                googleIcon
                Text("Sign in with Google")
                    .font(LoginTypography.button)
                    .foregroundStyle(Theme.textPrimary)
            }
            .frame(maxWidth: .infinity)
            .frame(height: LoginTypography.buttonHeight)
            .background(Color.white.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: LoginTypography.buttonCornerRadius))
            .overlay(
                RoundedRectangle(cornerRadius: LoginTypography.buttonCornerRadius)
                    .strokeBorder(Theme.border, lineWidth: 1)
            )
        }
        .disabled(authManager.isLoading)
    }

    private var googleIcon: some View {
        Text("G")
            .font(.system(size: LoginTypography.buttonIcon, weight: .bold))
            .foregroundStyle(
                .linearGradient(
                    colors: [
                        Color(red: 0.91, green: 0.26, blue: 0.21),
                        Color(red: 0.98, green: 0.74, blue: 0.18),
                        Color(red: 0.20, green: 0.66, blue: 0.33),
                        Color(red: 0.26, green: 0.52, blue: 0.96),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
    }

    private var legalDisclosure: some View {
        VStack(spacing: 10) {
            HStack(spacing: 6) {
                Text("AI CADDY")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.2)
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Theme.accent.opacity(0.14))
                    .clipShape(Capsule())
                Text("Advice is AI-generated and may be wrong.")
                    .font(.system(size: 12, weight: .regular, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
            }

            Text("By continuing, you agree to KindCaddy LLC's Privacy Policy, Terms of Use, and AI Caddy Disclaimer.")
                .font(.system(size: 12, weight: .regular, design: .serif))
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 10) {
                Button("Privacy") { selectedLegalDoc = .privacy }
                legalLinkSeparator
                Button("Terms") { selectedLegalDoc = .terms }
                legalLinkSeparator
                Button("Disclaimer") { selectedLegalDoc = .disclaimer }
            }
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Theme.accent)
            .buttonStyle(.plain)
        }
        .padding(12)
        .background(Theme.cardBackground.opacity(0.75))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(Theme.border.opacity(0.7), lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("AI-generated advice may be wrong. By continuing, you accept the Privacy Policy, Terms, and AI Caddy Disclaimer.")
    }

    private var legalLinkSeparator: some View {
        Text("•")
            .font(.system(size: 11, weight: .regular))
            .foregroundStyle(Theme.textTertiary)
    }

    // MARK: - Error

    @ViewBuilder
    private var errorLabel: some View {
        if let error = authManager.errorMessage {
            Text(error)
                .font(LoginTypography.secondary)
                .foregroundStyle(Theme.error)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Theme.error.opacity(0.10))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .transition(.opacity.combined(with: .scale(scale: 0.97)))
        }
    }
}

#Preview {
    LoginView()
        .environmentObject(AuthManager())
}
