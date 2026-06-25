import Foundation
import SwiftData

// MARK: - SwiftData models

@Model
final class CachedRound {
    @Attribute(.unique) var roundId: String
    var startedAt: Date
    var courseName: String?
    var lastSyncedAt: Date

    @Relationship(deleteRule: .cascade, inverse: \CachedRoundMessage.round)
    var messages: [CachedRoundMessage] = []

    init(roundId: String, startedAt: Date, courseName: String?, lastSyncedAt: Date) {
        self.roundId = roundId
        self.startedAt = startedAt
        self.courseName = courseName
        self.lastSyncedAt = lastSyncedAt
    }
}

@Model
final class CachedRoundMessage {
    var role: String
    var content: String
    var hole: Int?
    var createdAt: Date
    var sequence: Int
    var round: CachedRound?

    init(role: String, content: String, hole: Int?, createdAt: Date, sequence: Int) {
        self.role = role
        self.content = content
        self.hole = hole
        self.createdAt = createdAt
        self.sequence = sequence
    }
}

// MARK: - Cache facade

/// Local cache for chat history so the History → Round Detail screen can render
/// instantly (and keep working without network). Server data is the source of
/// truth — every successful network fetch upserts into the cache; on failure
/// we fall back to whatever was last cached.
@MainActor
final class ChatCache {
    static let shared = ChatCache()

    let container: ModelContainer

    private init() {
        let schema = Schema([CachedRound.self, CachedRoundMessage.self])
        do {
            self.container = try ModelContainer(for: schema)
        } catch {
            print("[ChatCache] persistent ModelContainer failed: \(error). Falling back to in-memory cache.")
            let cfg = ModelConfiguration(isStoredInMemoryOnly: true)
            do {
                self.container = try ModelContainer(for: schema, configurations: cfg)
            } catch {
                fatalError("[ChatCache] failed to create in-memory fallback container: \(error)")
            }
        }
    }

    private var context: ModelContext { container.mainContext }

    /// Replace the cached messages for ``detail.id`` with the server snapshot.
    /// Called after a successful ``GET /rounds/{id}`` so the cache always
    /// reflects the latest server state.
    func upsertRoundDetail(_ detail: RoundDetail) {
        let serverMessages = detail.messages ?? []
        let started = parseDate(detail.started_at) ?? Date()
        let cached = fetchOrCreate(roundId: detail.id, startedAt: started, courseName: detail.course_name)
        cached.startedAt = started
        cached.courseName = detail.course_name
        cached.lastSyncedAt = Date()

        for msg in cached.messages {
            context.delete(msg)
        }
        cached.messages.removeAll()

        for (idx, m) in serverMessages.enumerated() {
            let createdAt = parseDate(m.created_at) ?? Date()
            let cm = CachedRoundMessage(
                role: m.role,
                content: m.content,
                hole: m.hole,
                createdAt: createdAt,
                sequence: idx
            )
            context.insert(cm)
            cm.round = cached
            cached.messages.append(cm)
        }
        save()
    }

    /// Append one live caddy exchange (user prompt + assistant reply) to the
    /// cache so chat history is preserved even if the user kills the app
    /// before we get a chance to refetch the round detail.
    func appendLiveExchange(
        roundId: String,
        userText: String,
        assistantText: String,
        hole: Int?,
        roundStartedAt: Date? = nil
    ) {
        let cached = fetchOrCreate(
            roundId: roundId,
            startedAt: roundStartedAt ?? Date(),
            courseName: nil
        )
        let nextSeq = (cached.messages.map(\.sequence).max() ?? -1) + 1
        let now = Date()
        let userMsg = CachedRoundMessage(
            role: "user",
            content: userText,
            hole: hole,
            createdAt: now,
            sequence: nextSeq
        )
        let asstMsg = CachedRoundMessage(
            role: "assistant",
            content: assistantText,
            hole: hole,
            createdAt: now.addingTimeInterval(0.001),
            sequence: nextSeq + 1
        )
        context.insert(userMsg)
        context.insert(asstMsg)
        userMsg.round = cached
        asstMsg.round = cached
        cached.messages.append(contentsOf: [userMsg, asstMsg])
        cached.lastSyncedAt = now
        save()
    }

    /// Read-only view of cached messages for the round, in chronological order.
    /// Returns ``[]`` when no cache exists for ``roundId``.
    func messages(forRound roundId: String) -> [RoundMessage] {
        guard let cached = fetchRound(roundId: roundId) else { return [] }
        let sorted = cached.messages.sorted { $0.sequence < $1.sequence }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return sorted.map { msg in
            RoundMessage(
                role: msg.role,
                content: msg.content,
                hole: msg.hole,
                created_at: formatter.string(from: msg.createdAt)
            )
        }
    }

    /// Delete the cached round and all its messages. Called from delete flows.
    func deleteRound(roundId: String) {
        guard let cached = fetchRound(roundId: roundId) else { return }
        context.delete(cached)
        save()
    }

    /// Has any cached message ever been written for this round?
    func hasCache(forRound roundId: String) -> Bool {
        guard let cached = fetchRound(roundId: roundId) else { return false }
        return !cached.messages.isEmpty
    }

    // MARK: - Helpers

    private func fetchRound(roundId: String) -> CachedRound? {
        let predicate = #Predicate<CachedRound> { $0.roundId == roundId }
        let descriptor = FetchDescriptor<CachedRound>(predicate: predicate)
        return try? context.fetch(descriptor).first
    }

    private func fetchOrCreate(
        roundId: String,
        startedAt: Date,
        courseName: String?
    ) -> CachedRound {
        if let existing = fetchRound(roundId: roundId) {
            return existing
        }
        let new = CachedRound(
            roundId: roundId,
            startedAt: startedAt,
            courseName: courseName,
            lastSyncedAt: Date()
        )
        context.insert(new)
        return new
    }

    private func parseDate(_ iso: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: iso) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: iso)
    }

    private func save() {
        do {
            try context.save()
        } catch {
            print("[ChatCache] save failed: \(error)")
        }
    }
}
