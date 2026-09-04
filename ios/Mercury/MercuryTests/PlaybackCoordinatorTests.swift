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
    //
    // `isEndOfPlayback(positionMs:audioDurationMs:)` and the four tests that
    // covered it are gone. It inferred "the audio drained" from the reported
    // position, and the reported position comes from `CADisplayLink`, which
    // stops firing when the app is backgrounded — so the inference was wrong
    // in exactly the case the app is designed for. `PlaybackCompletionTests`
    // below replaces them, asserting on the completion path itself.

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

/// Shared by both interruption suites below.
fileprivate func interruption(
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

fileprivate func routeChange(_ reason: AVAudioSession.RouteChangeReason) -> Notification {
    Notification(
        name: AVAudioSession.routeChangeNotification,
        object: AVAudioSession.sharedInstance(),
        userInfo: [AVAudioSessionRouteChangeReasonKey: reason.rawValue]
    )
}

final class InterruptionHandlingTests: XCTestCase {

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
        // kilo-code-bot, correctly: this test drove the no-options path
        // from the day it was written and asserted only on the half that
        // `.began` had already set. The interruption state it left behind
        // is the part that mattered, and nothing looked at it.
        XCTAssertFalse(
            coordinator.isInterruptedForTesting,
            "left set, playbackDidComplete rejects every later completion "
                + "and isPlaying sticks at true for the rest of the session"
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

// MARK: - Bot review findings, PR #12
//
// Every test below was seen to fail against the code as reviewed before the
// corresponding fix was written. Where a fix removes a guard, there is a
// paired negative control asserting the guard's real job is still done.

/// A briefing with a deliberate silence between the two turns: 0–1000ms is
/// spoken, 1000–2000ms is a gap, 2000–3000ms is spoken. The gap is the whole
/// point — it is where `turn(containing:)` returns nil.
private func gappedManifest() -> BriefingManifest {
    BriefingManifest(
        briefingId: "2026-08-23",
        generatedAt: Date(timeIntervalSince1970: 0),
        durationMs: 3_000,
        audioUrl: "/briefing/2026-08-23/audio",
        segments: [
            BriefingSegment(id: "inbox", label: "Inbox", startMs: 0),
            BriefingSegment(id: "news", label: "News", startMs: 2_000),
        ],
        turns: [
            Turn(speaker: "A", segment: "inbox", text: "first", startMs: 0, endMs: 1_000),
            Turn(speaker: "B", segment: "news", text: "second", startMs: 2_000, endMs: 3_000),
        ],
        sources: []
    )
}

final class PlaybackCompletionTests: XCTestCase {

    /// macroscope/cubic: the completion callback was gated on `positionMs`,
    /// which only moves when the display link fires — and `CADisplayLink`
    /// stops firing the moment the app is backgrounded. Listening with the
    /// phone in a pocket, the position freezes wherever it was at
    /// backgrounding, so the audio drains at 5:22 while the coordinator
    /// still believes it is at 2:00. The sole completion callback was then
    /// discarded: `isPlaying` stayed true, the briefing was never marked
    /// complete, and its resume position was never rewound.
    @MainActor
    func testCompletionIsAcceptedEvenWhenThePositionIsStale() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 322_036)
        coordinator.setPositionForTesting(120_000)

        coordinator.simulatePlaybackCompletion(engineRunning: true)

        XCTAssertTrue(
            coordinator.hasFinished,
            "the node drained; a display link that stopped ticking when the "
                + "app backgrounded is not evidence that it did not"
        )
        XCTAssertFalse(coordinator.isPlaying)
    }

    /// The negative control for the test above. Dropping the position gate
    /// must not turn every engine teardown into "the briefing ended" — that
    /// is the bug the gate was there to prevent, and it is now the engine's
    /// own state that rules it out rather than an inference from position.
    @MainActor
    func testEngineTeardownIsStillNotTheEndOfTheBriefing() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 322_036)
        coordinator.setPositionForTesting(321_000)

        coordinator.simulatePlaybackCompletion(engineRunning: false)

        XCTAssertFalse(
            coordinator.hasFinished,
            "the engine was stopped under us — that fires the same callback "
                + "as draining, and marking the briefing complete would rewind "
                + "the listener's place"
        )
    }

    /// The other half of the same protection, for the ordering the
    /// `isInterrupted` flag exists to cover.
    @MainActor
    func testAnInterruptionIsNeverAcceptedAsCompletion() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 322_036)
        coordinator.setPositionForTesting(321_000)
        coordinator.handleInterruption(interruption(.began))

        coordinator.simulatePlaybackCompletion(engineRunning: true)

        XCTAssertFalse(coordinator.hasFinished)
    }

    /// macroscope: `.ended` arrives without the options key, the guard
    /// returned early, and `isInterrupted` stayed set for the rest of the
    /// session — after which `playbackDidComplete` rejected every callback
    /// and the briefing could never register its own ending again.
    @MainActor
    func testInterruptionEndedWithNoOptionsKeyStillClearsTheInterruption() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 322_036)

        coordinator.handleInterruption(interruption(.began))
        coordinator.handleInterruption(interruption(.ended))

        XCTAssertFalse(
            coordinator.isInterruptedForTesting,
            "a missing options key means no options, not a permanent interruption"
        )

        // The consequence, which is what actually reaches the user.
        coordinator.setPositionForTesting(321_000)
        coordinator.simulatePlaybackCompletion(engineRunning: true)
        XCTAssertTrue(coordinator.hasFinished)
    }

    /// macroscope: a resume that lands at or past the end of the file
    /// cannot schedule anything, and the discarded return value left the
    /// transport pointing at a position it can never play from. `play()`
    /// then retried the same invalid position forever: the button did
    /// nothing, for the rest of the session.
    @MainActor
    func testAScheduleThatCannotBePlacedFinishesRatherThanStalling() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 3_000)

        // No file, so no window can be produced — the same failure a seek
        // past the end of real audio produces.
        coordinator.scheduleOrFinishForTesting(fromMs: 3_000)

        XCTAssertTrue(
            coordinator.hasFinished,
            "nothing left to schedule has to resolve to finished, or play() "
                + "retries the same unplayable position forever"
        )
    }
}

