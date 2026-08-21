import XCTest
@testable import Mercury

final class SegmentSeekTests: XCTestCase {

    private func manifest() throws -> BriefingManifest {
        try ManifestDecodingTests.loadFixture()
    }

    func testSegmentStartMsForKnownSegmentID() throws {
        let m = try manifest()
        XCTAssertEqual(m.segmentStartMs(id: "weather"), 4000)
        XCTAssertEqual(m.segmentStartMs(id: "calendar"), 12000)
        XCTAssertEqual(m.segmentStartMs(id: "inbox"), 20000)
    }

    func testSegmentStartMsForUnknownSegmentIDIsNil() throws {
        let m = try manifest()
        XCTAssertNil(m.segmentStartMs(id: "does_not_exist"))
    }

    func testSegmentForTurnReturnsOwningChapter() throws {
        let m = try manifest()
        let calendarTurn = m.turns[4]  // starts at 12000, inside "calendar"
        let owningSegment = m.segment(for: calendarTurn)
        XCTAssertEqual(owningSegment?.id, "calendar")
    }

    func testSegmentForTurnInGapStillResolvesToPrecedingChapter() throws {
        // turns[2] starts at 5000ms, which is inside a gap after the
        // "weather" segment's own start (4000ms) but the segment
        // attribution should still pick "weather" — the last segment
        // whose startMs does not exceed the turn's startMs.
        let m = try manifest()
        let weatherTurn = m.turns[2]
        XCTAssertEqual(weatherTurn.startMs, 5000)
        XCTAssertEqual(m.segment(for: weatherTurn)?.id, "weather")
    }

    func testChapterTapSeekOffsetMatchesSegmentStart() throws {
        // Simulates ChapterTabs.onSelect / PlayerView wiring: tapping a
        // chapter tab should seek to exactly that segment's start_ms.
        let m = try manifest()
        for segment in m.segments {
            XCTAssertEqual(m.segmentStartMs(id: segment.id), segment.startMs)
        }
    }
}
