import SwiftUI

// MARK: - Legal Document Model

enum LegalDocument: String, CaseIterable, Identifiable {
    case privacy
    case terms
    case disclaimer

    var id: String { rawValue }

    var title: String {
        switch self {
        case .privacy: return "Privacy Policy"
        case .terms: return "Terms of Use"
        case .disclaimer: return "AI Caddy Disclaimer"
        }
    }

    /// Short one-liner used at the top of the sheet.
    var summary: String {
        switch self {
        case .privacy:
            return "What KindCaddy collects, how it is used, and the choices you have."
        case .terms:
            return "The rules for using KindCaddy, including subscription and AI advice terms."
        case .disclaimer:
            return "KindCaddy's advice is AI-generated and can be wrong. You are always responsible for your shot."
        }
    }

    /// Structured sections so the sheet can render proper headings instead of one large text blob.
    var sections: [LegalSection] {
        switch self {
        case .privacy: return Self.privacySections
        case .terms: return Self.termsSections
        case .disclaimer: return Self.disclaimerSections
        }
    }

    /// Preserved for any callers that want a plain-text blob (e.g. share sheets).
    var bodyText: String {
        sections.map { "\($0.heading.uppercased())\n\n\($0.body)" }.joined(separator: "\n\n")
    }

    /// Bumped whenever the substance of any document changes; surfaced at the bottom of the sheet.
    static let effectiveDate = "May 22, 2026"
    static let version = "v1.4"
}

struct LegalSection: Identifiable {
    let heading: String
    let body: String
    var id: String { heading }
}

// MARK: - Privacy

