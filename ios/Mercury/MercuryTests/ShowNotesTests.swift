import XCTest
@testable import Mercury

final class ShowNotesTests: XCTestCase {

    private func manifest() throws -> BriefingManifest {
        try ManifestDecodingTests.loadFixture()
    }

    // MARK: - Grouping

    func testGroupsSourcesBySourceTypePreservingFirstSeenOrder() {
        let sources = [
            SourceRef(turnIndex: 0, title: "Standup", url: nil, sourceName: "Calendar",
                      sourceType: "calendar", clusterSize: 1),
            SourceRef(turnIndex: 1, title: "Q3 doc", url: nil, sourceName: "Priya",
                      sourceType: "email", clusterSize: 1),
            SourceRef(turnIndex: 2, title: "1:1", url: nil, sourceName: "Calendar",
                      sourceType: "calendar", clusterSize: 1),
        ]

        let groups = ShowNotesLogic.grouped(sources)

        // "calendar" appears before "email" in the input, so it must lead
        // the section list even though it isn't alphabetically first —
        // Dictionary iteration order is undefined, which is exactly the
        // bug this asserts against.
        XCTAssertEqual(groups.map(\.id), ["calendar", "email"])
        XCTAssertEqual(groups[0].sources.map(\.title), ["Standup", "1:1"])
        XCTAssertEqual(groups[1].sources.map(\.title), ["Q3 doc"])
    }

    func testGroupLabelIsCapitalizedSourceType() {
        let sources = [
            SourceRef(turnIndex: 0, title: "A", url: nil, sourceName: "X",
                      sourceType: "news", clusterSize: 1),
        ]
        XCTAssertEqual(ShowNotesLogic.grouped(sources).first?.label, "News")
    }

    // MARK: - Simulator findings, 2026-10-11 (ROADMAP task 50)

    /// The manifest carries one ref per turn that cites a source, so an
    /// event the hosts mention four times arrived as four identical rows
    /// ("Flight to Casablanca (AT 556)" x4 in the 2026-10-10 briefing).
    func testASourceCitedByManyTurnsIsListedOnceAtItsFirstMention() {
        let flight = { (turn: Int) in
            SourceRef(turnIndex: turn, title: "Flight to Casablanca (AT 556)",
                      url: nil, sourceName: "Calendar", sourceType: "calendar",
                      clusterSize: 1)
        }
        let groups = ShowNotesLogic.grouped([flight(3), flight(4), flight(9), flight(12)])

        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(groups[0].sources.count, 1)
        // The FIRST mention, so a tap seeks to where it is first discussed.
        XCTAssertEqual(groups[0].sources[0].turnIndex, 3)
    }

    func testDifferentSourcesWithTheSameTitleStaySeparate() {
        let sources = [
            SourceRef(turnIndex: 0, title: "Weekly digest", url: nil,
                      sourceName: "Stratechery", sourceType: "email", clusterSize: 1),
            SourceRef(turnIndex: 1, title: "Weekly digest", url: nil,
                      sourceName: "Platformer", sourceType: "email", clusterSize: 1),
        ]
        XCTAssertEqual(ShowNotesLogic.grouped(sources)[0].sources.count, 2)
    }

    /// `sourceType.capitalized` turned "rss" into a header reading "Rss".
    func testSectionLabelsAreWordsNotEnumNames() {
        let label = { (type: String) in
            ShowNotesLogic.grouped([
                SourceRef(turnIndex: 0, title: "A", url: nil, sourceName: "X",
                          sourceType: type, clusterSize: 1),
            ]).first?.label
        }
        XCTAssertEqual(label("rss"), "News")
        XCTAssertEqual(label("email"), "Email")
        XCTAssertEqual(label("calendar"), "Calendar")
        XCTAssertEqual(label("reddit"), "Reddit")
    }

    func testEmptySourceListProducesNoGroups() {
        XCTAssertTrue(ShowNotesLogic.grouped([]).isEmpty)
    }

    func testGroupingAgainstTheRealFixtureCoversTwoSourceTypes() throws {
        let m = try manifest()
        let groups = ShowNotesLogic.grouped(m.sources)
        XCTAssertEqual(groups.map(\.id), ["calendar", "email"])
        XCTAssertEqual(groups.reduce(0) { $0 + $1.sources.count }, m.sources.count)
    }

    // MARK: - turn_index -> seek mapping

    func testSeekTargetMsResolvesTurnIndexToThatTurnsStartMs() throws {
        let m = try manifest()
        // Fixture's first source has turn_index 4, which is the
        // "Standup at nine..." turn starting at 12000ms.
        let source = try XCTUnwrap(m.sources.first)
        XCTAssertEqual(source.turnIndex, 4)

        let ms = ShowNotesLogic.seekTargetMs(for: source, in: m)
        XCTAssertEqual(ms, 12000)
        XCTAssertEqual(ms, m.turns[4].startMs)
    }

    func testSeekTargetMsForEverySourceInTheFixtureMatchesItsTurn() throws {
        let m = try manifest()
        for source in m.sources {
            let expected = m.turns[source.turnIndex].startMs
            XCTAssertEqual(ShowNotesLogic.seekTargetMs(for: source, in: m), expected)
        }
    }

    func testSeekTargetMsIsNilForOutOfRangeTurnIndex() throws {
        let m = try manifest()
        let source = SourceRef(turnIndex: 999, title: "Bad", url: nil,
                                sourceName: "X", sourceType: "email", clusterSize: 1)
        XCTAssertNil(ShowNotesLogic.seekTargetMs(for: source, in: m))
    }

    func testSeekTargetMsIsNilForNegativeTurnIndex() throws {
        let m = try manifest()
        let source = SourceRef(turnIndex: -1, title: "Bad", url: nil,
                                sourceName: "X", sourceType: "email", clusterSize: 1)
        XCTAssertNil(ShowNotesLogic.seekTargetMs(for: source, in: m))
    }

    func testSeekTargetMsIsNilOnAnEmptyManifest() {
        let empty = BriefingManifest(
            briefingId: "empty", generatedAt: .now, durationMs: 0, audioUrl: "https://x",
            segments: [], turns: [], sources: []
        )
        let source = SourceRef(turnIndex: 0, title: "Bad", url: nil,
                                sourceName: "X", sourceType: "email", clusterSize: 1)
        XCTAssertNil(ShowNotesLogic.seekTargetMs(for: source, in: empty))
    }
}
