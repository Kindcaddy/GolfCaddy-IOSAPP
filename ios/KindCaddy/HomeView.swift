import SwiftUI

struct HomeView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @Binding var deepLinkedRoundId: String?
    @State private var showingProfile = false

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                // Logo
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

                // Action buttons
                VStack(spacing: 14) {
                    // Primary: Start Round
                    Button {
                        Task { await appState.startSession() }
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
                    .disabled(appState.isLoading)

                    // Secondary: Profile & Stats
                    Button {
                        showingProfile = true
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
                    .disabled(appState.isLoading)
                }
                .padding(.horizontal, Theme.spacingLG)

                if let error = appState.errorMessage {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(Theme.error)
                            .font(.system(size: 13))
                        Text(error)
                            .foregroundStyle(Theme.error)
                            .font(.system(size: 13))
                    }
                    .padding(12)
                    .background(Theme.error.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .padding(.horizontal)
                    .padding(.top, 12)
                }

                Spacer()
                    .frame(height: 60)
            }
        }
        .fullScreenCover(isPresented: $showingProfile) {
            FullProfileView()
                .environmentObject(appState)
                .environmentObject(authManager)
        }
        .sheet(item: Binding(
            get: { deepLinkedRoundId.map { RoundDeepLink(id: $0) } },
            set: { deepLinkedRoundId = $0?.id }
        )) { link in
            NavigationStack {
                RoundDetailView(roundId: link.id)
                    .environmentObject(authManager)
            }
        }
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
