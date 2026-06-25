import SwiftUI

// MARK: - Root Tab Container

struct FullProfileView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        TabView {
            SetupTabView()
                .environmentObject(appState)
                .environmentObject(authManager)
                .tabItem { Label("Setup", systemImage: "slider.horizontal.3") }

            NavigationStack {
                InsightsView()
                    .toolbar { doneToolbarItem }
            }
            .tabItem { Label("Insights", systemImage: "sparkles") }

            NavigationStack {
                StatsView()
                    .toolbar { doneToolbarItem }
            }
            .tabItem { Label("Stats", systemImage: "chart.bar.fill") }

            NavigationStack {
                HistoryView()
                    .environmentObject(authManager)
                    .toolbar { doneToolbarItem }
            }
            .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }
        }
        .tint(Theme.accent)
        .task { await subscriptionManager.refreshStatus() }
        .fullScreenCover(isPresented: Binding(
            get: { appState.paywallRequired },
            set: { isPresented in
                appState.paywallRequired = isPresented
            }
        )) {
            PaywallView()
                .environmentObject(subscriptionManager)
        }
    }

    /// Shared toolbar item that dismisses the entire Profile & Stats fullScreenCover.
    /// Profile changes auto-persist via `AppState.profile.didSet { saveProfile() }`,
    /// so "Done" is the right verb — there is no separate "Save" step required.
    @ToolbarContentBuilder
    private var doneToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button("Done") { dismiss() }
                .foregroundStyle(Theme.accent)
                .fontWeight(.semibold)
        }
    }
}

// MARK: - Setup Tab