private extension LegalDocument {
    static let privacySections: [LegalSection] = [
        .init(
            heading: "Scope",
            body: """
            This Privacy Policy explains how KindCaddy LLC collects, uses, and protects information when you use the KindCaddy iOS app and backend services.
            """
        ),
        .init(
            heading: "Information We Collect",
            body: """
            • Account details — your name, email, and the identifier returned by Apple Sign-In or Google Sign-In.
            • Golfer profile — handicap, swing speed, shot shape, club carry distances, and preferences you enter.
            • Round data — course, tee, hole scores, and shot logs (club, outcome, and optional notes) that you record in the app.
            • Advice interactions — the text you ask the caddy, the contextual state at the time (hole, lie, wind, round state), and the advice returned.
            • Voice input — speech text and, for recorded voice features, short audio clips used to transcribe your question.
            • Device and diagnostic data — app version, iOS version, crash and error logs, and coarse usage events used to measure reliability and quality.
            • Location and weather context — used to provide course conditions. Coordinates may be sent to KindCaddy or weather providers only as needed to fetch weather and support the current request.
            • Purchase information — App Store subscription product, transaction status, and renewal or expiration dates needed to unlock paid features.
            """
        ),
        .init(
            heading: "How We Use It",
            body: """
            • To provide the core product: generate caddy advice, track your rounds, and compute personal insights (miss patterns, carry deltas, scoring trends).
            • To improve the product: aggregate analytics, debug crashes, and evaluate advice quality.
            • To secure your account and prevent abuse.
            • To manage subscriptions, trial access, support requests, and service notices.
            """
        ),
        .init(
            heading: "AI Data Sharing Consent",
            body: """
            KindCaddy asks for your permission in-app before sending personal data to OpenAI for AI processing. If you agree, KindCaddy may send your golfer profile, club distances, handicap, shot tendencies, physical profile fields you enter, round state, hole details, scores, shot logs, location/weather context, recent conversation, voice transcript, and your advice question to KindCaddy's backend and OpenAI. AirPods/headset audio recordings may be sent to OpenAI Whisper for transcription, and caddy replies may be sent to OpenAI text-to-speech to generate spoken audio. You can decline, but AI caddy advice, transcription, and spoken AI replies will not work without this permission.
            """
        ),
        .init(
            heading: "Third-Party Processors",
            body: """
            • OpenAI — advice, transcription, text-to-speech, embeddings, and style/memory requests may be sent to OpenAI's API after your in-app consent. Advice requests include your profile snapshot, round state, recent shots, weather context, memory snippets, recent conversation, and your message. Per OpenAI's API terms, API content is not used to train OpenAI models. Do not include sensitive personal information in your messages to the caddy.
            • Apple / Google — used only to authenticate you when you choose "Sign in with Apple" or "Sign in with Google." We receive the minimum profile information needed to create your account. Apple Speech Recognition may process microphone audio when you use phone mic dictation.
            • Hosting and weather — the backend runs on AWS EC2. Weather can be fetched from Apple WeatherKit on-device, or from Open-Meteo by the backend when WeatherKit is unavailable. Coordinates are used only as needed to fetch weather and support the current caddy request.
            KindCaddy requires third-party processors to provide the same or equal protection for personal data through their published data protection commitments, API terms, and contractual obligations.
            We do not sell your personal data, and we do not use it for advertising.
            """
        ),
        .init(
            heading: "Retention",
            body: """
            Account, profile, round, and subscription entitlement data are retained while your account is active. Advice interactions and diagnostic logs are retained only as long as reasonably needed for service operation, quality, security, and legal compliance. Backups and operational logs rotate on a limited schedule.
            """
        ),
        .init(
            heading: "Your Choices",
            body: """
            • You can request access to, correction of, or deletion of your data by emailing customersupport@kindcaddy.app.
            • You can sign out at any time from the Profile screen.
            • You can delete your account from the Profile screen. Deletion removes your account, profile, rounds, notes, insights, and device tokens from KindCaddy's active systems. App Store subscriptions must be canceled through your Apple account.
            """
        ),
        .init(
            heading: "Children",
            body: """
            KindCaddy is not directed to children under 13 and we do not knowingly collect data from them. We also do not knowingly sell or share personal information of minors under 16. If you believe a child has provided us information, contact customersupport@kindcaddy.app so we can remove it.
            """
        ),
        .init(
            heading: "California Residents",
            body: """
            If you reside in California, you have rights under the California Consumer Privacy Act, as amended by the CPRA, including:
            • The right to know what personal information we collect, the sources, purposes, and categories of recipients.
            • The right to request deletion or correction of your personal information.
            • The right to opt out of the sale or sharing of your personal information.
            • The right to limit the use of your sensitive personal information.
            • The right not to be discriminated against for exercising these rights.
            We do not sell personal information, and we do not share personal information for cross-context behavioral advertising as those terms are defined under the CCPA/CPRA. We do not use sensitive personal information for purposes beyond providing, securing, and improving the service. Voice input is used only to process caddy requests. To exercise a California privacy right, email customersupport@kindcaddy.app from the address on your account; we will respond within 45 days, with one 45-day extension where reasonably necessary. We will not discriminate against you for exercising these rights.
            """
        ),
        .init(
            heading: "Contact",
            body: """
            Questions, requests, or privacy concerns: customersupport@kindcaddy.app.
            """
        ),
    ]
}

// MARK: - Terms

