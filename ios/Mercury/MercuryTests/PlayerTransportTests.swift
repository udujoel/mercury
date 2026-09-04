import XCTest
@testable import Mercury

/// The player shipped with no elapsed time, no duration, no scrubber and no
/// skip — every one of which the app already had the data for, in
/// `PlaybackCoordinator.positionMs` and `BriefingManifest.durationMs`. This
/// covers the arithmetic those four controls need, as pure functions, so the
/// edge cases can be checked without an audio engine.
final class PlayerTransportTests: XCTestCase {

    // MARK: - Timecodes

    func testElapsedIsMinutesAndZeroPaddedSeconds() {
        XCTAssertEqual(PlayerTransport.timecode(ms: 134_000), "2:14")
        XCTAssertEqual(PlayerTransport.timecode(ms: 0), "0:00")
        XCTAssertEqual(PlayerTransport.timecode(ms: 9_000), "0:09")
        XCTAssertEqual(PlayerTransport.timecode(ms: 600_000), "10:00")
    }

    func testTimecodeTruncatesRatherThanRounding() {
        // A clock that rounds shows 2:14 for a fifth of a second before the
        // audio reaches it, and — worse at the end — shows the full duration
        // while the briefing is still speaking.
        XCTAssertEqual(PlayerTransport.timecode(ms: 134_999), "2:14")
    }

    func testANegativePositionClampsToZeroRatherThanPrintingMinusOne() {
        XCTAssertEqual(PlayerTransport.timecode(ms: -1), "0:00")
    }

    func testRemainingCountsDownAndCarriesItsSign() {
        XCTAssertEqual(
            PlayerTransport.remainingLabel(positionMs: 134_000, durationMs: 360_000),
            "−3:46"
        )
    }

    func testRemainingIsZeroNotNegativeWhenPositionOverrunsDuration() {
        // `durationMs` comes from the manifest and the position from the
        // audio file; the two are produced by different halves of the
        // pipeline and a stitched file can be a few frames longer.
        XCTAssertEqual(
            PlayerTransport.remainingLabel(positionMs: 361_000, durationMs: 360_000),
            "−0:00"
        )
    }

    func testRemainingRoundsUpSoItReachesZeroExactlyAtTheEnd() {
        // Truncating here would show "−0:00" for a whole second before the
        // briefing ends, while elapsed still climbs. Ceiling keeps the two
        // halves of the line consistent: they sum to the duration.
        XCTAssertEqual(
            PlayerTransport.remainingLabel(positionMs: 500, durationMs: 10_000),
            "−0:10"
        )
    }

    // MARK: - The progress line

    func testProgressIsTheFractionPlayed() {
        XCTAssertEqual(PlayerTransport.progress(positionMs: 90_000, durationMs: 360_000),
                       0.25, accuracy: 0.0001)
    }

    func testProgressIsZeroRatherThanNaNForAZeroDurationManifest() {
        // A degraded briefing can carry duration_ms 0. Dividing by it puts a
        // NaN into a SwiftUI frame width, which is a crash, not a glitch.
        XCTAssertEqual(PlayerTransport.progress(positionMs: 1_000, durationMs: 0), 0)
        XCTAssertEqual(PlayerTransport.progress(positionMs: 1_000, durationMs: -1), 0)
    }

    func testProgressClampsToTheUnitInterval() {
        XCTAssertEqual(PlayerTransport.progress(positionMs: 400_000, durationMs: 360_000), 1)
        XCTAssertEqual(PlayerTransport.progress(positionMs: -5_000, durationMs: 360_000), 0)
    }

    // MARK: - Drag to seek

    func testDraggingMapsXAcrossTheTrackToAPositionInTheBriefing() {
        XCTAssertEqual(
            PlayerTransport.seekTarget(atX: 87.5, trackWidth: 350, durationMs: 360_000),
            90_000
        )
    }

    func testDraggingPastEitherEndOfTheTrackClampsInsideTheBriefing() {
        // A drag that starts on the line and continues off the screen edge
        // reports an x outside the track on every frame after that.
        XCTAssertEqual(
            PlayerTransport.seekTarget(atX: -40, trackWidth: 350, durationMs: 360_000), 0)
        XCTAssertEqual(
            PlayerTransport.seekTarget(atX: 900, trackWidth: 350, durationMs: 360_000), 360_000)
    }

    func testSeekTargetIsZeroForAZeroWidthTrack() {
        // The first layout pass reports width 0. Dividing by it is the same
        // NaN as above, arriving through a different door.
        XCTAssertEqual(
            PlayerTransport.seekTarget(atX: 10, trackWidth: 0, durationMs: 360_000), 0)
    }

    // MARK: - Skip 15

    func testSkipForwardAndBackMoveFifteenSeconds() {
        XCTAssertEqual(PlayerTransport.skipInterval, 15_000)
        XCTAssertEqual(
            PlayerTransport.skipTarget(positionMs: 100_000, byMs: 15_000, durationMs: 360_000),
            115_000
        )
        XCTAssertEqual(
            PlayerTransport.skipTarget(positionMs: 100_000, byMs: -15_000, durationMs: 360_000),
            85_000
        )
    }

    func testSkippingBackFromTheOpeningLandsAtTheStartNotBeforeIt() {
        XCTAssertEqual(
            PlayerTransport.skipTarget(positionMs: 4_000, byMs: -15_000, durationMs: 360_000),
            0
        )
    }

    func testSkippingForwardNearTheEndLandsAtTheEndNotPastIt() {
        // `PlaybackCoordinator.scheduleWindow` already clamps the frame, but
        // a seek target past the file means the transport and the scrubber
        // disagree about where playback is for one frame.
        XCTAssertEqual(
            PlayerTransport.skipTarget(positionMs: 355_000, byMs: 15_000, durationMs: 360_000),
            360_000
        )
    }

    // MARK: - Rate

    func testRateLabelDropsTheTrailingZeroesOnlyWhereThereAreAny() {
        XCTAssertEqual(PlayerTransport.rateLabel(1.0), "1x")
        XCTAssertEqual(PlayerTransport.rateLabel(1.25), "1.25x")
        XCTAssertEqual(PlayerTransport.rateLabel(1.5), "1.5x")
        XCTAssertEqual(PlayerTransport.rateLabel(2.0), "2x")
    }

    func testEveryRateTheCoordinatorCyclesThroughHasAReadableLabel() {
        // The old label used "%.2g", which formats 1.25 as "1.2" — a speed
        // the player will never actually play at.
        for rate in PlaybackCoordinator.availableRates {
            let label = PlayerTransport.rateLabel(rate)
            XCTAssertTrue(label.hasSuffix("x"), label)
            let value = Float(label.dropLast())
            XCTAssertEqual(value, rate, "\(label) does not read back as \(rate)")
        }
    }

    // MARK: - Accessibility

    func testTheScrubberAnnouncesAPositionAPersonCanAct() {
        // VoiceOver reading "0.372" off a slider is useless; it needs the
        // same two numbers a sighted user gets.
        XCTAssertEqual(
            PlayerTransport.progressAccessibilityValue(positionMs: 134_000, durationMs: 360_000),
            "2 minutes 14 seconds of 6 minutes"
        )
    }

    func testTheScrubberAnnouncementHandlesSubMinutePositions() {
        XCTAssertEqual(
            PlayerTransport.progressAccessibilityValue(positionMs: 9_000, durationMs: 45_000),
            "9 seconds of 45 seconds"
        )
    }
}
