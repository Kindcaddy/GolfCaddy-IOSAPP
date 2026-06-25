import StoreKit
import SwiftUI
import UIKit

struct ProfileView: View {
    @EnvironmentObject var authManager: AuthManager
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @Environment(\.dismiss) private var dismiss

    @State private var user: AuthUserInfo?
    @State private var isLoading = false
    @State private var errorMessage: String?

    @State private var isEditingName = false
    @State private var editedName = ""
    @State private var isSaving = false
    @State private var successMessage: String?
    @State private var showingPaywall = false
    @State private var memoryEnabled: Bool = true
    @State private var isUpdatingMemory = false
    @State private var showingDeleteAccountConfirmation = false
    @State private var isDeletingAccount = false
    @State private var showingLegalDocument: LegalDocument?

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                if isLoading && user == nil {
                    ProgressView().tint(Theme.accent)
                } else {
                    ScrollView {
                        VStack(spacing: 20) {
                            avatarSection
                            infoSection
                            if Config.subscriptionsEnabled {
                                subscriptionSection
                            }
                            privacySection
                            legalSection
                            if let success = successMessage {
                                successBanner(success)
                            }
                            if let error = errorMessage {
                                errorBanner(error)
                            }
                            accountSection
                            logOutSection
                        }
                        .padding()
                    }
                }
            }
            .navigationTitle("Profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Theme.accent)
                }
            }
            .task { await loadProfile() }
            .fullScreenCover(isPresented: $showingPaywall) {
                PaywallView()
                    .environmentObject(subscriptionManager)
            }
            .onChange(of: subscriptionManager.status.is_subscribed) { _, isSubscribed in
                if isSubscribed {
                    showingPaywall = false
                }
            }
            .sheet(isPresented: $isEditingName) {
                EditNameSheet(
                    currentName: user?.display_name ?? authManager.currentUser?.displayName ?? "",
                    isSaving: $isSaving,
                    onSave: { newName in
                        Task { await saveName(newName) }
                    }
                )
                .presentationDetents([.height(260)])
                .presentationDragIndicator(.visible)
            }
            .sheet(item: $showingLegalDocument) { document in
                LegalDocumentSheet(document: document)
            }
            .alert("Delete Account?", isPresented: $showingDeleteAccountConfirmation) {
                Button("Delete Account", role: .destructive) {
                    Task { await deleteAccount() }
                }
                Button("Cancel", role: .cancel) { }
            } message: {
                Text("This permanently deletes your KindCaddy account, profile, rounds, notes, insights, and device tokens. Active App Store subscriptions must still be canceled in your Apple account.")
            }
        }
    }

    // MARK: - Avatar

    private var avatarSection: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(Theme.accent.opacity(0.15))
                    .frame(width: 88, height: 88)
                Text(initials)
                    .font(.system(size: 32, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.accent)
            }
            Text(displayName)
                .font(Theme.headline)
                .foregroundStyle(Theme.textPrimary)
            if let email = user?.email ?? authManager.currentUser?.email {
                Text(email)
                    .font(.subheadline)
                    .foregroundStyle(Theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    // MARK: - Info

    private var infoSection: some View {
        VStack(spacing: 0) {
            infoRow(label: "Display Name", value: displayName) {
                editedName = displayName
                isEditingName = true
            }
            Divider().padding(.leading, 16).overlay(Theme.border)
            infoRow(label: "Email", value: user?.email ?? authManager.currentUser?.email ?? "—")
            Divider().padding(.leading, 16).overlay(Theme.border)
            infoRow(label: "Sign-in Method", value: providerLabel)
        }
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var subscriptionSection: some View {
        VStack(spacing: 0) {
            infoRow(label: "Subscription Tier", value: subscriptionLabel)
            Divider().padding(.leading, 16).overlay(Theme.border)
            infoRow(label: "Trial Rounds Remaining", value: "\(subscriptionManager.status.trial_rounds_remaining)")
            Divider().padding(.leading, 16).overlay(Theme.border)
            Button {
                showingPaywall = true
            } label: {
                HStack {
                    Text(upgradeButtonTitle)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.accent)
                    Spacer()
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(Theme.accent)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 14)
            }
            if subscriptionManager.status.is_subscribed {
                Divider().padding(.leading, 16).overlay(Theme.border)
                Button {
                    manageSubscriptions()
                } label: {
                    HStack {
                        Text("Manage Subscription")
                            .font(.body.weight(.semibold))
                            .foregroundStyle(Theme.accent)
                        Spacer()
                        Image(systemName: "arrow.up.right")
                            .font(.caption)
                            .foregroundStyle(Theme.accent)
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 14)
                }
            }
        }
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var privacySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Caddy Memory")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                    Text("Let the caddy recall what worked on similar past shots.")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if isUpdatingMemory {
                    ProgressView().tint(Theme.accent)
                } else {
                    Toggle("", isOn: Binding(
                        get: { memoryEnabled },
                        set: { newValue in
                            Task { await updateMemoryEnabled(newValue) }
                        }
                    ))
                    .labelsHidden()
                    .tint(Theme.accent)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var legalSection: some View {
        VStack(spacing: 0) {
            legalRow(title: "Privacy Policy", document: .privacy)
            Divider().padding(.leading, 16).overlay(Theme.border)
            legalRow(title: "Terms of Use", document: .terms)
            Divider().padding(.leading, 16).overlay(Theme.border)
            legalRow(title: "AI Caddy Disclaimer", document: .disclaimer)
        }
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func legalRow(title: String, document: LegalDocument) -> some View {
        Button {
            showingLegalDocument = document
        } label: {
            HStack {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textTertiary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
    }

    @ViewBuilder
    private func infoRow(label: String, value: String, onEdit: (() -> Void)? = nil) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                Text(value.isEmpty ? "—" : value)
                    .font(.body)
                    .foregroundStyle(Theme.textPrimary)
            }
            Spacer()
            if let onEdit {
                Button(action: onEdit) {
                    Image(systemName: "pencil")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(Theme.accent)
                        .frame(width: 36, height: 36)
                        .background(Theme.accent.opacity(0.12))
                        .clipShape(Circle())
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    // MARK: - Banners

    private func successBanner(_ message: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(Theme.success)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(Theme.textPrimary)
            Spacer()
        }
        .padding(14)
        .background(Theme.success.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundStyle(Theme.error)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(Theme.textPrimary)
            Spacer()
        }
        .padding(14)
        .background(Theme.error.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Log Out

    private var logOutSection: some View {
        Button(role: .destructive) {
            authManager.signOut()
            dismiss()
        } label: {
            HStack {
                Spacer()
                Label("Log Out", systemImage: "rectangle.portrait.and.arrow.right")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.error)
                Spacer()
            }
            .frame(minHeight: 52)
            .background(Theme.error.opacity(0.10))
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }
    }

    private var accountSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Account")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textSecondary)
                .textCase(.uppercase)

            Button(role: .destructive) {
                showingDeleteAccountConfirmation = true
            } label: {
                HStack {
                    if isDeletingAccount {
                        ProgressView().tint(Theme.error)
                    } else {
                        Image(systemName: "trash")
                            .font(.system(size: 16, weight: .semibold))
                    }
                    Text(isDeletingAccount ? "Deleting Account..." : "Delete Account")
                        .font(.body.weight(.semibold))
                    Spacer()
                }
                .foregroundStyle(Theme.error)
                .padding(16)
                .frame(maxWidth: .infinity)
                .background(Theme.error.opacity(0.10))
                .clipShape(RoundedRectangle(cornerRadius: 16))
            }
            .disabled(isDeletingAccount)

            Text("Deletes your account data from KindCaddy. App Store subscription management remains in your Apple account.")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
        }
    }

    // MARK: - Actions

    private func loadProfile() async {
        isLoading = true
        await subscriptionManager.refreshStatus()
        do {
            let fetched = try await api.getCurrentUser()
            user = fetched
            memoryEnabled = fetched.memory_enabled ?? true
        } catch let error as APIError {
            if case .unauthorized = error {
                errorMessage = error.localizedDescription
            }
            // Non-fatal: fall back to cached auth info
        } catch { }
        isLoading = false
    }

    /// Push a memory-toggle change to the server. Optimistically updates the UI
    /// immediately; reverts and surfaces an error banner if the server rejects.
    private func updateMemoryEnabled(_ enabled: Bool) async {
        let previous = memoryEnabled
        memoryEnabled = enabled
        isUpdatingMemory = true
        errorMessage = nil
        do {
            let updated = try await api.updateMemoryEnabled(enabled)
            user = updated
            memoryEnabled = updated.memory_enabled ?? enabled
            successMessage = enabled
                ? "Caddy will use memory of past chats."
                : "Caddy will stop recalling past chats."
            Task {
                try? await Task.sleep(for: .seconds(3))
                successMessage = nil
            }
        } catch {
            memoryEnabled = previous
            errorMessage = error.localizedDescription
        }
        isUpdatingMemory = false
    }

    private func deleteAccount() async {
        isDeletingAccount = true
        errorMessage = nil
        do {
            try await api.deleteAccount()
            authManager.signOut()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            isDeletingAccount = false
        }
    }

    private var subscriptionLabel: String {
        if subscriptionManager.status.is_subscribed {
            switch subscriptionManager.status.product_id {
            case SubscriptionManager.monthlyProductID:
                return "KindCaddy Pro Monthly"
            case SubscriptionManager.yearlyProductID:
                return "KindCaddy Pro Yearly"
            default:
                return "KindCaddy Pro"
            }
        }
        if subscriptionManager.status.trial_rounds_remaining > 0 {
            return "Free trial"
        }
        return "Trial ended"
    }

    private var upgradeButtonTitle: String {
        subscriptionManager.status.is_subscribed ? "Change Plan" : "Upgrade to KindCaddy Pro"
    }

    private func manageSubscriptions() {
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene else {
            errorMessage = "Unable to open subscription settings."
            return
        }
        Task {
            do {
                try await AppStore.showManageSubscriptions(in: scene)
                await subscriptionManager.refreshStatus()
            } catch {
                errorMessage = "Unable to open subscription settings."
            }
        }
    }

    private func saveName(_ name: String) async {
        isSaving = true
        successMessage = nil
        errorMessage = nil
        do {
            let updated = try await api.updateDisplayName(name)
            user = updated
            authManager.currentUser = AuthManager.AuthUser(
                id: updated.id,
                email: updated.email,
                displayName: updated.display_name,
                provider: authManager.currentUser?.provider ?? .unknown
            )
            successMessage = "Name updated successfully"
            isEditingName = false
            Task {
                try? await Task.sleep(for: .seconds(3))
                successMessage = nil
            }
        } catch {
            errorMessage = error.localizedDescription
            isEditingName = false
        }
        isSaving = false
    }

    // MARK: - Helpers

    private var displayName: String {
        user?.display_name ?? authManager.currentUser?.displayName ?? ""
    }

    private var initials: String {
        let name = displayName
        let parts = name.split(separator: " ")
        if parts.count >= 2 {
            return "\(parts[0].prefix(1))\(parts[1].prefix(1))".uppercased()
        }
        return String(name.prefix(2)).uppercased()
    }

    private var providerLabel: String {
        switch user?.provider ?? "" {
        case "apple": return "Apple"
        case "google": return "Google"
        default: return user?.provider ?? "—"
        }
    }
}

// MARK: - Edit Name Sheet

private struct EditNameSheet: View {
    let currentName: String
    @Binding var isSaving: Bool
    let onSave: (String) -> Void

    @State private var name: String = ""
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                VStack(spacing: 20) {
                    TextField("Display name", text: $name)
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)
                        .padding(14)
                        .background(Theme.cardBackground)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .strokeBorder(focused ? Theme.accent : Theme.border, lineWidth: 1.5)
                        )
                        .focused($focused)

                    if name.trimmingCharacters(in: .whitespaces).isEmpty {
                        Text("Name cannot be empty")
                            .font(.caption)
                            .foregroundStyle(Theme.error)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding()
            }
            .navigationTitle("Edit Name")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(Theme.textSecondary)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSaving {
                        ProgressView().tint(Theme.accent)
                    } else {
                        Button("Save") {
                            onSave(name.trimmingCharacters(in: .whitespaces))
                        }
                        .foregroundStyle(Theme.accent)
                        .fontWeight(.semibold)
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
            .onAppear {
                name = currentName
                focused = true
            }
        }
    }
}

#Preview {
    ProfileView()
        .environmentObject(AuthManager())
        .preferredColorScheme(.dark)
}