private extension LegalDocument {
    static let termsSections: [LegalSection] = [
        .init(
            heading: "Service",
            body: """
            KindCaddy provides AI-generated golf caddy advice, round tracking, and personal golf insights. The service is provided "as is" and "as available," without warranties of any kind. Do not rely on it for critical decisions, tournament play, wagering, or any situation where an incorrect answer could cause harm.
            """
        ),
        .init(
            heading: "Eligibility",
            body: """
            You must be at least 13 years old to use KindCaddy, and old enough under the law of your jurisdiction to enter into this agreement. You confirm that the information you provide (profile, handicap, etc.) is accurate to the best of your knowledge.
            """
        ),
        .init(
            heading: "Your Account",
            body: """
            You are responsible for safeguarding your sign-in credentials and for all activity under your account. Notify us at customersupport@kindcaddy.app if you believe your account has been compromised.
            """
        ),
        .init(
            heading: "Acceptable Use",
            body: """
            You agree not to:
            • Use the service to violate any law or any third party's rights.
            • Reverse engineer, scrape, or attempt to extract model prompts or training data.
            • Upload content that is unlawful, harassing, or that you do not have rights to share.
            • Interfere with or disrupt the service, probe it for vulnerabilities without authorization, or circumvent rate limits and authentication.
            """
        ),
        .init(
            heading: "AI-Generated Content",
            body: """
            Caddy advice is produced by a third-party large language model. Output can be inaccurate, inconsistent, or hallucinated. You are responsible for validating any recommendation before acting on it. See the AI Caddy Disclaimer for more detail.
            """
        ),
        .init(
            heading: "Subscriptions",
            body: """
            KindCaddy may offer auto-renewable subscriptions, including KindCaddy Pro Monthly and KindCaddy Pro Yearly. Subscription prices and periods are shown in the app before purchase. Payment is charged to your Apple ID at confirmation of purchase. Subscriptions automatically renew unless canceled at least 24 hours before the end of the current period, and your account may be charged for renewal within 24 hours before the period ends. You can manage or cancel subscriptions in your App Store account settings. Deleting your KindCaddy account does not cancel an active App Store subscription.
            """
        ),
        .init(
            heading: "Intellectual Property",
            body: """
            The KindCaddy app, branding, and backend are the property of KindCaddy LLC. You retain rights to the data you enter (profile, scores, shot logs). By using the service, you grant us a limited license to process that data to operate and improve KindCaddy as described in the Privacy Policy.
            """
        ),
        .init(
            heading: "Termination",
            body: """
            We may suspend or terminate access at any time, with or without notice, including for misuse, security risk, or violation of these terms. You may stop using the service and delete your account at any time.
            """
        ),
        .init(
            heading: "Disclaimer & Limitation of Liability",
            body: """
            To the fullest extent permitted by law, KindCaddy disclaims all warranties, express or implied, including merchantability, fitness for a particular purpose, and non-infringement. We are not liable for any indirect, incidental, consequential, or punitive damages, or for lost profits, lost data, or missed shots arising from use of the service. Our total liability for any claim will not exceed USD 50.
            """
        ),
        .init(
            heading: "Changes",
            body: """
            We may update these terms from time to time. Material changes will be surfaced in-app or through another reasonable notice. Continued use after an update constitutes acceptance.
            """
        ),
        .init(
            heading: "Governing Law and Venue",
            body: """
            These terms are governed by the laws of the State of Delaware, excluding its conflict-of-laws rules. Any dispute arising out of or relating to these terms or the service will be resolved exclusively in the state or federal courts located in Delaware, and you and KindCaddy LLC consent to personal jurisdiction and venue in those courts. If you are a California resident, nothing in this section limits any non-waivable right or remedy available to you under California law.
            """
        ),
        .init(
            heading: "Feedback",
            body: """
            If you send us feedback, suggestions, or bug reports, you grant KindCaddy LLC a perpetual, irrevocable, royalty-free, worldwide license to use that feedback to operate, improve, and promote the service, without obligation to you.
            """
        ),
        .init(
            heading: "Contact",
            body: """
            Questions about these terms: customersupport@kindcaddy.app.
            """
        ),
    ]
}

// MARK: - Disclaimer

