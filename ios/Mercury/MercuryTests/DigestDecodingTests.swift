import XCTest
@testable import Mercury

/// Decoding the browse surface.
///
/// The digest shipped on the backend before the app could read it, so R2
/// already holds three different shapes: digests with `points` and
/// `image_url`, digests with neither, and briefings with no digest at all.
/// Every one of them has to render. A decode failure here is a blank screen
/// on a morning that worked yesterday, which is the failure mode
/// `ManifestDecodingTests` exists to prevent for the player.
final class DigestDecodingTests: XCTestCase {

    private func decode(_ json: String) throws -> Digest {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(Digest.self, from: Data(json.utf8))
    }

    // MARK: - The shapes already in R2

    func testADigestFromBeforeBulletsAndImagesStillDecodes() throws {
        let digest = try decode("""
        {"briefing_id": "2026-09-01", "stories": [
            {"id": "st_c1", "headline": "Northwind buys Halcyon",
             "summary": "A paragraph.", "source_count": 3, "sources": [],
             "covered_in_audio": true, "turn_index": 4}
        ], "newsletters": []}
        """)

        let story = try XCTUnwrap(digest.stories.first)
        XCTAssertEqual(story.headline, "Northwind buys Halcyon")
        // Absent, not broken: the detail view falls back to the paragraph.
        XCTAssertTrue(story.points.isEmpty)
        XCTAssertNil(story.imageURL)
        XCTAssertEqual(story.summary, "A paragraph.")
    }

