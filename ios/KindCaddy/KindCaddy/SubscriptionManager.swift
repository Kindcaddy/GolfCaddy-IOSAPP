import Foundation
import StoreKit

@MainActor
final class SubscriptionManager: ObservableObject {
    static let monthlyProductID = "kindcaddy.pro.monthly"
    static let yearlyProductID = "kindcaddy.pro.yearly"
    static let productIDs: Set<String> = [monthlyProductID, yearlyProductID]

    /// Synthetic "all access" status returned when `Config.subscriptionsEnabled == false`.
    static let unlocked = SubscriptionStatusResponse(
        trial_round_starts: 0,
        trial_round_limit: 0,
        trial_rounds_remaining: 0,
        is_trial_available: false,
        profile_stats_allowed: true,
        can_start_round: true,
        subscription_status: "disabled",
        is_subscribed: true,
        product_id: nil,
        expires_at: nil,
        environment: nil
    )

    @Published private(set) var status: SubscriptionStatusResponse =
        Config.subscriptionsEnabled ? .empty : SubscriptionManager.unlocked
    @Published private(set) var products: [Product] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isLoadingProducts = false
    @Published private(set) var didLoadProducts = false
    @Published var errorMessage: String?

    private let api = APIClient.shared
    private var updatesTask: Task<Void, Never>?

    init() {
        updatesTask = Config.subscriptionsEnabled ? observeTransactions() : nil
    }

    deinit {
        updatesTask?.cancel()
    }

    func configure() async {
        await api.setBaseURL(Config.backendBaseURL)
        guard Config.subscriptionsEnabled else {
            status = Self.unlocked
            didLoadProducts = true
            return
        }
        await loadProducts()
        await refreshStatus()
        await syncCurrentEntitlements()
    }

    func reset() {
        status = Config.subscriptionsEnabled ? .empty : Self.unlocked
        errorMessage = nil
    }

    func refreshStatus() async {
        guard Config.subscriptionsEnabled else {
            status = Self.unlocked
            return
        }
        do {
            status = try await api.getSubscriptionStatus()
        } catch APIError.unauthorized {
            reset()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadProducts() async {
        guard Config.subscriptionsEnabled else {
            products = []
            didLoadProducts = true
            return
        }
        isLoadingProducts = true
        didLoadProducts = false
        errorMessage = nil
        defer {
            isLoadingProducts = false
            didLoadProducts = true
        }

        do {
            products = try await Product.products(for: Array(Self.productIDs))
                .sorted { lhs, rhs in
                    if lhs.id == Self.monthlyProductID { return true }
                    if rhs.id == Self.monthlyProductID { return false }
                    return lhs.displayPrice < rhs.displayPrice
                }
            if products.isEmpty {
                errorMessage = "Subscription options are temporarily unavailable. Please try again."
            }
        } catch {
            products = []
            errorMessage = "Unable to load subscription options."
        }
    }

    func ensureCanStartRound() async -> Bool {
        guard Config.subscriptionsEnabled else { return true }
        await refreshStatus()
        return status.can_start_round
    }

    func ensureProfileStatsAccess() async -> Bool {
        guard Config.subscriptionsEnabled else { return true }
        await refreshStatus()
        return status.profile_stats_allowed
    }

    @discardableResult
    func purchase(_ product: Product) async -> Bool {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            await track("purchase_started", productID: product.id)
            let result = try await product.purchase()
            switch result {
            case .success(let verification):
                let transaction = try checkVerified(verification)
                try await sync(verification.jwsRepresentation)
                await transaction.finish()
                await refreshStatus()
                await track("purchase_succeeded", productID: product.id)
                return status.is_subscribed
            case .userCancelled:
                return false
            case .pending:
                errorMessage = "Purchase is pending approval."
                return false
            @unknown default:
                errorMessage = "Purchase could not be completed."
                return false
            }
        } catch {
            errorMessage = error.localizedDescription
            await track("purchase_failed", productID: product.id)
            return false
        }
    }

    func restorePurchases() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            try await AppStore.sync()
            await syncCurrentEntitlements()
            await refreshStatus()
            await track("restore_succeeded")
        } catch {
            errorMessage = "Unable to restore purchases."
        }
    }

    private func syncCurrentEntitlements() async {
        for await result in Transaction.currentEntitlements {
            guard let transaction = try? checkVerified(result),
                  Self.productIDs.contains(transaction.productID) else {
                continue
            }
            try? await sync(result.jwsRepresentation)
        }
        await refreshStatus()
    }

    private func observeTransactions() -> Task<Void, Never> {
        Task { [weak self] in
            for await result in Transaction.updates {
                guard let self else { return }
                guard let transaction = try? self.checkVerified(result),
                      Self.productIDs.contains(transaction.productID) else {
                    continue
                }
                try? await self.sync(result.jwsRepresentation)
                await transaction.finish()
                await self.refreshStatus()
            }
        }
    }

    private func sync(_ signedTransactionInfo: String) async throws {
        let response = try await api.verifySubscription(signedTransactionInfo: signedTransactionInfo)
        status = response.status
    }

    private func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .verified(let value):
            return value
        case .unverified(_, let error):
            throw error
        }
    }

    private func track(_ name: String, productID: String? = nil) async {
        var properties: [String: String] = [:]
        if let productID {
            properties["product_id"] = productID
        }
        await api.trackEvent(name: name, properties: properties)
    }
}
