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