    func testADigestWithNoStoriesOrNewslettersIsAnEmptyDeskNotAnError() throws {
        let digest = try decode(#"{"briefing_id": "2026-09-01"}"#)
        XCTAssertTrue(digest.stories.isEmpty)
        XCTAssertTrue(digest.newsletters.isEmpty)
    }

    func testTheFullShapeDecodes() throws {
        let digest = try decode("""
        {"briefing_id": "2026-09-04", "stories": [
            {"id": "st_c1", "headline": "H", "summary": "S",
             "points": ["one", "two"], "why_it_matters": "W",
             "image_url": "https://cdn.example.com/a.jpg",
             "topics": ["ai"], "source_count": 2,
             "sources": [
                {"source_name": "Reuters", "title": "T",
                 "url": "https://example.com/a",
                 "published_at": "2026-09-04T06:00:00Z", "source_type": "rss"}
             ],
             "covered_in_audio": true, "turn_index": 4, "segment": "world"}
        ], "newsletters": [
            {"id": "nl_1", "source_name": "Stratechery", "subject": "Monday",
             "received_at": "2026-09-04T05:00:00Z", "in_briefing": true,
             "story_ids": ["st_c1"]}
        ]}
        """)

        let story = try XCTUnwrap(digest.stories.first)
        XCTAssertEqual(story.points, ["one", "two"])
        XCTAssertEqual(story.imageURL?.absoluteString, "https://cdn.example.com/a.jpg")
        XCTAssertEqual(story.sources.first?.url?.absoluteString, "https://example.com/a")
        XCTAssertTrue(try XCTUnwrap(digest.newsletters.first).reachedTheBriefing)
    }

    // MARK: - Fields that must degrade rather than throw

    /// A URL the backend wrote that `URL(string:)` will not take. No image is
    /// the same outcome as no field, and both beat losing the story.
    func testAnUnparseableImageUrlCostsTheImageNotTheStory() throws {
        let digest = try decode("""
        {"briefing_id": "x", "stories": [
            {"id": "s", "headline": "H", "image_url": "http://[", "sources": []}
        ]}
        """)

        XCTAssertEqual(digest.stories.count, 1)
        XCTAssertNil(digest.stories.first?.imageURL)
    }

    /// An email source has no URL, structurally — `gmail.py` builds every
    /// email Item with none. The row must render as unopenable rather than
    /// as a link that does nothing.
    func testAnEmailSourceDecodesWithNoLink() throws {
        let digest = try decode("""
        {"briefing_id": "x", "stories": [
            {"id": "s", "headline": "H", "sources": [
                {"source_name": "Stratechery", "title": "Monday", "url": null,
                 "source_type": "email"}
            ]}
        ]}
        """)

        let source = try XCTUnwrap(digest.stories.first?.sources.first)
        XCTAssertNil(source.url)
        XCTAssertFalse(source.isOpenable)
    }

    /// The backend denormalises `source_count`; deriving it when absent means
    /// the two can only disagree by the backend being wrong, never by the
    /// field being missing.
    func testAMissingSourceCountIsDerivedFromTheSources() throws {
        let digest = try decode("""
        {"briefing_id": "x", "stories": [
            {"id": "s", "headline": "H", "sources": [
                {"source_name": "A", "title": "a"}, {"source_name": "B", "title": "b"}
            ]}
        ]}
        """)

        XCTAssertEqual(digest.stories.first?.sourceCount, 2)
    }

    // MARK: - The desk's ordering

    /// Covered stories first, in the order the hosts discussed them — the
    /// only ordering the morning actually has. Then everything the briefing
    /// had no time for.
    func testCoveredStoriesLeadInTheOrderTheyWereDiscussed() {
        let digest = Digest(briefingId: "x", stories: [
            DigestStory(id: "late", headline: "L", coveredInAudio: true, turnIndex: 9),
            DigestStory(id: "unread", headline: "U"),
            DigestStory(id: "early", headline: "E", coveredInAudio: true, turnIndex: 2),
        ])

        XCTAssertEqual(digest.covered.map(\.id), ["early", "late"])
        XCTAssertEqual(digest.uncovered.map(\.id), ["unread"])
    }

    /// A covered story with no turn index should not jump to the front of
    /// the audio order — there is no evidence it belongs there.
    func testACoveredStoryWithNoTurnIndexSortsLast() {
        let digest = Digest(briefingId: "x", stories: [
            DigestStory(id: "nowhere", headline: "N", coveredInAudio: true),
            DigestStory(id: "second", headline: "S", coveredInAudio: true, turnIndex: 5),
        ])

        XCTAssertEqual(digest.covered.map(\.id), ["second", "nowhere"])
    }

    // MARK: - A digest this build is too old to read

    /// The one field that refuses rather than degrades.
    ///
    /// Every other default turns a missing field into an empty state, which
    /// is right for a digest written by an OLDER backend. Applying the same
    /// leniency to a NEWER one renders an empty desk for a briefing that
    /// has stories, and says nothing about why.
    func testADigestFromANewerBackendIsRejectedRatherThanShownEmpty() {
        XCTAssertThrowsError(try decode("""
        {"briefing_id": "x", "schema_version": 99, "shelves": []}
        """))
    }

    func testTheCurrentSchemaVersionIsAccepted() throws {
        let digest = try decode("""
        {"briefing_id": "x", "schema_version": 1, "stories": []}
        """)
        XCTAssertEqual(digest.briefingId, "x")
    }

    /// Absent means version 1 — that is what the backend field defaults to.
    func testAMissingSchemaVersionReadsAsTheOriginalShape() throws {
        XCTAssertNoThrow(try decode(#"{"briefing_id": "x"}"#))
    }

    // MARK: - The archive index

    /// The field the backend actually emits is `in_briefing`, a BOOL.
    ///
    /// The first cut of this model invented a `turn_index` the backend has
    /// never written, so every newsletter decoded as "did not reach the
    /// briefing" — silently wrong in exactly the direction the screen
    /// exists to show, and green, because nothing asserted it against a
    /// real payload.
    func testANewsletterThatReachedTheBriefingSaysSo() throws {
        let digest = try decode("""
        {"briefing_id": "x", "newsletters": [
            {"id": "nl_1", "source_name": "Stratechery", "subject": "Monday",
             "in_briefing": true, "story_ids": ["st_c1"]},
            {"id": "nl_2", "source_name": "Platformer", "subject": "Friday",
             "in_briefing": false}
        ]}
        """)

        XCTAssertEqual(digest.newsletters.map(\.reachedTheBriefing), [true, false])
        XCTAssertEqual(digest.newsletters.first?.storyIds, ["st_c1"])
    }

    /// A newsletter has no public URL — `gmail.py` builds every email Item
    /// with none — so the field is carried and always nil.
    func testANewsletterHasNoLink() throws {
        let digest = try decode("""
        {"briefing_id": "x", "newsletters": [
            {"id": "nl_1", "source_name": "S", "subject": "M", "url": null}
        ]}
        """)
        XCTAssertNil(digest.newsletters.first?.url)
    }

    func testTheBriefingIndexDecodes() throws {
        let index = try JSONDecoder().decode(
            BriefingIndex.self,
            from: Data(#"{"briefings": ["2026-09-04", "2026-09-03"], "count": 2}"#.utf8))
        XCTAssertEqual(index.briefings, ["2026-09-04", "2026-09-03"])
    }

    func testAnEmptyArchiveDecodesAsEmptyRatherThanFailing() throws {
        let index = try JSONDecoder().decode(
            BriefingIndex.self, from: Data(#"{"count": 0}"#.utf8))
        XCTAssertTrue(index.briefings.isEmpty)
    }

    // MARK: - How a date reads

    /// A fixed date in the past, and a containment check rather than an
    /// exact string.
    ///
    /// The first version of this asserted `"2026-09-04"` renders as
    /// "Friday 4 September" and passed — until the next day, when 4
    /// September became "Yesterday" and the test failed for being right.
    /// A test whose result depends on when it runs is the recurring bug
    /// class in this project, and writing one INTO the suite is worse than
    /// shipping one.
    ///
    /// Exact formatting is also locale-dependent ("Friday 6 March" vs
    /// "Friday, March 6"), and the property worth pinning is that a machine
    /// id is turned into something a person reads — not which order the
    /// fields come in.
    func testAnArchiveRowNamesTheDayRatherThanShowingAnIsoString() {
        let shown = ArchiveView.display("2020-03-06")

        // The property, not the wording: a machine id became something a
        // person reads. Asserting "March" and "Friday" was the same mistake
        // the doc comment above warns about, one axis over — the first
        // version was time-dependent, the second was locale-dependent, and
        // both pass on the machine that wrote them and fail elsewhere.
        XCTAssertNotEqual(shown, "2020-03-06")
        XCTAssertFalse(shown.isEmpty)
        XCTAssertTrue(shown.contains("6"), shown)
    }

    /// Relative to now, computed rather than hardcoded — and built in the
    /// SERVER's zone, because that is the zone `display` parses in.
    ///
    /// The first version of this used `.current` for both the formatter and
    /// the day arithmetic, which is the exact timezone mismatch this PR was
    /// written to fix, reproduced inside its own test. It passes on a
    /// machine whose clock agrees with the server and flips silently on one
    /// that does not.
    func testTodayAndYesterdayAreNamedRatherThanDated() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = BriefingClock.serverTimeZone

        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = calendar
        f.timeZone = BriefingClock.serverTimeZone
        f.dateFormat = "yyyy-MM-dd"

        let today = f.string(from: .now)
        let yesterday = f.string(
            from: calendar.date(byAdding: .day, value: -1, to: .now)!)

        XCTAssertEqual(ArchiveView.display(today), "Today")
        XCTAssertEqual(ArchiveView.display(yesterday), "Yesterday")
    }

    /// An id that does not parse is a bug worth seeing. A blank row is a bug
    /// you cannot see.
    func testAnUnparseableIdFallsBackToItselfRatherThanToNothing() {
        XCTAssertEqual(ArchiveView.display("not-a-date"), "not-a-date")
    }
}
