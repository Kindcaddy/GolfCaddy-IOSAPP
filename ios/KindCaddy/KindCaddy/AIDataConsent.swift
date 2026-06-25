import SwiftUI

enum AIDataConsent {
    static let currentVersion = "2026-05-22.v1"
    private static let acceptedVersionKey = "kindcaddy.aiDataConsent.acceptedVersion"

    static var hasAcceptedCurrentVersion: Bool {
        UserDefaults.standard.string(forKey: acceptedVersionKey) == currentVersion
    }

    static func acceptCurrentVersion() {
        UserDefaults.standard.set(currentVersion, forKey: acceptedVersionKey)
    }
}

struct AIDataConsentSheet: View {
    let onAccept: () -> Void
    let onDecline: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var showingLegalDocument: LegalDocument?

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        header
                        disclosureCard
                        legalLinks
                        actionButtons
                    }
                    .padding(20)
                }
            }
            .navigationTitle("AI Data Sharing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .interactiveDismissDisabled()
            .sheet(item: $showingLegalDocument) { document in
                LegalDocumentSheet(document: document)
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Before KindCaddy sends data to OpenAI")
                .font(Theme.sectionTitle)
                .foregroundStyle(Theme.textPrimary)
            Text("KindCaddy uses OpenAI APIs to generate caddy advice, transcribe certain voice recordings, create spoken replies, and power memory/search features. Please review and confirm before continuing.")
                .font(Theme.captionSerif)
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(4)
        }
    }

    private var disclosureCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            consentRow(
                icon: "person.text.rectangle",
                title: "What may be sent",
                body: "Your golfer profile, club distances, handicap, preferences, round state, hole details, scores, shot logs, location/weather context, recent conversation, and the question you ask."
            )
            consentRow(
                icon: "waveform",
                title: "Voice and audio",
                body: "Phone mic questions are converted to text by Apple Speech Recognition, which may process audio on Apple's servers. AirPods/headset recordings may be uploaded to KindCaddy and OpenAI Whisper for transcription before advice is generated."
            )
            consentRow(
                icon: "building.2",
                title: "Who receives it",
                body: "Data is sent to KindCaddy's backend and to OpenAI, LLC for AI processing. Weather lookups may use Apple Weather or Open-Meteo, using coordinates only as needed for course conditions."
            )
            consentRow(
                icon: "shield",
                title: "How it is protected",
                body: "KindCaddy does not sell personal data or use it for advertising. OpenAI API content is processed under OpenAI's API data protections and is not used to train OpenAI models."
            )
        }
        .padding(16)
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(Theme.border, lineWidth: 1)
        )
    }

    private func consentRow(icon: String, title: String, body: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .frame(width: 22)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 14, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.textPrimary)
                Text(body)
                    .font(.system(size: 13, weight: .regular, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var legalLinks: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Read more")
                .font(.system(size: 11, weight: .semibold, design: .serif))
                .tracking(1.2)
                .textCase(.uppercase)
                .foregroundStyle(Theme.textTertiary)
            HStack(spacing: 12) {
                legalButton("Privacy Policy", .privacy)
                legalButton("AI Disclaimer", .disclaimer)
            }
        }
    }

    private func legalButton(_ title: String, _ document: LegalDocument) -> some View {
        Button {
            showingLegalDocument = document
        } label: {
            Text(title)
                .font(.system(size: 13, weight: .semibold, design: .serif))
                .foregroundStyle(Theme.accent)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Theme.accent.opacity(0.12))
                .clipShape(Capsule())
        }
    }

    private var actionButtons: some View {
        VStack(spacing: 10) {
            Button {
                onAccept()
                dismiss()
            } label: {
                Text("Agree and Continue")
                    .font(Theme.headline)
                    .foregroundStyle(Theme.background)
                    .frame(maxWidth: .infinity)
                    .frame(height: Theme.minTouchHeight)
                    .background(Theme.accent)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }

            Button {
                onDecline()
                dismiss()
            } label: {
                Text("Not Now")
                    .font(.system(size: 14, weight: .semibold, design: .serif))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
            }
        }
        .padding(.top, 4)
    }
}

