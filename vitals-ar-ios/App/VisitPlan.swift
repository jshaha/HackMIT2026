import Foundation
import Combine

/// What the clinician wants to cover this visit, and how much appointment time is left.
final class VisitPlan: ObservableObject {
    struct Topic: Identifiable, Equatable {
        let id = UUID()
        var title: String
    }

    @Published private(set) var topics: [Topic]
    @Published private(set) var coveredCount = 0
    @Published private(set) var lastCovered: (topic: Topic, index: Int, at: Date)?
    @Published var minutes: Double { didSet { defaults.set(minutes, forKey: Keys.minutes) } }
    @Published private(set) var start = Date()
    @Published private(set) var pausedAt: Date?

    private let defaults = UserDefaults.standard
    private enum Keys { static let topics = "visitTopics", minutes = "visitMinutes" }

    static let defaultTopics = [
        "Sleep and energy levels",
        "Side effects of new medication",
        "Blood pressure follow-up",
        "Exercise and diet",
        "Mood check-in",
        "Order follow-up labs",
    ]

    init() {
        let saved = defaults.stringArray(forKey: Keys.topics) ?? Self.defaultTopics
        topics = saved.map { Topic(title: $0) }
        let m = defaults.double(forKey: Keys.minutes)
        minutes = m > 0 ? m : 15
    }

    var totalCount: Int { topics.count + coveredCount }

    /// Topics as editable text, one per line (Settings). Saving resets the checklist.
    var topicsText: String {
        get { (topics.map(\.title)).joined(separator: "\n") }
        set {
            let lines = newValue.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            defaults.set(lines, forKey: Keys.topics)
            topics = lines.map { Topic(title: $0) }
            coveredCount = 0
            lastCovered = nil
        }
    }

    /// Adopt an agenda from the backend when it changes (resets the checklist for the new list).
    func syncAgenda(_ items: [String]) {
        guard items != syncedAgenda else { return }
        syncedAgenda = items
        topicsText = items.joined(separator: "\n")
    }
    private var syncedAgenda: [String]?

    func cover(_ topic: Topic) {
        guard let i = topics.firstIndex(of: topic) else { return }
        topics.remove(at: i)
        coveredCount += 1
        lastCovered = (topic, i, Date())
    }

    func undo() {
        guard let last = lastCovered else { return }
        topics.insert(last.topic, at: min(last.index, topics.count))
        coveredCount -= 1
        lastCovered = nil
    }

    // MARK: Appointment clock

    func remaining(at now: Date) -> TimeInterval {
        minutes * 60 - (pausedAt ?? now).timeIntervalSince(start)
    }

    var progress: Double { 1 - max(0, remaining(at: Date())) / (minutes * 60) }

    func togglePause() {
        if let p = pausedAt {
            start = start.addingTimeInterval(Date().timeIntervalSince(p))
            pausedAt = nil
        } else {
            pausedAt = Date()
        }
    }

    /// Fresh visit: restart the clock and restore the saved topic list.
    func restart() {
        start = Date()
        pausedAt = nil
        let saved = defaults.stringArray(forKey: Keys.topics) ?? Self.defaultTopics
        topics = saved.map { Topic(title: $0) }
        coveredCount = 0
        lastCovered = nil
    }
}
