import SwiftUI

/// Quick-start screen for first-time users.
/// Collects name + handicap, silently estimates club distances, then starts the round.
/// Returning users (name + clubs already set) go directly to FullProfileView.
struct SetupView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @FocusState private var focusedField: String?
    @State private var showingFullProfile = false
    @State private var showingAccountProfile = false
    @State private var showingPaywall = false

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 28) {
                        // Logo
                        VStack(spacing: 8) {
                            Image("KCLogo")
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(height: 80)
                            Text("Your AI Caddy")
                                .font(.system(size: 13, weight: .regular, design: .serif))
                                .tracking(1.5)
                                .foregroundStyle(Theme.textSecondary)
                        }
                        .padding(.top, 32)

                        // Input card
                        VStack(spacing: 0) {
                            inputRow(label: "Your Name") {
                                TextField("e.g. Jimmy", text: $appState.profile.name)
                                    .multilineTextAlignment(.trailing)
                                    .focused($focusedField, equals: "name")
                            }
                            Divider().overlay(Theme.border)
                            inputRow(label: "Handicap") {
                                TextField("15", value: $appState.profile.handicap, format: .number)
                                    .keyboardType(.decimalPad)
                                    .multilineTextAlignment(.trailing)
                                    .frame(width: 60)
                                    .focused($focusedField, equals: "handicap")
                            }
                        }
                        .background(Theme.cardBackground)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .padding(.horizontal)

                        // Start button
                        Button {
                            Task { await quickStart() }
                        } label: {
                            HStack {
                                Spacer()
                                if appState.isLoading {
                                    ProgressView().tint(Theme.background)
                                } else {
                                    Label("Start Round", systemImage: "figure.golf")
                                        .font(Theme.headline)
                                        .foregroundStyle(Theme.background)
                                }
                                Spacer()
                            }
                            .frame(height: Theme.minTouchHeight)
                        }
                        .disabled(appState.isLoading || appState.profile.name.trimmingCharacters(in: .whitespaces).isEmpty)
                        .opacity(appState.profile.name.trimmingCharacters(in: .whitespaces).isEmpty ? 0.55 : 1.0)
                        .background(Theme.accent)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                        .padding(.horizontal)

                        // Refine profile link
                        Button {
                            Task { await openFullProfileIfAllowed() }
                        } label: {
                            Text("Refine Your Profile")
                                .font(.subheadline)
                                .foregroundStyle(Theme.accent)
                                .underline()
                        }

                        // Error
                        if let error = appState.errorMessage {
                            HStack(spacing: 10) {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(Theme.error)
                                    .font(.system(size: 14))
                                Text(error)
                                    .foregroundStyle(Theme.error)
                                    .font(.system(size: 14, weight: .regular, design: .serif))
                            }
                            .padding(14)
                            .background(Theme.error.opacity(0.08))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                            .padding(.horizontal)
                        }

                        Spacer(minLength: 40)
                    }
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle("KindCaddy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingAccountProfile = true
                    } label: {
                        Image(systemName: "person.circle")
                            .foregroundStyle(Theme.accent)
                    }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focusedField = nil }
                }
            }
            .fullScreenCover(isPresented: $showingFullProfile) {
                FullProfileView()
                    .environmentObject(appState)
                    .environmentObject(authManager)
                    .environmentObject(subscriptionManager)
            }
            .fullScreenCover(isPresented: Binding(
                get: { showingPaywall || appState.paywallRequired },
                set: { isPresented in
                    showingPaywall = isPresented
                    appState.paywallRequired = isPresented
                }
            )) {
                PaywallView()
                    .environmentObject(subscriptionManager)
            }
            .sheet(isPresented: $showingAccountProfile) {
                ProfileView()
                    .environmentObject(authManager)
                    .environmentObject(subscriptionManager)
            }
        }
    }

    // MARK: - Quick Start

    private func quickStart() async {
        appState.errorMessage = nil
        guard await subscriptionManager.ensureCanStartRound() else {
            showingPaywall = true
            return
        }
        // Silently estimate distances if none set
        if appState.profile.clubs.isEmpty {
            do {
                let resp = try await api.estimateDistances(
                    handicap: appState.profile.handicap,
                    driverSpeed: nil,
                    gender: appState.profile.physical.gender.isEmpty ? "male" : appState.profile.physical.gender
                )
                appState.profile.clubs = resp.clubs
            } catch {
                // Non-fatal — caddy works without pre-set distances
            }
        }
        await appState.startSession()
        await subscriptionManager.refreshStatus()
    }

    private func openFullProfileIfAllowed() async {
        showingFullProfile = true
    }

    // MARK: - Helpers

    @ViewBuilder
    private func inputRow<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(label)
                .foregroundStyle(Theme.textPrimary)
            Spacer()
            content()
                .foregroundStyle(Theme.textPrimary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }
}

// MARK: - Add Club Sheet (shared between SetupView and FullProfileView)

struct AddClubSheet: View {
    let availableClubs: [String]
    let onAdd: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                List(availableClubs, id: \.self) { club in
                    Button {
                        onAdd(club)
                    } label: {
                        Text(club)
                            .font(.body.weight(.medium))
                            .foregroundStyle(Theme.textPrimary)
                    }
                    .listRowBackground(Theme.cardBackground)
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Add Club")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}

#Preview {
    SetupView()
        .environmentObject(AppState())
        .environmentObject(AuthManager())
}
