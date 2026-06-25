import StoreKit
import SwiftUI

struct PaywallView: View {
    @EnvironmentObject var subscriptionManager: SubscriptionManager
    @Environment(\.dismiss) private var dismiss

    @State private var selectedProductID: String?
    @State private var selectedLegalDocument: LegalDocument?

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 24) {
                        header
                        trialStatus
                        productList
                        actionButtons
                        legalLinks
                    }
                    .padding()
                }
            }
            .navigationTitle("KindCaddy Pro")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                        .foregroundStyle(Theme.accent)
                }
            }
            .task {
                await subscriptionManager.configure()
                if selectedProductID == nil || selectedProduct == nil {
                    selectedProductID = subscriptionManager.products.first?.id
                }
                await APIClient.shared.trackEvent(
                    name: "paywall_viewed",
                    properties: [
                        "trial_rounds_remaining": "\(subscriptionManager.status.trial_rounds_remaining)",
                        "subscription_status": subscriptionManager.status.subscription_status
                    ]
                )
            }
            .onChange(of: subscriptionManager.status.is_subscribed) { _, isSubscribed in
                if isSubscribed {
                    dismiss()
                }
            }
            .onChange(of: subscriptionManager.products.map(\.id)) { _, _ in
                if selectedProductID == nil || selectedProduct == nil {
                    selectedProductID = subscriptionManager.products.first?.id
                }
            }
            .sheet(item: $selectedLegalDocument) { document in
                LegalDocumentSheet(document: document)
            }
        }
    }

    private var header: some View {
        VStack(spacing: 12) {
            Image("KCLogo")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(height: 76)

            Text("Keep your AI caddy on the bag")
                .font(Theme.serifFont(26, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .multilineTextAlignment(.center)

            Text(paywallSubtitle)
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
        }
        .padding(.top, 12)
    }

    private var trialStatus: some View {
        HStack(spacing: 12) {
            Image(systemName: subscriptionManager.status.is_subscribed ? "checkmark.seal.fill" : "flag.checkered")
                .font(.system(size: 22))
                .foregroundStyle(Theme.accent)

            VStack(alignment: .leading, spacing: 3) {
                Text(subscriptionManager.status.is_subscribed ? "Subscription active" : "Trial completed rounds")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
                Text(trialStatusText)
                    .font(Theme.headline)
                    .foregroundStyle(Theme.textPrimary)
            }
            Spacer()
        }
        .padding(16)
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private var productList: some View {
        VStack(spacing: 12) {
            if subscriptionManager.isLoadingProducts {
                ProgressView()
                    .tint(Theme.accent)
                    .frame(maxWidth: .infinity)
                    .padding(24)
                    .background(Theme.cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
            } else if subscriptionManager.products.isEmpty && subscriptionManager.didLoadProducts {
                unavailableProductsCard
            } else {
                ForEach(subscriptionManager.products, id: \.id) { product in
                    productCard(product)
                }
            }
        }
    }

    private var unavailableProductsCard: some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 24))
                .foregroundStyle(Theme.accent)
            Text("Subscription options are unavailable")
                .font(Theme.headline)
                .foregroundStyle(Theme.textPrimary)
            Text("Check your connection and try again. If this continues, restore purchases or contact support.")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
            Button("Try Again") {
                Task {
                    await subscriptionManager.loadProducts()
                    selectedProductID = subscriptionManager.products.first?.id
                }
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Theme.accent)
        }
        .frame(maxWidth: .infinity)
        .padding(18)
        .background(Theme.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(Theme.border, lineWidth: 1)
        )
    }

    private func productCard(_ product: Product) -> some View {
        let isSelected = selectedProductID == product.id
        return Button {
            selectedProductID = product.id
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(productTitle(product))
                        .font(Theme.headline)
                        .foregroundStyle(Theme.textPrimary)
                    Text(productSubtitle(product))
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text(product.displayPrice)
                        .font(.headline)
                        .foregroundStyle(Theme.accent)
                    Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(isSelected ? Theme.accent : Theme.textTertiary)
                }
            }
            .padding(16)
            .background(Theme.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .strokeBorder(isSelected ? Theme.accent : Theme.border, lineWidth: isSelected ? 1.5 : 1)
            )
        }
        .buttonStyle(.plain)
    }

    private var actionButtons: some View {
        VStack(spacing: 12) {
            Button {
                Task { await purchaseSelectedProduct() }
            } label: {
                HStack {
                    Spacer()
                    if subscriptionManager.isLoading {
                        ProgressView().tint(Theme.background)
                    } else {
                        Text("Continue")
                            .font(Theme.headline)
                            .foregroundStyle(Theme.background)
                    }
                    Spacer()
                }
                .frame(height: Theme.minTouchHeight)
                .background(Theme.accent)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(subscriptionManager.isLoading || selectedProduct == nil)

            Button("Restore Purchases") {
                Task { await subscriptionManager.restorePurchases() }
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Theme.accent)

            if let error = subscriptionManager.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(Theme.error)
                    .multilineTextAlignment(.center)
            }

            Text("Payment will be charged to your Apple ID. Subscriptions auto-renew unless canceled at least 24 hours before the end of the current period. You can manage or cancel anytime in App Store account settings.")
                .font(.caption2)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .lineSpacing(2)
                .padding(.top, 2)
        }
    }

    private var legalLinks: some View {
        HStack(spacing: 16) {
            Button("Terms") { selectedLegalDocument = .terms }
            Button("Privacy") { selectedLegalDocument = .privacy }
        }
        .font(.caption)
        .foregroundStyle(Theme.textSecondary)
        .padding(.bottom, 10)
    }

    private var selectedProduct: Product? {
        subscriptionManager.products.first { $0.id == selectedProductID }
    }

    private var paywallSubtitle: String {
        let monthly = subscriptionManager.products.first { $0.id == SubscriptionManager.monthlyProductID }
        let yearly = subscriptionManager.products.first { $0.id == SubscriptionManager.yearlyProductID }
        if let monthly, let yearly {
            return "Your free trial includes 5 completed rounds. Then choose Monthly at \(monthly.displayPrice) or Yearly at \(yearly.displayPrice) to keep starting rounds and using Profile & Stats."
        }
        return "Your free trial includes 5 completed rounds. Choose a plan to keep starting rounds and using Profile & Stats."
    }

    private var trialStatusText: String {
        if subscriptionManager.status.is_subscribed {
            return "You have full access."
        }
        let remaining = subscriptionManager.status.trial_rounds_remaining
        if remaining == 0 {
            return "Trial ended. Choose a plan to continue."
        }
        return "\(remaining) of \(subscriptionManager.status.trial_round_limit) completed rounds remaining"
    }

    private func productTitle(_ product: Product) -> String {
        switch product.id {
        case SubscriptionManager.monthlyProductID:
            return "Monthly"
        case SubscriptionManager.yearlyProductID:
            return "Yearly"
        default:
            return product.displayName
        }
    }

    private func productSubtitle(_ product: Product) -> String {
        switch product.id {
        case SubscriptionManager.monthlyProductID:
            return "\(product.displayPrice) per month"
        case SubscriptionManager.yearlyProductID:
            return "\(product.displayPrice) per year"
        default:
            return product.description
        }
    }

    private func purchaseSelectedProduct() async {
        guard let selectedProduct else { return }
        await subscriptionManager.purchase(selectedProduct)
    }
}
