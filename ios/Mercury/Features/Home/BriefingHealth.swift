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

    /// Every label `degraded_sources` can carry, as the thing the listener
    /// lost.
    ///
    /// Two vocabularies land in one list, both named in the backend's
    /// `schemas.py`: `INGESTION_SOURCE_LABELS` and `REFUSABLE_SEGMENTS`.
    /// They are said the same way, because from the listener's side "Gmail
    /// would not answer" and "the model would not write about your inbox"
    /// cost the same thing — so `email` and `inbox` are one phrase, and
    /// saying it twice would be wrong rather than thorough.
    ///
    /// `news` and `weather` are NOT written today, and that is a backend
    /// decision rather than an oversight here: ingestion records only the
    /// personal sources, on the grounds that a dead feed costs a segment
    /// rule 7 already covers (see `_ingest_concurrently`). A morning whose
    /// every feed failed therefore shows no notice. The rows are kept so
    /// that the day the backend does record them, the phone says so in
    /// words rather than as "one other source".
    ///
    /// Ordered as `Segment` is — weather, calendar, inbox, world,
    /// follow-ups — so the sentence lists what is missing in the order it
    /// would have played.
    static let phrases: [(labels: Set<String>, phrase: String)] = [
        (["weather"], "the weather"),
        (["calendar"], "your calendar"),
        (["email", "inbox"], "your inbox"),
        (["news", "world"], "the news"),
        (["follow_ups"], "your follow-ups"),
    ]

    /// "Made without your calendar and your inbox.", or nil on a healthy
    /// morning.
    ///
    /// A label this build does not know still produces a sentence. A newer
    /// backend adding a source must not turn a degraded morning back into a
    /// silent one, which is the failure this exists to end; it just cannot
    /// be named.
    ///
    /// `narratorFallback` is task 22: the briefing was read by the backup
    /// voice because the configured one was unavailable. It arrives in its
    /// own manifest field rather than in `degraded`, because the backend
    /// retries everything in `degraded_sources` and a voice quota does not
    /// come back by morning. To the listener it is the same kind of fact,
    /// so it joins the same sentence rather than adding a second line.
    static func notice(for degraded: [String], narratorFallback: Bool = false) -> String? {
        switch (missing(degraded), narratorFallback) {
        case (nil, false):
            return nil
        case (nil, true):
            return "Read by the backup voice today."
        case (let lacking?, false):
            return "Made without \(lacking)."
        case (let lacking?, true):
            return "Made without \(lacking), and read by the backup voice."
        }
    }

    /// "your calendar and your inbox", or nil when nothing is missing.
    private static func missing(_ degraded: [String]) -> String? {
        let labels = Set(degraded.map { $0.lowercased() }.filter { !$0.isEmpty })
        guard !labels.isEmpty else { return nil }

        let named = phrases.filter { !$0.labels.isDisjoint(with: labels) }.map(\.phrase)
        let known = phrases.reduce(into: Set<String>()) { $0.formUnion($1.labels) }
        // Counted, not flagged: two unknown labels are two things missing,
        // and "one other source" for both would understate the morning in
        // exactly the direction this notice exists to stop.
        let unknown = labels.subtracting(known).count

        guard !named.isEmpty else { return "part of what you usually get" }
        let others: [String]
        switch unknown {
        case 0: others = []
        case 1: others = ["one other source"]
        default: others = ["\(unknown) other sources"]
        }
        return sentenceList(named + others)
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
