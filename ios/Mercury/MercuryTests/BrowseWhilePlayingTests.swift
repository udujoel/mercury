import XCTest
@testable import Mercury

/// Browsing a day must not stop the briefing you are listening to
/// (ROADMAP task 49).
///
/// Found on the simulator on 2026-10-11: with Sunday's briefing playing,
/// tapping Saturday in the calendar strip loaded Saturday's audio into the
/// engine, so Sunday stopped and the mini player read "Saturday, 0:00".
/// `openBriefing(andPlay: false)` was always a load: it only skipped the
/// final `play()`.
final class BrowseWhilePlayingTests: XCTestCase {

    func testChoosingToPlayAlwaysLoads() {
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sat", andPlay: true,
                                  isPlaying: true, loadedId: "sun"),
            .load)
    }

    func testLookingAtAnotherDayWhileOneIsPlayingLeavesThePlayerAlone() {
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sat", andPlay: false,
                                  isPlaying: true, loadedId: "sun"),
            .showOnly)
    }

    /// Reloading the briefing already in the engine would restart it at
    /// zero, playing or paused.
    func testTheDayAlreadyLoadedIsNotReloaded() {
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sun", andPlay: false,
                                  isPlaying: false, loadedId: "sun"),
            .showOnly)
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sun", andPlay: false,
                                  isPlaying: true, loadedId: "sun"),
            .showOnly)
    }

    /// Nothing playing: browsing loads, as it always has, so the play
    /// button on the browsed day works offline and at once. A paused
    /// briefing's position is already persisted per briefing, so replacing
    /// it in the engine loses nothing.
    func testBrowsingWithNothingPlayingStillLoads() {
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sat", andPlay: false,
                                  isPlaying: false, loadedId: "sun"),
            .load)
        XCTAssertEqual(
            AppState.browseAction(requestedId: "sat", andPlay: false,
                                  isPlaying: false, loadedId: nil),
            .load)
    }

    // MARK: - The card on a browsed day

    /// Found on the simulator once browsing stopped interrupting playback:
    /// Saturday's card drew a PAUSE icon while Sunday played, because it
    /// read `playback.isPlaying` alone, and tapping it would have paused
    /// Sunday rather than playing Saturday. A state the old code could
    /// never reach, because browsing always replaced what was playing.
    func testABrowsedDaysCardDoesNotClaimToBePlaying() {
        XCTAssertFalse(AppState.cardShowsPlaying(isPlaying: true, isCurrent: false))
        XCTAssertTrue(AppState.cardShowsPlaying(isPlaying: true, isCurrent: true))
        XCTAssertFalse(AppState.cardShowsPlaying(isPlaying: false, isCurrent: true))
    }

    func testTappingABrowsedDaysCardPlaysThatDay() {
        XCTAssertEqual(AppState.cardTapAction(isLoaded: true, isCurrent: false), .openShown)
    }

    func testTappingTheCurrentDaysCardTogglesIt() {
        XCTAssertEqual(AppState.cardTapAction(isLoaded: true, isCurrent: true), .toggle)
    }

    func testTappingWithNothingLoadedFetches() {
        XCTAssertEqual(AppState.cardTapAction(isLoaded: false, isCurrent: false), .fetch)
    }
}
