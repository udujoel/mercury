import Foundation

/// What arrived in the inbox this morning, as a thing you can look at.
///
/// **Why this file exists.** The backend has shipped `digest.newsletters`
/// since PR #17 — 44 entries in the live 2026-09-11 digest — and
/// `Digest.swift` decodes every field of it, including a bug fix and its own
/// regression tests. No view has ever read it. The data crosses the wire on
/// every launch and is dropped on the floor, which is this project's
/// signature failure wearing its politest clothes: nothing is broken, the
/// tests are green, and the feature does not exist.
///
/// Pure and separated from the view, like `BriefingDigest`, so the counting
/// can be checked without rendering anything.
enum NewsletterDesk {

    /// One sender, and everything they sent this morning.
    ///
    /// Grouped by sender rather than listed flat because 44 rows is not a
    /// section, it is a second inbox — and the useful question is "who wrote
    /// to me", not "what are the 44 subject lines".
    struct Sender: Identifiable, Equatable {
        /// The sender's display name, which is also the group key.
        let id: String
        let items: [DigestNewsletter]

        /// Whether anything this sender wrote reached the audio.
        var reachedTheBriefing: Bool { items.contains(where: \.inBriefing) }

        /// The most recent arrival, which is what the row is dated by.
        var latest: Date? { items.compactMap(\.receivedAt).max() }

        /// The stories this sender contributed to, deduplicated. A newsletter
        /// with none is not a broken row: `is_digest_story` deliberately
        /// keeps ordinary personal mail off the browse surface, and a
        /// newsletter that clustered with nothing browsable has nothing to
        /// open.
        var storyIds: [String] {
            var seen = Set<String>()
            return items.flatMap(\.storyIds).filter { seen.insert($0).inserted }
        }
    }

    /// The senders, most recent first, with the ones that reached the
    /// briefing lifted to the top.
    ///
    /// That ordering is the whole editorial claim of the section: the four
    /// newsletters that shaped what you just heard matter more than the
    /// forty that did not, and burying them in arrival order would make the
    /// list a chronology rather than an answer.
    static func senders(from newsletters: [DigestNewsletter]) -> [Sender] {
        var order: [String] = []
        var grouped: [String: [DigestNewsletter]] = [:]
        for item in newsletters {
            // Keyed on the display name because that is what the row shows.
            // Two senders sharing a display name is a collision the user
            // cannot see either, so merging them is the honest rendering.
            let key = item.sourceName
            if grouped[key] == nil { order.append(key) }
            grouped[key, default: []].append(item)
        }

        let senders = order.map { key in
            Sender(
                id: key,
                // Newest first WITHIN a sender too, so a daily that sent
                // twice shows today's above yesterday's.
                items: (grouped[key] ?? []).sorted {
                    ($0.receivedAt ?? .distantPast) > ($1.receivedAt ?? .distantPast)
                })
        }

        return senders.sorted { lhs, rhs in
            if lhs.reachedTheBriefing != rhs.reachedTheBriefing {
                return lhs.reachedTheBriefing
            }
            return (lhs.latest ?? .distantPast) > (rhs.latest ?? .distantPast)
        }
    }

    /// The one line above the list.
    ///
    /// Counted, never estimated — `BriefingDigest`'s rule, for the same
    /// reason. And it answers the question the section exists for: a lot
    /// arrived, and this much of it is in the briefing you just heard.
    ///
    /// The second clause is dropped rather than written as "0 reached the
    /// briefing", because a zero here is usually a quiet morning rather than
    /// a fault, and a sentence built to report a failure reads as one.
    static func summary(for newsletters: [DigestNewsletter]) -> String? {
        guard !newsletters.isEmpty else { return nil }
        let arrived = newsletters.count
        let noun = arrived == 1 ? "newsletter" : "newsletters"
        let covered = newsletters.filter(\.inBriefing).count
        guard covered > 0 else { return "\(arrived) \(noun) this morning." }
        return "\(arrived) \(noun) this morning — \(covered) in the briefing."
    }

    /// Rows shown before the list is truncated.
    ///
    /// Eleven senders is already a long scroll under a briefing, and the
    /// ordering above puts everything that reached the audio first, so the
    /// cut falls on the tail that did not. What is cut is counted out loud
    /// by `remainder`, because a cap that hides what it dropped is how
    /// "plus eleven others" became an entire segment.
    static let visibleSenders = 8

    static func remainder(_ senders: [Sender]) -> Int {
        max(0, senders.count - visibleSenders)
    }
}
