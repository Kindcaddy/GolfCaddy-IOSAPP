import SwiftUI

private enum OnboardingStep {
    case name, skillLevel, handicapDetail
}

struct OnboardingView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @FocusState private var focusedField: String?

    @State private var step: OnboardingStep = .name
    @State private var localName: String = ""
    @State private var localHandicap: Double = 15
    @State private var localShotShape: String = "fade"
    @State private var localHanded: String = "right"

    private let api = APIClient.shared

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                logoHeader

                Group {
                    switch step {
                    case .name:         nameStep
                    case .skillLevel:   skillLevelStep
                    case .handicapDetail: handicapDetailStep
                    }
                }
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)
                ))
                .animation(.easeInOut(duration: 0.25), value: step)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { focusedField = nil }
            }
        }
    }

    // MARK: - Logo

    private var logoHeader: some View {
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
        .padding(.top, 40)
        .padding(.bottom, 24)
    }

    // MARK: - Step 1: Name

    private var nameStep: some View {
        VStack(spacing: 28) {
            Text("What should I call you?")
                .font(Theme.serifFont(22, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)

            TextField("Your name", text: $localName)
                .font(.title3)
                .multilineTextAlignment(.center)
                .textFieldStyle(.plain)
                .foregroundStyle(Theme.textPrimary)
                .focused($focusedField, equals: "name")
                .padding()
                .background(Theme.cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(
                            focusedField == "name" ? Theme.accent.opacity(0.6) : Theme.border,
                            lineWidth: focusedField == "name" ? 1.5 : 1
                        )
                )
                .animation(.easeInOut(duration: 0.15), value: focusedField == "name")
                .padding(.horizontal, 40)

            actionButton(label: "Continue", icon: nil) {
                focusedField = nil
                withAnimation { step = .skillLevel }
            }
            .disabled(localName.trimmingCharacters(in: .whitespaces).isEmpty)
            .opacity(localName.trimmingCharacters(in: .whitespaces).isEmpty ? 0.55 : 1.0)

            Spacer()
        }
        .padding(.top, 8)
    }

    // MARK: - Step 2: Beginner or experienced

    private var skillLevelStep: some View {
        VStack(spacing: 20) {
            Text("Are you new to golf?")
                .font(Theme.serifFont(22, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)

            // YES
            Button {
                Task { await startAsBeginner() }
            } label: {
                VStack(spacing: 6) {
                    Text("Yes, I'm new")
                        .font(Theme.headline)
                        .foregroundStyle(Theme.background)
                    Text("I'll guide you through your first round")
                        .font(.caption)
                        .foregroundStyle(Theme.background.opacity(0.75))
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 22)
                .background(Theme.accent)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(appState.isLoading)
            .padding(.horizontal, Theme.spacingLG)

            // NO
            Button {
                withAnimation { step = .handicapDetail }
            } label: {
                VStack(spacing: 6) {
                    Text("No, I have a handicap")
                        .font(Theme.headline)
                        .foregroundStyle(Theme.textPrimary)
                    Text("I'll tailor advice to your game")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 22)
                .background(Theme.cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(Theme.border, lineWidth: 1)
                )
            }
            .disabled(appState.isLoading)
            .padding(.horizontal, Theme.spacingLG)

            loadingIndicator

            Spacer()
        }
        .padding(.top, 8)
    }

    // MARK: - Step 3: Handicap + shape + handedness

    private var handicapDetailStep: some View {
        ScrollView {
            VStack(spacing: 28) {
                Text("Tell me about your game")
                    .font(Theme.serifFont(22, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)

                VStack(spacing: 0) {
                    inputRow(label: "Handicap") {
                        TextField("15", value: $localHandicap, format: .number)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 60)
                            .focused($focusedField, equals: "handicap")
                    }
                    Divider().overlay(Theme.border)
                    pickerRow(label: "Shot Shape") {
                        Picker("", selection: $localShotShape) {
                            Text("Fade").tag("fade")
                            Text("Draw").tag("draw")
                            Text("Straight").tag("straight")
                        }
                        .pickerStyle(.segmented)
                        .tint(Theme.accent)
                        .frame(width: 195)
                    }
                    Divider().overlay(Theme.border)
                    pickerRow(label: "Handed") {
                        Picker("", selection: $localHanded) {
                            Text("Right").tag("right")
                            Text("Left").tag("left")
                        }
                        .pickerStyle(.segmented)
                        .tint(Theme.accent)
                        .frame(width: 130)
                    }
                }
                .background(Theme.cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .padding(.horizontal)

                actionButton(label: "Start Round", icon: "figure.golf") {
                    Task { await startAsExperienced() }
                }
                .disabled(appState.isLoading)
                .overlay {
                    if appState.isLoading {
                        RoundedRectangle(cornerRadius: 14)
                            .fill(Theme.accent)
                            .overlay {
                                VStack(spacing: 4) {
                                    ProgressView().tint(Theme.background)
                                    if !appState.loadingMessage.isEmpty {
                                        Text(appState.loadingMessage)
                                            .font(.caption2)
                                            .foregroundStyle(Theme.background.opacity(0.8))
                                    }
                                }
                            }
                    }
                }

                errorLabel

                Spacer(minLength: 40)
            }
            .padding(.top, 8)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    // MARK: - Actions

    private func startAsBeginner() async {
        appState.profile.name = localName.trimmingCharacters(in: .whitespaces)
        appState.profile.handicap = 36
        appState.profile.shot_shape = "straight"
        appState.isBeginnerMode = true
        // Ensure tutorial shows on first RoundView open
        UserDefaults.standard.set(false, forKey: "hasSeenRoundTutorial")
        await runQuickStart(handicap: 36, gender: "male")
    }

    private func startAsExperienced() async {
        appState.profile.name = localName.trimmingCharacters(in: .whitespaces)
        appState.profile.handicap = localHandicap
        appState.profile.shot_shape = localShotShape
        appState.profile.handed = localHanded
        appState.isBeginnerMode = false
        await runQuickStart(handicap: localHandicap, gender: appState.profile.physical.gender.isEmpty ? "male" : appState.profile.physical.gender)
    }

    private func runQuickStart(handicap: Double, gender: String) async {
        appState.errorMessage = nil
        if appState.profile.clubs.isEmpty {
            do {
                let resp = try await api.estimateDistances(handicap: handicap, driverSpeed: nil, gender: gender)
                appState.profile.clubs = resp.clubs
            } catch {
                // Non-fatal — caddy works without preset distances
            }
        }
        await appState.startSession()
    }

    // MARK: - Shared helpers

    @ViewBuilder
    private func actionButton(label: String, icon: String?, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Spacer()
                if let icon {
                    Label(label, systemImage: icon)
                        .font(Theme.headline)
                        .foregroundStyle(Theme.background)
                } else {
                    Text(label)
                        .font(Theme.headline)
                        .foregroundStyle(Theme.background)
                }
                Spacer()
            }
            .frame(height: Theme.minTouchHeight)
            .background(Theme.accent)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .padding(.horizontal, Theme.spacingLG)
    }

    @ViewBuilder
    private func inputRow<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(label)
                .font(Theme.bodySerif)
                .foregroundStyle(Theme.textPrimary)
            Spacer()
            content().foregroundStyle(Theme.textPrimary)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private func pickerRow<Content: View>(label: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(label)
                .font(Theme.bodySerif)
                .foregroundStyle(Theme.textPrimary)
            Spacer()
            content()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var loadingIndicator: some View {
        if appState.isLoading {
            VStack(spacing: 6) {
                ProgressView().tint(Theme.accent)
                if !appState.loadingMessage.isEmpty {
                    Text(appState.loadingMessage)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    @ViewBuilder
    private var errorLabel: some View {
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
        }
    }
}

#Preview {
    OnboardingView()
        .environmentObject(AppState())
        .environmentObject(AuthManager())
        .preferredColorScheme(.dark)
}