final class RouteChangePrivacyTests: XCTestCase {

    /// macroscope/cubic, and a privacy defect rather than a convenience one.
    ///
    /// A phone call pauses playback and records that it should resume. If
    /// the headphones come out DURING that call, the route-change handler
    /// saw `isPlaying == false` and did nothing — so when the call ended
    /// with `.shouldResume`, the coordinator restarted the user's inbox on
    /// the built-in speaker. That is exactly the broadcast the route-change
    /// handler exists to prevent, reached by a different door.
    @MainActor
    func testHeadphonesLeavingDuringAnInterruptionCancelsTheResume() {
        let coordinator = PlaybackCoordinator()
        coordinator.setIsPlayingForTesting(true)

        coordinator.handleInterruption(interruption(.began))
        coordinator.handleRouteChange(routeChange(.oldDeviceUnavailable))

        XCTAssertFalse(
            coordinator.wasPlayingBeforeInterruptionForTesting,
            "the audio route the user consented to is gone; the system's "
                + "permission to resume must not survive it"
        )

        coordinator.handleInterruption(interruption(.ended, options: .shouldResume))
        XCTAssertFalse(coordinator.isPlaying)
    }

    /// The negative control: an ordinary interruption with the headphones
    /// still in must keep resuming, or this "fix" would simply break
    /// auto-resume.
    @MainActor
    func testAnOrdinaryInterruptionStillKeepsItsResumeIntent() {
        let coordinator = PlaybackCoordinator()
        coordinator.setIsPlayingForTesting(true)

        coordinator.handleInterruption(interruption(.began))

        XCTAssertTrue(coordinator.wasPlayingBeforeInterruptionForTesting)
    }
}

final class TurnResolutionTests: XCTestCase {

