import AVFoundation
import XCTest
@testable import Mercury

/// Why these tests are shaped the way they are.
///
/// `PlaybackCoordinator` owns an `AVAudioEngine`, and the existing suite
/// contained nothing at all for it — `docs/mvp/ios_client.md` says so
/// outright, filing playback under "compiles and was reviewed, not
/// exercised". That is exactly how a hard crash in `seek(toMs:)` and a
/// player that never stopped both survived a green run.
///
/// The fix is not "test the engine". It is to pull the parts that are
/// arithmetic — where to schedule from, whether a completion callback means
/// the audio drained — out as pure `static` functions that can be checked
/// exactly, with no simulator audio hardware and no real file.
final class PlaybackCoordinatorTests: XCTestCase {

    // 44.1 kHz, ten seconds.
    private let sampleRate: Double = 44_100
    private var fileLength: AVAudioFramePosition { 441_000 }

    // MARK: - seek arithmetic (the crash)

    func testSeekToStartSchedulesTheWholeFile() throws {
        let window = try XCTUnwrap(PlaybackCoordinator.scheduleWindow(
            forMs: 0, fileLength: fileLength, sampleRate: sampleRate
        ))
        XCTAssertEqual(window.startFrame, 0)
        XCTAssertEqual(window.frameCount, AVAudioFrameCount(fileLength))
        XCTAssertEqual(window.positionMs, 0)
    }

    func testSeekMidFileSchedulesOnlyTheRemainder() throws {
        let window = try XCTUnwrap(PlaybackCoordinator.scheduleWindow(
            forMs: 4_000, fileLength: fileLength, sampleRate: sampleRate
        ))
        XCTAssertEqual(window.startFrame, 176_400)
        XCTAssertEqual(window.frameCount, AVAudioFrameCount(441_000 - 176_400))
        XCTAssertEqual(window.positionMs, 4_000)
    }

    /// THE CRASH.
    ///
    /// `AVAudioFrameCount(file.length - frame)` was computed before the
    /// `remaining > 0` guard could help. Past the end that subtraction is
    /// negative, and `AVAudioFrameCount` is unsigned: converting a negative
    /// value traps and takes the whole app down. A chapter tab on a
    /// truncated download, a lock-screen scrub to the very end, or a
    /// manifest whose `duration_ms` runs a few frames past the audio all
    /// reach it.
    func testSeekPastTheEndReturnsNilInsteadOfTrapping() {
        XCTAssertNil(PlaybackCoordinator.scheduleWindow(
            forMs: 11_000, fileLength: fileLength, sampleRate: sampleRate
        ))
    }

    func testSeekExactlyToTheEndReturnsNil() {
        XCTAssertNil(PlaybackCoordinator.scheduleWindow(
            forMs: 10_000, fileLength: fileLength, sampleRate: sampleRate
        ), "there is no audio left to schedule at the last frame")
    }

    /// A position far enough past the end to overflow `AVAudioFramePosition`
    /// on conversion — the second way the old arithmetic could trap.
    func testAbsurdlyLargeSeekIsClampedNotOverflowed() {
        XCTAssertNil(PlaybackCoordinator.scheduleWindow(
            forMs: .max, fileLength: fileLength, sampleRate: sampleRate
        ))
    }

    func testNegativeSeekIsClampedToTheStart() throws {
        let window = try XCTUnwrap(PlaybackCoordinator.scheduleWindow(
            forMs: -5_000, fileLength: fileLength, sampleRate: sampleRate
        ))
        XCTAssertEqual(window.startFrame, 0)
        XCTAssertEqual(window.positionMs, 0)
    }

    func testEmptyOrUnreadableFileYieldsNoWindow() {
        XCTAssertNil(PlaybackCoordinator.scheduleWindow(
            forMs: 0, fileLength: 0, sampleRate: sampleRate
        ))
        XCTAssertNil(PlaybackCoordinator.scheduleWindow(
            forMs: 0, fileLength: fileLength, sampleRate: 0
        ))
    }

    // MARK: - end of playback vs. being stopped

    func testDrainingToTheEndCountsAsFinished() {
        XCTAssertTrue(PlaybackCoordinator.isEndOfPlayback(
            positionMs: 322_036, audioDurationMs: 322_036
        ))
    }

    /// One 20 Hz tick short of the end still counts: the last position
    /// reported before the node stops is up to a tick behind the truth.
    func testATickShortOfTheEndStillCountsAsFinished() {
        XCTAssertTrue(PlaybackCoordinator.isEndOfPlayback(
            positionMs: 321_950, audioDurationMs: 322_036
        ))
    }

    /// An interruption tears down the engine, and THAT fires the same
    /// completion callback as the audio draining. Treating a phone call
    /// three minutes in as "the briefing ended" would clear the transport
    /// mid-sentence and make the interruption unresumable.
    func testBeingStoppedMidBriefingDoesNotCountAsFinished() {
        XCTAssertFalse(PlaybackCoordinator.isEndOfPlayback(
            positionMs: 180_000, audioDurationMs: 322_036
        ))
    }

