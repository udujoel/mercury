import XCTest
@testable import Mercury

/// The waiting room, and the promise that it stays out of the way.
///
/// ADR-001 means the server generates overnight and the audio is finished
/// before the app opens, so this exists only for "opening before the cron
/// ran, or after a failed run". The roadmap asks specifically for a test
/// that it never appears on a normal morning — this file is that test, plus
/// the ones that stop it being a screen you can never reach.
final class WaitingRoomTests: XCTestCase {

    // MARK: - When it appears

    func testOnlyTheNotGeneratedStatusOpensIt() {
        XCTAssertTrue(WaitingRoom.shouldEnter(status: 404))
    }

    /// Every other failure is an ORDINARY error with ordinary text.
    ///
    /// A 500, a 401 or a timeout are all things where "your briefing isn't
    /// ready, try shortly" is a comforting lie: the briefing may be sitting
    /// in R2 perfectly intact while the token is wrong. Those get the real
    /// message, which is the only one that can lead to a fix.
    func testOtherFailuresDoNot() {
        for status in [200, 401, 403, 405, 429, 500, 502, 503] {
            XCTAssertFalse(
                WaitingRoom.shouldEnter(status: status),
                "\(status) is not a missing briefing")
        }
        XCTAssertFalse(
            WaitingRoom.shouldEnter(status: nil),
            "a transport failure has no status and is not a missing briefing")
    }

    // MARK: - The promise: never on a normal morning

    /// The whole point, asserted end to end rather than on the predicate.
    ///
    /// A successful fetch must leave nothing that could open the waiting
    /// room: no failure status, and a manifest on screen. Either alone
    /// would keep it shut, and the test wants both — checking one would
    /// pass even if the other regressed.
    @MainActor
    func testANormalMorningRecordsNoFailure() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)

        await state.playBriefing()

        // The fake's `downloadAudio` throws a 404 — deliberately, and it is
        // what makes this test worth having. The manifest arrived, so there
        // IS a briefing; a 404 on its audio must not read as "nothing was
        // made this morning", or a broken object in R2 would be presented
        // to the user as a run that never happened.
        XCTAssertNotNil(state.manifest, "the briefing itself loaded")
        XCTAssertNil(state.todaysFailureStatus,
                     "a briefing that exists is not a missing one")
        XCTAssertFalse(WaitingRoom.shouldEnter(status: state.todaysFailureStatus))
    }

    /// The status survives to the view layer as a NUMBER.
    ///
    /// It used to be re-scraped from "Backend returned 404: …" — prose
    /// written to be read by a person, so rewording the sentence would have
    /// silently turned the waiting room off forever with every test still
    /// green.
    @MainActor
    func testANotGeneratedFailureIsRecordedAsA404() async {
        // Both halves of the real morning-before-the-cron path: R2 has no
        // manifest, and the read-only Worker answers 405 to the generate
        // that `fetchTodayOrGenerate` falls back to. Setting only the first
        // is what an earlier cut of this test did, and the fake cheerfully
        // GENERATED a briefing — so the test asserted a missing-briefing
        // state it had never actually reached.
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(404, "no manifest")))
        api.generateError = APIError.badStatus(405, "read-only backend")
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertNil(state.manifest, "there is genuinely no briefing")
        XCTAssertEqual(state.todaysFailureStatus, 404)
        XCTAssertTrue(WaitingRoom.shouldEnter(status: state.todaysFailureStatus))
    }

    /// A 500 must not open it, driven through the same path.
    @MainActor
    func testAServerErrorIsRecordedAsItself() async {
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(500, "exploded")))
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertEqual(state.todaysFailureStatus, 500)
        XCTAssertFalse(WaitingRoom.shouldEnter(status: state.todaysFailureStatus))
    }

    // MARK: - The bed

    /// Bundled, and actually there.
    ///
    /// This is the app-icon lesson written down as an assertion: that
    /// catalog built cleanly three times while shipping nothing, because
    /// "it compiles" and "it is in the product" are different claims. A
    /// missing bed would leave the waiting room silent, and silence is
    /// indistinguishable from a bug the user cannot report.
    func testTheBedIsInTheBundle() throws {
        let url = try XCTUnwrap(
            Bundle.main.url(forResource: "waiting-bed", withExtension: "mp3")
                ?? Bundle(for: Self.self).url(
                    forResource: "waiting-bed", withExtension: "mp3"),
            "waiting-bed.mp3 is not in the app bundle")
        let size = try Data(contentsOf: url).count
        XCTAssertGreaterThan(size, 10_000, "a real audio file, not a stub")
    }

    /// An archive morning that is gone must not read as today's.
    ///
    /// Retention sweeps briefings, and the index is a list fetched a moment
    /// earlier — so tapping a row whose object has just been swept is an
    /// ordinary 404. It used to satisfy every condition the waiting room
    /// checks, and Home replaced "that morning is gone" with a promise of a
    /// briefing that was never coming.
    @MainActor
    func testAMissingArchiveBriefingIsNotTodaysWaitingRoom() async {
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(404, "swept")))
        let state = AppState(api: api)

        await state.openBriefing(id: "2019-01-01")

        XCTAssertEqual(state.lastFailure?.status, 404, "it did fail with a 404")
        XCTAssertEqual(state.lastFailure?.briefingId, "2019-01-01")
        XCTAssertNil(state.todaysFailureStatus,
                     "but it says nothing about today")
        XCTAssertFalse(WaitingRoom.shouldEnter(status: state.todaysFailureStatus))
    }

    /// The line says when, and promises no when.
    func testTheHoldingLineDoesNotPromiseATime() {
        let line = WaitingRoom.line.lowercased()
        XCTAssertTrue(line.contains("morning"), "it says when briefings are made")
        for guess in ["any moment", "any second", "in a minute", "right now"] {
            XCTAssertFalse(line.contains(guess),
                           "the run it waits for may have failed outright")
        }
    }
}
