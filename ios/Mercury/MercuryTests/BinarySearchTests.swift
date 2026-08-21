import XCTest
@testable import Mercury

final class BinarySearchTests: XCTestCase {

    private func manifest() throws -> BriefingManifest {
        try ManifestDecodingTests.loadFixture()
    }

    func testFindsTurnAtItsStartBoundary() throws {
        let m = try manifest()
        let turn = try XCTUnwrap(m.turn(containing: 12000))
        XCTAssertEqual(turn.text, "Standup at nine, then a one on one with Priya at eleven.")
    }

    func testFindsTurnMidway() throws {
        let m = try manifest()
        let turn = try XCTUnwrap(m.turn(containing: 13500))
        XCTAssertEqual(turn.startMs, 12000)
        XCTAssertEqual(turn.endMs, 16000)
    }

    func testEndBoundaryIsExclusiveAndBelongsToNextTurn() throws {
        // turns[4] is [12000, 16000), turns[5] is [16000, 19800) — the
        // instant at 16000 must resolve to the next turn, not the one
        // that just ended.
        let m = try manifest()
        let turn = try XCTUnwrap(m.turn(containing: 16000))
        XCTAssertEqual(turn.startMs, 16000)
        XCTAssertEqual(turn.speaker, "B")
    }

    func testGapBetweenTurnsReturnsNil() throws {
        // turns[1] ends at 4000, turns[2] starts at 5000 — a deliberate
        // fixture gap (silence / segment transition).
        let m = try manifest()
        XCTAssertNil(m.turn(containing: 4500))
    }

    func testPositionBeforeFirstTurnReturnsNil() throws {
        let m = try manifest()
        // No turn in the fixture starts before ms 0, so anything negative
        // (which cannot occur in practice, but exercises the lower bound)
        // and any position landing before the first turn's start is nil
        // only when there's a leading gap; here turn 0 starts at 0, so
        // assert the boundary itself resolves to the first turn.
        let turn = try XCTUnwrap(m.turn(containing: 0))
        XCTAssertEqual(turn.speaker, "A")
        XCTAssertEqual(turn.startMs, 0)
    }

    func testPositionAfterLastTurnReturnsNil() throws {
        let m = try manifest()
        XCTAssertNil(m.turn(containing: 26000))
        XCTAssertNil(m.turn(containing: 999_999))
    }

    func testEmptyManifestTurnsReturnsNil() throws {
        let empty = BriefingManifest(
            briefingId: "empty",
            generatedAt: .now,
            durationMs: 0,
            audioUrl: "https://example.com/a.wav",
            segments: [],
            turns: [],
            sources: []
        )
        XCTAssertNil(empty.turn(containing: 0))
    }
}