    /// macroscope/cubic: `resolveTurn` fell forward to the next turn on
    /// EVERY call, including the 20 Hz tick. In the silence between two
    /// turns the transcript therefore jumped to the next line — and the
    /// chapter tab with it — before a word of that line had been spoken.
    @MainActor
    func testTheTranscriptHoldsTheFinishedLineThroughSilence() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 3_000)

        coordinator.resolveTurnForTesting(atMs: 500, fromSeek: false)
        XCTAssertEqual(coordinator.currentTurn?.text, "first")

        coordinator.resolveTurnForTesting(atMs: 1_500, fromSeek: false)

        XCTAssertEqual(
            coordinator.currentTurn?.text, "first",
            "1500ms is silence between two turns; the line that just "
                + "finished holds until the next one is actually speaking"
        )
        XCTAssertEqual(
            coordinator.currentSegment?.id, "inbox",
            "and the chapter tab must not jump ahead either"
        )
    }

    /// The negative control. Forward resolution is right after a SEEK:
    /// landing in a gap must show the line about to be spoken, not one from
    /// wherever the listener jumped from.
    @MainActor
    func testASeekIntoSilenceResolvesForwardToTheNextLine() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 3_000)

        coordinator.resolveTurnForTesting(atMs: 1_500, fromSeek: true)

        XCTAssertEqual(coordinator.currentTurn?.text, "second")
        XCTAssertEqual(coordinator.currentSegment?.id, "news")
    }

    /// Playback inside a turn is unaffected by any of this.
    @MainActor
    func testPlaybackInsideATurnStillAdvances() {
        let coordinator = PlaybackCoordinator()
        coordinator.loadForTesting(manifest: gappedManifest(), audioDurationMs: 3_000)

        coordinator.resolveTurnForTesting(atMs: 500, fromSeek: false)
        coordinator.resolveTurnForTesting(atMs: 2_500, fromSeek: false)

        XCTAssertEqual(coordinator.currentTurn?.text, "second")
    }
}

// MARK: - Audio session category
//
// A device-only failure, reported from an iPhone 16 Pro while every
// simulator run and the whole suite stayed green.
//
// `.playback` was configured with `options: [.allowBluetooth]`. That
// constant is Hands-Free Profile: the SDK header deprecates it in favour of
// `.allowBluetoothHFP` and documents it as controlling whether a paired HFP
// device is available *for input*. `.playback` has no input. A real audio
// session rejects the pair with paramErr (-50) — surfacing in the device log
// as `Failed to set properties, error: 4294967246`, which is -50 unsigned —
// and `setCategory` throws, taking `load()` with it. The briefing then
// reports "Cached audio could not be loaded ... Code=-50".
//
// The simulator's session does not enforce the combination, so no amount of
// simulator testing would have found it. What CAN be asserted here is the
// contract that was violated, which is what these tests do.

final class AudioSessionCategoryTests: XCTestCase {

    /// `.playback` accepts only a small set of options. Anything derived from
    /// recording — Hands-Free Profile, default-to-speaker — is a category
    /// mismatch, and a real session says so with paramErr.
    private static let optionsValidForPlayback: AVAudioSession.CategoryOptions = [
        .mixWithOthers,
        .duckOthers,
        .interruptSpokenAudioAndMixWithOthers,
        .allowBluetoothA2DP,
        .allowAirPlay,
    ]

    func testTheConfiguredOptionsAreLegalForPlayback() throws {
        // The value the app actually passes, not one this test invents. The
        // first draft called setCategory itself and asserted on its own call,
        // so it passed with the bug restored.
        let configured = PlaybackCoordinator.playbackCategoryOptions

        XCTAssertTrue(
            Self.optionsValidForPlayback.isSuperset(of: configured),
            """
            \(configured) contains an option that is not valid for .playback. \
            A real device rejects the whole setCategory call with paramErr \
            (-50) and playback never starts — the simulator does not.
            """
        )
    }

    /// The specific regression. `.allowBluetooth` is HFP, the narrow-band mono
    /// call profile — so even on a session that tolerated it, it would have
    /// made a spoken-word briefing sound worse on exactly the devices most
    /// likely to play it. `.playback` already routes to A2DP by default.
    func testHandsFreeProfileIsNotRequestedForPlayback() {
        XCTAssertFalse(
            PlaybackCoordinator.playbackCategoryOptions.contains(.allowBluetooth),
            "HFP is for phone calls; a briefing wants A2DP, which .playback gives by default"
        )
    }
}