private extension LegalDocument {
    static let disclaimerSections: [LegalSection] = [
        .init(
            heading: "AI Advice Is Not Professional Instruction",
            body: """
            KindCaddy's recommendations are generated by a large language model (GPT-4o) using the context you provide. It is not a PGA-certified instructor, a rules official, or a substitute for professional medical, fitness, or legal advice.
            """
        ),
        .init(
            heading: "What Can Be Wrong",
            body: """
            Advice quality depends on inputs and the model itself. Distances, club selections, wind and altitude adjustments, shot-shape recommendations, and strategic calls can be incorrect because of:
            • Inaccurate or outdated profile data (carry distances, handicap, swing tendencies).
            • Wrong or stale weather data, including wind direction, gusts, temperature, or pressure.
            • Model limitations — large language models can confidently state wrong facts, misread lies, or misinterpret context.
            • Connectivity or API errors, including degraded or truncated responses.
            • Mis-transcribed voice input from speech recognition.
            """
        ),
        .init(
            heading: "Your Responsibility",
            body: """
            You alone decide which club to hit, where to aim, and whether to attempt any shot. Always:
            • Visually verify yardages with a rangefinder, GPS, or course markers before playing a shot.
            • Follow the Rules of Golf and the rules of your course.
            • Use your own judgment for safety — weather, terrain, other players, wildlife, and physical condition.
            • Ignore any advice that conflicts with posted rules, safety guidance, or common sense.
            """
        ),
        .init(
            heading: "No Guarantees",
            body: """
            KindCaddy does not guarantee any score improvement, shot outcome, or that the service will be available, accurate, or free of errors.
            """
        ),
        .init(
            heading: "Safety",
            body: """
            Do not use the app in ways that distract you from your surroundings. Do not rely on it during lightning, severe weather, or any condition where continued play is unsafe.
            """
        ),
        .init(
            heading: "Acknowledgement",
            body: """
            By continuing, you acknowledge that KindCaddy's advice is AI-generated, may be incorrect, and that you take full responsibility for your decisions on the course.
            """
        ),
    ]
}

// MARK: - Support Links

enum SupportLinks {
    static let privacyURL = URL(string: "https://kindcaddy.app/privacy")
    static let termsURL = URL(string: "https://kindcaddy.app/terms")
    static let disclaimerURL = URL(string: "https://kindcaddy.app/disclaimer")
    static let supportEmail = "customersupport@kindcaddy.app"

    static func feedbackURL(
        userId: String?,
        sessionId: String?,
        roundId: String?
    ) -> URL? {
        let subject = "KindCaddy feedback"
        let body = """
        Device: iOS
        User ID: \(userId ?? "unknown")
        Session ID: \(sessionId ?? "none")
        Round ID: \(roundId ?? "none")

        What happened:
        """
        let encodedSubject = subject.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? subject
        let encodedBody = body.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? body
        return URL(string: "mailto:\(supportEmail)?subject=\(encodedSubject)&body=\(encodedBody)")
    }
}

// MARK: - Sheet

struct LegalDocumentSheet: View {
    let document: LegalDocument
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    summaryBanner

                    ForEach(document.sections) { section in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(section.heading)
                                .font(.system(size: 15, weight: .semibold, design: .serif))
                                .foregroundStyle(Theme.accent)
                                .textCase(.uppercase)
                                .tracking(0.5)
                            Text(section.body)
                                .font(.system(size: 15, weight: .regular, design: .serif))
                                .foregroundStyle(Theme.textPrimary)
                                .lineSpacing(5)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    footer
                }
                .padding(20)
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle(document.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(Theme.accent)
                }
            }
        }
    }

    private var summaryBanner: some View {
        Text(document.summary)
            .font(.system(size: 14, weight: .regular, design: .serif))
            .foregroundStyle(Theme.textSecondary)
            .lineSpacing(4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(Theme.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(Theme.border, lineWidth: 1)
            )
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Effective \(LegalDocument.effectiveDate) · \(LegalDocument.version)")
                .font(.caption)
                .foregroundStyle(Theme.textTertiary)
            Text("Questions? \(SupportLinks.supportEmail)")
                .font(.caption)
                .foregroundStyle(Theme.textTertiary)
        }
        .padding(.top, 12)
    }
}

#Preview("Privacy") {
    LegalDocumentSheet(document: .privacy)
}

#Preview("Terms") {
    LegalDocumentSheet(document: .terms)
}

#Preview("Disclaimer") {
    LegalDocumentSheet(document: .disclaimer)
}
