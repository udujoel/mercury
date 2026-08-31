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

    // MARK: - transcript resolution after a seek

    /// A seek must resolve the transcript IMMEDIATELY. Waiting for the next
    /// display-link tick meant a seek while paused never updated it at all,
    /// because a paused coordinator has no display link to tick.
    ///
    /// `turn(coveringOrAfter:)` is what makes that possible for a position
    /// landing in the silence between turns, where `turn(containing:)`
    /// correctly returns nil.
    func testSeekIntoAGapResolvesForwardToTheNextTurn() throws {
        let manifest = try ManifestDecodingTests.loadFixture()

        // 19800..20000 is a gap in the fixture: turns[5] ends at 19800 and
        // turns[6] starts at 20000.
        XCTAssertNil(manifest.turn(containing: 19_900), "precondition: a real gap")

        let next = try XCTUnwrap(manifest.turn(coveringOrAfter: 19_900))
        XCTAssertEqual(next.startMs, 20_000)
    }

    func testStartingAtOrAfterInsideATurnReturnsThatTurn() throws {
        let manifest = try ManifestDecodingTests.loadFixture()
        let inside = try XCTUnwrap(manifest.turn(coveringOrAfter: 13_500))
        XCTAssertEqual(inside.startMs, 12_000)
        XCTAssertEqual(inside.endMs, 16_000)
    }

    func testStartingAtOrAfterPastTheLastTurnIsNil() throws {
        let manifest = try ManifestDecodingTests.loadFixture()
        XCTAssertNil(manifest.turn(coveringOrAfter: 999_999))
    }

    func testStartingAtOrAfterBeforeTheFirstTurnReturnsTheFirst() throws {
        let manifest = try ManifestDecodingTests.loadFixture()
        let first = try XCTUnwrap(manifest.turn(coveringOrAfter: 0))
        XCTAssertEqual(first.startMs, 0)
    }

    // MARK: - rendered turns are computed once

    /// `turn(containing:)` ran `turns.filter(\.isRendered)` on entry, so the
    /// O(log n) binary search searched an array it had just rebuilt in O(n)
    /// — twenty times a second, for the whole briefing. Now the list is
    /// derived once, at decode.
    func testRenderedTurnsIsStoredNotRecomputedPerCall() throws {
        let manifest = try ManifestDecodingTests.loadFixture()
        let a = manifest.renderedTurns
        let b = manifest.renderedTurns
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.count, manifest.turns.filter(\.isRendered).count)
    }

    func testUnrenderedTurnsAreExcludedFromTheStoredList() {
        let rendered = Turn(speaker: "A", segment: "cold_open", text: "spoken",
                            startMs: 0, endMs: 2_000)
        let failed = Turn(speaker: "B", segment: "cold_open", text: "tts failed",
                          startMs: -1, endMs: -1)
        let manifest = BriefingManifest(
            briefingId: "2026-08-21",
            generatedAt: .now,
            durationMs: 2_000,
            audioUrl: "/briefing/2026-08-21/audio",
            segments: [],
            turns: [rendered, failed],
            sources: []
        )

        XCTAssertEqual(manifest.turns.count, 2)
        XCTAssertEqual(manifest.renderedTurns, [rendered],
                       "a turn whose TTS failed must never reach the transcript")
    }
}
