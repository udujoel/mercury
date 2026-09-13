import XCTest
@testable import Mercury

/// The newsletters that arrived, which nothing rendered for five months.
///
/// The backend has shipped `digest.newsletters` since PR #17 — 44 entries in
/// the live 2026-09-11 digest — and `Digest.swift` decodes every field of it,
/// including a bug fix and its own regression tests. No view read it. This
/// project's signature failure in its politest form: nothing broken, tests
/// green, feature absent.
final class NewsletterDeskTests: XCTestCase {

    /// Built through the real decoder rather than a memberwise init, so
    /// these tests exercise the same path production does — and so a wire
    /// field being renamed shows up here rather than only in the app.
    ///
    /// Serialised by `JSONSerialization` rather than by string interpolation.
    /// The first cut hand-rolled the JSON and force-tried the decode, which
    /// review rightly flagged: a sender name or subject containing a quote
    /// would produce malformed JSON and an opaque crash with no line number.
    /// Reporting that failure better was the suggested fix; not being able
    /// to produce it is a better one, so the escaping is the library's job
    /// now and the decode cannot fail on input this builds.
    private func newsletter(
        _ sender: String, _ subject: String,
        at hour: Int = 9, inBriefing: Bool = false, storyIds: [String] = [],
        dated: Bool = true
    ) -> DigestNewsletter {
        var fields: [String: Any] = [
            "id": "\(sender)-\(subject)",
            "source_name": sender,
            "sender": "\(sender.lowercased())@example.com",
            "subject": subject,
            "story_ids": storyIds,
            "in_briefing": inBriefing,
        ]
        if dated {
            fields["received_at"] = "2026-09-11T\(String(format: "%02d", hour)):00:00Z"
        }
        let data = try! JSONSerialization.data(withJSONObject: fields)
        return try! JSONDecoder.mercury().decode(DigestNewsletter.self, from: data)
    }

    // MARK: - Grouping

    func testOneSenderWithFourNewslettersIsNamedOnce() {
        let senders = NewsletterDesk.senders(from: [
            newsletter("TLDR", "Issue 1", at: 6),
            newsletter("TLDR", "Issue 2", at: 7),
            newsletter("TLDR", "Issue 3", at: 8),
            newsletter("Stratechery", "The interview", at: 9),
        ])
        XCTAssertEqual(senders.count, 2)
        XCTAssertEqual(senders.first(where: { $0.id == "TLDR" })?.items.count, 3)
    }

    /// The editorial claim of the whole section: of everything that arrived,
    /// this is what you actually heard.
    func testWhatReachedTheBriefingIsListedFirst() {
        let senders = NewsletterDesk.senders(from: [
            newsletter("Noise", "Ad", at: 11),
            newsletter("Stratechery", "The interview", at: 6, inBriefing: true),
        ])
        XCTAssertEqual(senders.first?.id, "Stratechery",
                       "arrival order would have buried it under the ad")
    }

    func testWithinOneSenderTheNewestIssueIsFirst() {
        let senders = NewsletterDesk.senders(from: [
            newsletter("TLDR", "Older", at: 6),
            newsletter("TLDR", "Newer", at: 10),
        ])
        XCTAssertEqual(senders.first?.items.first?.subject, "Newer")
    }

    func testSendersWithNothingInTheBriefingFallBackToMostRecent() {
        let senders = NewsletterDesk.senders(from: [
            newsletter("Early", "a", at: 5),
            newsletter("Late", "b", at: 20),
        ])
        XCTAssertEqual(senders.map(\.id), ["Late", "Early"])
    }

    /// `receivedAt` is optional on the wire, and a nil must not crash the
    /// sort or jump the queue.
    func testAMissingTimestampSortsLastRatherThanCrashing() {
        let undated = newsletter("Undated", "s", dated: false)
        let senders = NewsletterDesk.senders(from: [undated, newsletter("Dated", "s")])
        XCTAssertEqual(senders.map(\.id), ["Dated", "Undated"])
    }

    // MARK: - Story join

    func testStoryIdsAreDeduplicatedAcrossASendersIssues() {
        let senders = NewsletterDesk.senders(from: [
            newsletter("TLDR", "One", storyIds: ["st_a", "st_b"]),
            newsletter("TLDR", "Two", storyIds: ["st_b", "st_c"]),
        ])
        XCTAssertEqual(senders.first?.storyIds, ["st_a", "st_b", "st_c"])
    }

    /// Not a broken row. `is_digest_story` deliberately keeps ordinary mail
    /// off the browse surface, so a newsletter with nowhere to go is an
    /// ordinary state and the row is text rather than a dead control.
    func testASenderWithNoStoriesHasNothingToOpen() {
        let senders = NewsletterDesk.senders(from: [newsletter("Quiet", "s")])
        XCTAssertTrue(senders.first?.storyIds.isEmpty == true)
    }

    // MARK: - The summary line

    func testTheSummaryCountsWhatArrivedAndWhatWasHeard() {
        let summary = NewsletterDesk.summary(for: [
            newsletter("A", "1", inBriefing: true),
            newsletter("B", "2"),
            newsletter("C", "3"),
        ])
        XCTAssertEqual(summary, "3 newsletters this morning — 1 in the briefing.")
    }

    /// A zero is usually a quiet morning, not a fault, and a sentence built
    /// to report a failure reads as one.
    func testNoneInTheBriefingDropsTheClauseRatherThanSayingZero() {
        let summary = NewsletterDesk.summary(for: [newsletter("A", "1")])
        XCTAssertEqual(summary, "1 newsletter this morning.")
    }

    func testNoNewslettersMeansNoSectionAtAll() {
        XCTAssertNil(NewsletterDesk.summary(for: []))
        XCTAssertTrue(NewsletterDesk.senders(from: []).isEmpty)
    }

    // MARK: - The cap

    /// A live morning had 44 newsletters. The cap has to hold, and it has to
    /// say what it cut — a cap that hides its remainder is how "plus eleven
    /// others" became an entire segment.
    func testTheCapCountsTheSendersItHides() {
        let many = (1...20).map { newsletter("Sender \($0)", "s", at: $0 % 24) }
        let senders = NewsletterDesk.senders(from: many)
        XCTAssertEqual(senders.count, 20)
        XCTAssertEqual(NewsletterDesk.remainder(senders),
                       20 - NewsletterDesk.visibleSenders)
    }

    func testNothingIsHiddenWhenEverythingFits() {
        let senders = NewsletterDesk.senders(from: [newsletter("A", "1")])
        XCTAssertEqual(NewsletterDesk.remainder(senders), 0)
    }

    /// The cap must never hide something that reached the briefing, which is
    /// what the ordering is for.
    func testAnythingInTheBriefingSurvivesTheCap() {
        var many = (1...20).map { newsletter("Filler \($0)", "s", at: 23) }
        many.append(newsletter("Heard", "s", at: 1, inBriefing: true))
        let visible = NewsletterDesk.senders(from: many)
            .prefix(NewsletterDesk.visibleSenders)
        XCTAssertTrue(visible.contains { $0.id == "Heard" })
    }
}
