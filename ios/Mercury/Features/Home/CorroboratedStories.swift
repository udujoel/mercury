import Foundation

/// What more than one outlet covered this morning.
///
/// Task 17 asks for a topic-led section: what subjects arrived, and which
/// senders covered each. The obvious material is `DigestStory.topics`, and it
/// is the wrong material — those are single lowercase words, so `ai` spans 16
/// stories and a heading built from one answers nothing (docs/LOG.md,
/// 2026-09-12).
///
/// **A cluster already IS a subject, with its coverage attached.** "OpenAI
/// Agents API Now in Public Beta" carries TLDR, TLDR AI and TLDR DevOps in
/// its own `sources`. That is the question task 17 asks, answerable today
/// with no prompt change, no schema change, and no re-run of anything.
///
/// The honest cost is length: with `source_count` counting distinct outlets,
/// about 15 of 209 stories qualify on a live morning. Short — and every row
/// of it is true, which the alternative could not claim until the extraction
/// is rewritten.
enum CorroboratedStories {

    /// A story and the distinct outlets that covered it.
    struct Item: Identifiable, Equatable {
        let story: DigestStory
        /// Distinct publication names, in the order the backend ranked them
        /// (`authority_sorted`), so the most authoritative reads first.
        let outlets: [String]

        var id: String { story.id }
        var outletCount: Int { outlets.count }
    }

    /// Two distinct outlets, not two cluster members.
    ///
    /// The difference was a real bug: `source_count` was `len(members)`, so
    /// a survey company that emailed four times was the highest-scoring
    /// story in the 2026-09-11 digest. Counting here rather than trusting
    /// `sourceCount` keeps this section honest even against a manifest
    /// generated before that fix — every briefing already in R2 carries the
    /// old inflated number, and this screen must not repeat it.
    static let minimumOutlets = 2

    static func items(from stories: [DigestStory]) -> [Item] {
        merged(stories)
            .map { Item(story: $0.story, outlets: $0.outlets) }
            .filter { $0.outletCount >= minimumOutlets }
            .sorted {
                // Most-corroborated first; ties broken by what reached the
                // audio, because a story the hosts discussed is one the
                // listener has context for.
                if $0.outletCount != $1.outletCount {
                    return $0.outletCount > $1.outletCount
                }
                return $0.story.coveredInAudio && !$1.story.coveredInAudio
            }
    }

    /// Stories that share a `subject` are ONE subject, with their outlets
    /// pooled.
    ///
    /// The live digest had "Houthi Advance Threatens Global Trade" (CNBC,
    /// the BBC, the New York Times) and "Houthi Advance Consolidates Red Sea
    /// Power" (Al Jazeera, the BBC) as separate clusters. They are the same
    /// story, and listing them twice makes the section a list of headlines
    /// again — which is what it exists to stop being. Pooled, that is one
    /// subject carried by four outlets.
    ///
    /// Clustering could not do this: those are genuinely different articles
    /// about different developments. The SUBJECT is what says they concern
    /// the same thing, which is why it had to come from the extraction
    /// rather than from similarity.
    ///
    /// Stories with no subject — every digest written before 2026-09-13,
    /// and roundups since — stand alone rather than being merged into one
    /// giant nil group.
    private static func merged(
        _ stories: [DigestStory]
    ) -> [(story: DigestStory, outlets: [String])] {
        var order: [String] = []
        var groups: [String: (DigestStory, [String])] = [:]

        for story in stories {
            let outlets = distinctOutlets(of: story)
            // Nil and empty subjects get a key of their own, so they never
            // pool with each other.
            guard let subject = story.subject, !subject.isEmpty else {
                order.append(story.id)
                groups[story.id] = (story, outlets)
                continue
            }
            if var existing = groups[subject] {
                // Keep the story with the widest coverage as the one shown;
                // its headline is the better-corroborated telling.
                if outlets.count > existing.1.count { existing.0 = story }
                for outlet in outlets where !existing.1.contains(outlet) {
                    existing.1.append(outlet)
                }
                groups[subject] = existing
            } else {
                order.append(subject)
                groups[subject] = (story, outlets)
            }
        }
        return order.compactMap { groups[$0] }
    }

    /// Distinct names, first-seen order preserved.
    ///
    /// `Set` alone would scramble `authority_sorted`'s ranking, which is the
    /// backend's judgement about which outlet to name first — and "the BBC,
    /// CNBC" reading as "CNBC, the BBC" on alternate launches would look
    /// like a bug even though nothing changed.
    static func distinctOutlets(of story: DigestStory) -> [String] {
        var seen = Set<String>()
        return story.sources
            .map(\.sourceName)
            .filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    /// "CNBC, the BBC and the New York Times" — named, not counted.
    ///
    /// A count is the weaker statement. "3 sources" asks the reader to trust
    /// the number; naming them lets the reader judge it, which matters most
    /// exactly where corroboration is the point. Rule 3's spirit on a page.
    static func attribution(_ outlets: [String]) -> String {
        switch outlets.count {
        case 0: return ""
        case 1: return outlets[0]
        case 2: return "\(outlets[0]) and \(outlets[1])"
        default:
            return outlets.dropLast().joined(separator: ", ")
                + " and \(outlets[outlets.count - 1])"
        }
    }

    /// The line above the section.
    static func summary(_ items: [Item]) -> String? {
        guard !items.isEmpty else { return nil }
        let noun = items.count == 1 ? "story" : "stories"
        return "\(items.count) \(noun) more than one outlet covered."
    }
}