    func testUnknownDurationFallsBackToTreatingTheCallbackAsTheEnd() {
        XCTAssertTrue(PlaybackCoordinator.isEndOfPlayback(
            positionMs: 0, audioDurationMs: 0
        ), "with no duration to compare against, the callback is all we have")
    }

    // MARK: - volume ramp maths
    //
    // `PlaybackCoordinator` already documents these as "the volume ramp
    // maths tests in PlaybackCoordinatorTests" — a file that did not exist.

    func testRampStartsAtTheStartValue() {
        XCTAssertEqual(
            PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0, duration: 0.12),
            1.0, accuracy: 0.0001
        )
    }

    func testRampLandsExactlyOnTargetAtTheEnd() {
        XCTAssertEqual(
            PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0.12, duration: 0.12),
            0.15, accuracy: 0.0001
        )
    }

    func testRampOvershootIsClampedToTarget() {
        XCTAssertEqual(
            PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 5, duration: 0.12),
            0.15, accuracy: 0.0001, "the last step usually ticks past the end"
        )
    }

    func testZeroDurationRampJumpsStraightToTarget() {
        XCTAssertEqual(
            PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0, duration: 0),
            0.15, accuracy: 0.0001
        )
    }
}

// MARK: - Interruptions and route changes
//
// Findings 1 and 2 from the self-review on PR #12. `handleInterruption` and
// `handleRouteChange` were internal rather than private specifically so a
// test could post a synthetic Notification at them, and nothing did — which
// is how the dead state below survived a green suite in the very PR that set
// out to remove it.

final class InterruptionHandlingTests: XCTestCase {

    private func interruption(
        _ type: AVAudioSession.InterruptionType,
        options: AVAudioSession.InterruptionOptions? = nil
    ) -> Notification {
        var info: [AnyHashable: Any] = [
            AVAudioSessionInterruptionTypeKey: type.rawValue
        ]
        if let options {
            info[AVAudioSessionInterruptionOptionKey] = options.rawValue
        }
        return Notification(
            name: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            userInfo: info
        )
    }

    private func routeChange(_ reason: AVAudioSession.RouteChangeReason) -> Notification {
        Notification(
            name: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            userInfo: [AVAudioSessionRouteChangeReasonKey: reason.rawValue]
        )
    }

    /// The bug this PR claimed to fix, surviving on the path the auto-resume
    /// does not cover: the user declines the system's resume and taps play
    /// themselves.
    @MainActor
    func testInterruptionWithoutShouldResumeLeavesPlayReschedulable() {
        let coordinator = PlaybackCoordinator()

        coordinator.handleInterruption(interruption(.began))
        // `.ended` with no options at all is what arrives when the user
        // starts another app's audio.
        coordinator.handleInterruption(interruption(.ended))

        // The node was flushed by the interruption. If the coordinator still
        // believes something is scheduled, `play()` calls `player.play()` on
        // nothing and the transport claims to play silence forever.
        XCTAssertFalse(
            coordinator.hasScheduledAudio,
            "an interruption flushes the player node; the coordinator must "
                + "not still believe a schedule is live, or play() will start "
                + "silence it can never recover from"
        )
    }

    @MainActor
    func testInterruptionIsNeverTreatedAsTheEndOfTheBriefing() {
        let coordinator = PlaybackCoordinator()
        coordinator.handleInterruption(interruption(.began))

        // A call landing inside the end-of-playback tolerance used to reach
        // finish(), which marks the briefing complete and rewinds the resume
        // position — indistinguishable from having listened to the end.
        XCTAssertTrue(coordinator.isInterruptedForTesting)
        XCTAssertFalse(
            coordinator.hasFinished,
            "an interruption is not an ending, however close to the end it lands"
        )
    }

    @MainActor
    func testInterruptionEndedClearsTheInterruptedFlag() {
        let coordinator = PlaybackCoordinator()
        coordinator.handleInterruption(interruption(.began))
        coordinator.handleInterruption(interruption(.ended, options: []))
        XCTAssertFalse(
            coordinator.isInterruptedForTesting,
            "leaving this set would make every later completion a no-op, so "
                + "the briefing could never register as finished again"
        )
    }

    /// PRIVACY. Unplugging headphones reroutes to the built-in speaker, and a
    /// Mercury briefing is the user's inbox read out loud.
    @MainActor
    func testOldDeviceUnavailablePausesPlayback() {
        let coordinator = PlaybackCoordinator()
        coordinator.setIsPlayingForTesting(true)

        coordinator.handleRouteChange(routeChange(.oldDeviceUnavailable))

        XCTAssertFalse(
            coordinator.isPlaying,
            "unplugging headphones must pause, or the briefing plays the "
                + "user's email out loud to whoever is nearby"
        )
    }

    @MainActor
    func testUnrelatedRouteChangesDoNotPause() {
        let coordinator = PlaybackCoordinator()
        coordinator.setIsPlayingForTesting(true)

        coordinator.handleRouteChange(routeChange(.newDeviceAvailable))

        XCTAssertTrue(
            coordinator.isPlaying,
            "plugging headphones IN must not stop playback"
        )
    }
}
