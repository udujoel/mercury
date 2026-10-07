import Foundation

/// The one sentence Home owes you when a briefing is not the one you
/// configured.
///
/// `degraded_sources` is the manifest's channel for "this morning is missing
/// something", and until task 33 the phone dropped it on the floor. Three
/// mornings in September shipped with no inbox and no calendar — most of the
/// point of Mercury — and the card under the play button looked exactly as it
/// does on a good day.
///
/// One quiet line rather than a banner or an alert. Rule 7 says the briefing
/// still ships, and it did: what is on offer is still worth playing, and the
/// job here is to say what it lacks so nobody spends seven minutes waiting
/// for an inbox segment that is not coming. Not an error, not red.
enum BriefingHealth {

    /// Every label the backend writes, as the thing the listener lost.
    ///
    /// Two vocabularies land in one list: SOURCE labels from ingestion and
    /// SEGMENT names the refusal ladder gave up. Both are said the same way,
    /// because from the listener's side "Gmail would not answer" and "the
    /// model would not write about your inbox" cost the same thing. So
    /// `email` and `inbox` are one phrase, and saying it twice would be
    /// wrong rather than thorough.
    ///
    /// Ordered as the briefing is — inbox, day, news — so the sentence reads
    /// in the order the missing segments would have played.
    static let phrases: [(labels: Set<String>, phrase: String)] = [
        (["email", "inbox"], "your inbox"),
        (["calendar"], "your calendar"),
        (["follow_ups"], "your follow-ups"),
        (["news", "world"], "the news"),
        (["weather"], "the weather"),
    ]

    /// "Made without your inbox and your calendar.", or nil on a healthy
    /// morning.
    ///
    /// A label this build does not know still produces a sentence. A newer
    /// backend adding a source must not turn a degraded morning back into a
    /// silent one, which is the failure this exists to end; it just cannot
    /// be named.
    static func notice(for degraded: [String]) -> String? {
        let labels = Set(degraded.map { $0.lowercased() }.filter { !$0.isEmpty })
        guard !labels.isEmpty else { return nil }

        let named = phrases.filter { !$0.labels.isDisjoint(with: labels) }.map(\.phrase)
        let known = phrases.reduce(into: Set<String>()) { $0.formUnion($1.labels) }
        let unknown = !labels.isSubset(of: known)

        guard !named.isEmpty else {
            return "Made without part of what you usually get."
        }
        let list = named + (unknown ? ["one other source"] : [])
        return "Made without \(sentenceList(list))."
    }

    /// "a", "a and b", "a, b and c".
    private static func sentenceList(_ items: [String]) -> String {
        switch items.count {
        case 0: return ""
        case 1: return items[0]
        default:
            return items.dropLast().joined(separator: ", ") + " and \(items[items.count - 1])"
        }
    }
}