private struct SetupTabView: View {
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focusedField: String?
    @State private var showingProfile = false
    @State private var showingAddClub = false
    @State private var addClubChoices: [String] = []
    @State private var calibrationSuggestions: [CalibrationSuggestion] = []
    @State private var isEstimatingDistances = false
    @State private var showEstimateConfirmAlert = false
    @State private var showEstimateSuccessAlert = false
    @State private var backNineFatigueYards: Int = 0

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                Form {
                    headerSection
                    golferSection
                    physicalSection
                    clubDistancesSection
                    tendenciesSection
                    startButtonSection
                    errorSection
                }
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle("KindCaddy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadCalibration() }
            .sheet(isPresented: $showingProfile) {
                ProfileView()
                    .environmentObject(authManager)
                    .environmentObject(subscriptionManager)
            }
            .sheet(isPresented: $showingAddClub) {
                AddClubSheet(
                    availableClubs: addClubChoices,
                    onAdd: addClub
                )
                .presentationDetents([.medium])
            }
            .onChange(of: showingAddClub) { _, isPresented in
                if !isPresented {
                    addClubChoices = []
                }
            }
            .alert("Estimate Club Distances?", isPresented: $showEstimateConfirmAlert) {
                Button("Replace") { Task { await runEstimateDistances() } }
                Button("Cancel", role: .cancel) { }
            } message: {
                Text("This will replace your current club distances with estimates based on your handicap.")
            }
            .alert("Distances Estimated", isPresented: $showEstimateSuccessAlert) {
                Button("OK") { }
            } message: {
                Text("Distances estimated based on your handicap. You can adjust individual clubs below.")
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Theme.accent)
                        .fontWeight(.semibold)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingProfile = true
                    } label: {
                        Image(systemName: "person.circle")
                            .foregroundStyle(Theme.accent)
                    }
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Dismiss Keyboard") { focusedField = nil }
                }
            }
        }
    }

    // MARK: - Header

    private var headerSection: some View {
        Section {
            VStack(spacing: 8) {
                Image("KCLogo")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(height: 72)
                Text("Your AI Caddy")
                    .font(.system(size: 13, weight: .regular, design: .serif))
                    .tracking(1.5)
                    .foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .listRowBackground(Color.clear)
        }
    }

    // MARK: - Golfer

    private var golferSection: some View {
        Section {
            HStack {
                Text("Name")
                Spacer()
                TextField("Jimmy", text: $appState.profile.name)
                    .multilineTextAlignment(.trailing)
                    .focused($focusedField, equals: "name")
            }
            HStack {
                Text("Handicap")
                Spacer()
                TextField("15", value: $appState.profile.handicap, format: .number)
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 60)
                    .focused($focusedField, equals: "handicap")
            }
            Picker("Shot Shape", selection: $appState.profile.shot_shape) {
                Text("Fade").tag("fade")
                Text("Draw").tag("draw")
                Text("Straight").tag("straight")
            }
            Picker("Handed", selection: $appState.profile.handed) {
                Text("Right").tag("right")
                Text("Left").tag("left")
            }
            Picker("Chat Style", selection: $appState.profile.chat_style) {
                Text("Casual").tag("casual")
                Text("Detailed").tag("detailed")
                Text("Minimal").tag("minimal")
            }
            HStack {
                Text("Target Score")
                Spacer()
                TextField("85", value: $appState.profile.target_score, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 60)
                    .focused($focusedField, equals: "target")
            }
        } header: {
            sectionHeader("Golfer")
        }
        .listRowBackground(Theme.cardBackground)
    }

    // MARK: - Physical

    private var physicalSection: some View {
        Section {
            Picker("Gender", selection: $appState.profile.physical.gender) {
                Text("Male").tag("male")
                Text("Female").tag("female")
            }
            TextField("Age Group", text: $appState.profile.physical.age_group)
                .textInputAutocapitalization(.never)
                .focused($focusedField, equals: "age")
            HStack {
                Text("Driver Speed (mph)")
                Spacer()
                TextField("105", value: $appState.profile.physical.driver_clubhead_speed_mph, format: .number)
                    .keyboardType(.decimalPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 60)
                    .focused($focusedField, equals: "speed")
            }
            TextField("Workout Frequency", text: $appState.profile.physical.workout_frequency)
                .textInputAutocapitalization(.never)
                .focused($focusedField, equals: "workout")
            TextField("Practice Frequency", text: $appState.profile.physical.practice_frequency)
                .textInputAutocapitalization(.never)
                .focused($focusedField, equals: "practice")
        } header: {
            sectionHeader("Physical")
        }
        .listRowBackground(Theme.cardBackground)
    }

    // MARK: - Club Distances

    private var clubDistancesSection: some View {
        Section {
            Button {
                if appState.profile.clubs.isEmpty {
                    Task { await runEstimateDistances() }
                } else {
                    showEstimateConfirmAlert = true
                }
            } label: {
                HStack {
                    if isEstimatingDistances {
                        ProgressView().tint(Theme.accent)
                    } else {
                        Label("Estimate My Distances", systemImage: "wand.and.stars")
                            .foregroundStyle(Theme.accent)
                    }
                    Spacer()
                }
            }
            .disabled(isEstimatingDistances)
            .listRowBackground(Theme.accent.opacity(0.12))

            ForEach(calibrationSuggestions) { suggestion in
                VStack(alignment: .leading, spacing: 8) {
                    let sign = suggestion.delta >= 0 ? "+" : ""
                    HStack {
                        Text(suggestion.club)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                        Text("averaging \(suggestion.avg_carry)yd carry")
                            .font(.system(size: 13))
                            .foregroundStyle(Theme.textPrimary)
                        Spacer()
                        Text("\(sign)\(suggestion.delta)yd")
                            .font(.system(size: 13, weight: .semibold).monospacedDigit())
                            .foregroundStyle(suggestion.delta >= 0 ? Theme.success : Theme.error)
                    }
                    Text("vs profile \(suggestion.profile_carry)yd — \(suggestion.shot_count) shots")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                    HStack(spacing: 16) {
                        Button("Update to \(suggestion.avg_carry)yd") { applyCalibration(suggestion) }
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.accent)
                        Button("Dismiss") {
                            calibrationSuggestions.removeAll { $0.club == suggestion.club }
                        }
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                    }
                }
                .padding(.vertical, 4)
            }

            ForEach(clubSortOrder, id: \.self) { club in
                if let distance = appState.profile.clubs[club] {
                    HStack {
                        Text(club)
                            .font(.body.weight(.medium))
                            .lineLimit(1)
                            .fixedSize()
                            .frame(minWidth: 48, alignment: .leading)
                        Spacer()
                        Text("Carry")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                        TextField("0", value: Binding(
                            get: { distance.carry },
                            set: { appState.profile.clubs[club]?.carry = $0 }
                        ), format: .number)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 50)
                            .focused($focusedField, equals: "club_\(club)_carry")
                        Text("Total")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                        TextField("0", value: Binding(
                            get: { distance.total },
                            set: { appState.profile.clubs[club]?.total = $0 }
                        ), format: .number)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 50)
                            .focused($focusedField, equals: "club_\(club)_total")
                    }
                }
            }
            .onDelete { offsets in
                let sorted = clubSortOrder
                for index in offsets {
                    let club = sorted[index]
                    appState.profile.clubs.removeValue(forKey: club)
                }
            }

            if !availableClubs.isEmpty {
                Button {
                    presentAddClubSheet()
                } label: {
                    Label("Add Club", systemImage: "plus.circle.fill")
                        .font(.subheadline)
                        .foregroundStyle(Theme.accent)
                }
            }
        } header: {
            sectionHeader("Club Distances (yards)")
        }
        .listRowBackground(Theme.cardBackground)
    }

    private func presentAddClubSheet() {
        focusedField = nil
        addClubChoices = availableClubs
        showingAddClub = true
    }

    private func addClub(_ club: String) {
        guard appState.profile.clubs[club] == nil else {
            showingAddClub = false
            return
        }
        appState.profile.clubs[club] = ClubDistance(carry: 0, total: 0)
        showingAddClub = false
    }

    // MARK: - Calibration helpers

    private func loadCalibration() async {
        do {
            let resp = try await api.getCalibration()
            calibrationSuggestions = resp.suggestions
        } catch {
            // Silent failure — calibration is optional
        }
    }

    private func applyCalibration(_ suggestion: CalibrationSuggestion) {
        let current = appState.profile.clubs[suggestion.club]
        let ratio: Double
        if let c = current, c.carry > 0 {
            ratio = Double(c.total) / Double(c.carry)
        } else {
            ratio = 1.065
        }
        let newCarry = suggestion.avg_carry
        let newTotal = max(newCarry, Int((Double(newCarry) * ratio).rounded()))
        appState.profile.clubs[suggestion.club] = ClubDistance(carry: newCarry, total: newTotal)
        calibrationSuggestions.removeAll { $0.club == suggestion.club }
    }

    private func runEstimateDistances() async {
        isEstimatingDistances = true
        do {
            let resp = try await api.estimateDistances(
                handicap: appState.profile.handicap,
                driverSpeed: appState.profile.physical.driver_clubhead_speed_mph,
                gender: appState.profile.physical.gender.isEmpty ? "male" : appState.profile.physical.gender
            )
            appState.profile.clubs = resp.clubs
            showEstimateSuccessAlert = true
        } catch {
            // Silent failure — user can enter manually
        }
        isEstimatingDistances = false
    }

    // MARK: - Tendencies

    private static let pressureOptions = [
        "", "pulls left", "pushes right", "chunks short",
        "skulls long", "grip gets tight", "no consistent pattern"
    ]
    private static let windOptions = [
        "", "overcompensates into wind", "underestimates wind effect",
        "loses ball flight in crosswind", "handles wind well"
    ]
    private static let generalMissOptions = [
        "", "tends to miss right", "tends to miss left",
        "tends to miss short", "tends to miss long", "no consistent miss"
    ]

    private var tendenciesSection: some View {
        Section {
            let pressureLegacy = !Self.pressureOptions.contains(appState.profile.tendencies.under_pressure)
                && !appState.profile.tendencies.under_pressure.isEmpty

            VStack(alignment: .leading, spacing: 4) {
                Picker("Under Pressure", selection: $appState.profile.tendencies.under_pressure) {
                    Text("Not set").tag("")
                    ForEach(Self.pressureOptions.filter { !$0.isEmpty }, id: \.self) { opt in
                        Text(opt.capitalized).tag(opt)
                    }
                }
                if pressureLegacy {
                    Text("Previously: \(appState.profile.tendencies.under_pressure)")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.leading, 2)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                Stepper(value: $backNineFatigueYards, in: 0...15) {
                    HStack {
                        Text("Lose yards on irons after hole 10")
                        Spacer()
                        Text(backNineFatigueYards == 0 ? "None" : "\(backNineFatigueYards) yd")
                            .foregroundStyle(backNineFatigueYards == 0 ? Theme.textSecondary : Theme.textPrimary)
                    }
                }
                .onChange(of: backNineFatigueYards) { _, yards in
                    appState.profile.tendencies.back_nine = yards == 0
                        ? ""
                        : "loses \(yards) yards on irons"
                }
            }

            let windLegacy = !Self.windOptions.contains(appState.profile.tendencies.wind)
                && !appState.profile.tendencies.wind.isEmpty

            VStack(alignment: .leading, spacing: 4) {
                Picker("In Wind", selection: $appState.profile.tendencies.wind) {
                    Text("Not set").tag("")
                    ForEach(Self.windOptions.filter { !$0.isEmpty }, id: \.self) { opt in
                        Text(opt.capitalized).tag(opt)
                    }
                }
                if windLegacy {
                    Text("Previously: \(appState.profile.tendencies.wind)")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.leading, 2)
                }
            }

            let generalLegacy = !Self.generalMissOptions.contains(appState.profile.tendencies.general)
                && !appState.profile.tendencies.general.isEmpty

            VStack(alignment: .leading, spacing: 4) {
                Picker("General Miss", selection: $appState.profile.tendencies.general) {
                    Text("Not set").tag("")
                    ForEach(Self.generalMissOptions.filter { !$0.isEmpty }, id: \.self) { opt in
                        Text(opt.capitalized).tag(opt)
                    }
                }
                if generalLegacy {
                    Text("Previously: \(appState.profile.tendencies.general)")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.leading, 2)
                }
            }
        } header: {
            sectionHeader("Tendencies")
        }
        .listRowBackground(Theme.cardBackground)
        .onAppear { backNineFatigueYards = parseFatigueYards(appState.profile.tendencies.back_nine) }
    }

    private func parseFatigueYards(_ tendency: String) -> Int {
        let digits = tendency.components(separatedBy: CharacterSet.decimalDigits.inverted)
            .compactMap { Int($0) }.first ?? 0
        return min(digits, 15)
    }

    // MARK: - Start Button

    private var startButtonSection: some View {
        Section {
            Button {
                Task { await startRoundIfAllowed() }
            } label: {
                HStack {
                    Spacer()
                    if appState.isLoading {
                        ProgressView()
                            .tint(Theme.background)
                    } else {
                        Label("Start Round", systemImage: "figure.golf")
                            .font(Theme.headline)
                            .foregroundStyle(Theme.background)
                    }
                    Spacer()
                }
                .frame(minHeight: Theme.minTouchHeight)
            }
            .disabled(appState.isLoading || appState.profile.name.isEmpty)
            .opacity(appState.profile.name.isEmpty ? 0.55 : 1.0)
            .listRowBackground(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Theme.accent)
            )
        }
    }

    private func startRoundIfAllowed() async {
        if await subscriptionManager.ensureCanStartRound() {
            await appState.startSession()
            await subscriptionManager.refreshStatus()
        }
    }

    // MARK: - Error

    @ViewBuilder
    private var errorSection: some View {
        if let error = appState.errorMessage {
            Section {
                HStack(spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.error)
                        .font(.system(size: 14))
                    Text(error)
                        .foregroundStyle(Theme.error)
                        .font(.system(size: 14, weight: .regular, design: .serif))
                }
                .padding(.vertical, 4)
            }
            .listRowBackground(Theme.error.opacity(0.08))
        }
    }

    // MARK: - Helpers

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.system(size: 11, weight: .semibold, design: .serif))
            .tracking(1.6)
            .foregroundStyle(Theme.textSecondary)
    }

    private static let allClubNames = [
        "Driver", "3W", "5W", "7W",
        "2H", "3H", "4H", "5H",
        "2i", "3i", "4i", "5i", "6i", "7i", "8i", "9i",
        "PW", "AW", "GW", "50", "52", "54", "56", "58", "60"
    ]

    private var clubSortOrder: [String] {
        let keys = Array(appState.profile.clubs.keys)
        return keys.sorted { a, b in
            let ai = Self.allClubNames.firstIndex(of: a) ?? 99
            let bi = Self.allClubNames.firstIndex(of: b) ?? 99
            return ai < bi
        }
    }

    private var availableClubs: [String] {
        Self.allClubNames.filter { appState.profile.clubs[$0] == nil }
    }
}

#Preview {
    FullProfileView()
        .environmentObject(AppState())
        .environmentObject(AuthManager())
}
