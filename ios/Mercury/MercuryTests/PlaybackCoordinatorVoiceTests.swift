import XCTest
@testable import Mercury

/// Volume ramp maths for the Join flow's duck/restore behaviour (PRD 3.5:
/// "fade briefing audio to 15 percent over 120 milliseconds ... restore on
/// resume"). Split into two layers on purpose:
///
///  1. `PlaybackCoordinator.rampedVolume` — the pure interpolation formula —
///     checked with exact arithmetic, no timing involved at all.
///  2. `duck`/`restoreVolume` themselves — checked end to end, including the
///     asynchronous ramp loop, but only ever with short real durations
///     (tens of milliseconds) so the test suite stays fast. This exercises
///     `AVAudioEngine.mainMixerNode.outputVolume`, a plain property, not
///     actual audio output — no audio hardware or session route is
///     involved, so unlike speech recognition this is safe to run in the
///     simulator.
final class PlaybackCoordinatorVoiceTests: XCTestCase {

    // MARK: - Pure ramp maths

    func testRampedVolumeAtStartEqualsStartValue() {
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0, duration: 0.12)
        XCTAssertEqual(v, 1.0, accuracy: 0.0001)
    }

    func testRampedVolumeAtEndEqualsTargetExactly() {
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0.12, duration: 0.12)
        XCTAssertEqual(v, 0.15, accuracy: 0.0001)
    }

    func testRampedVolumeAtHalfwayIsTheMidpoint() {
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0.06, duration: 0.12)
        // Linear interpolation: 1.0 + (0.15 - 1.0) * 0.5 = 0.575
        XCTAssertEqual(v, 0.575, accuracy: 0.0001)
    }

    func testRampedVolumeAtQuarterPoint() {
        let v = PlaybackCoordinator.rampedVolume(start: 0.15, target: 1.0, elapsed: 0.03, duration: 0.12)
        // Restoring: 0.15 + (1.0 - 0.15) * 0.25 = 0.3625
        XCTAssertEqual(v, 0.3625, accuracy: 0.0001)
    }

    func testRampedVolumeClampsElapsedPastDurationToTarget() {
        // The real ramp loop's last tick often lands slightly past
        // `duration` — must never overshoot `target`.
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0.5, duration: 0.12)
        XCTAssertEqual(v, 0.15, accuracy: 0.0001)
    }

    func testRampedVolumeClampsNegativeElapsedToStart() {
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: -0.01, duration: 0.12)
        XCTAssertEqual(v, 1.0, accuracy: 0.0001)
    }

    func testRampedVolumeWithZeroDurationReturnsTargetImmediately() {
        let v = PlaybackCoordinator.rampedVolume(start: 1.0, target: 0.15, elapsed: 0, duration: 0)
        XCTAssertEqual(v, 0.15, accuracy: 0.0001)
    }

    func testRampedVolumeWorksSymmetricallyForRestoring() {
        // Restore is just a ramp toward 1.0 — same formula, opposite
        // direction. Confirms it isn't secretly special-cased for ducking
        // only.
        let start = PlaybackCoordinator.rampedVolume(start: 0.15, target: 1.0, elapsed: 0, duration: 0.12)
        let mid = PlaybackCoordinator.rampedVolume(start: 0.15, target: 1.0, elapsed: 0.06, duration: 0.12)
        let end = PlaybackCoordinator.rampedVolume(start: 0.15, target: 1.0, elapsed: 0.12, duration: 0.12)
        XCTAssertEqual(start, 0.15, accuracy: 0.0001)
        XCTAssertEqual(mid, 0.575, accuracy: 0.0001)
        XCTAssertEqual(end, 1.0, accuracy: 0.0001)
    }

    // MARK: - PRD constants

    func testDuckDefaultsMatchPRD35Verbatim() {
        XCTAssertEqual(PlaybackCoordinator.duckLevel, 0.15, accuracy: 0.0001)
        XCTAssertEqual(PlaybackCoordinator.duckSeconds, 0.12, accuracy: 0.0001)
    }

    // MARK: - duck()/restoreVolume() end to end

    @MainActor
    func testDuckWithZeroDurationSetsVolumeInstantly() {
        let coordinator = PlaybackCoordinator()
        coordinator.duck(to: 0.15, over: 0)
        XCTAssertEqual(coordinator.outputVolume, 0.15, accuracy: 0.0001)
    }

    @MainActor
    func testRestoreVolumeWithZeroDurationSetsVolumeInstantly() {
        let coordinator = PlaybackCoordinator()
        coordinator.duck(to: 0.15, over: 0)
        coordinator.restoreVolume(over: 0)
        XCTAssertEqual(coordinator.outputVolume, 1.0, accuracy: 0.0001)
    }

    @MainActor
    func testDuckRampsToTargetOverShortRealDuration() async {
        let coordinator = PlaybackCoordinator()
        XCTAssertEqual(coordinator.outputVolume, 1.0, accuracy: 0.0001, "starts at full volume")

        coordinator.duck(to: 0.15, over: 0.02)
        // Generous multiple of the ramp duration so this isn't sensitive to
        // scheduler jitter on a loaded CI machine.
        try? await Task.sleep(nanoseconds: 200_000_000)

        XCTAssertEqual(coordinator.outputVolume, 0.15, accuracy: 0.0001)
    }

    @MainActor
    func testRestoreVolumeRampsBackToFullOverShortRealDuration() async {
        let coordinator = PlaybackCoordinator()
        coordinator.duck(to: 0.15, over: 0)
        XCTAssertEqual(coordinator.outputVolume, 0.15, accuracy: 0.0001)

        coordinator.restoreVolume(over: 0.02)
        try? await Task.sleep(nanoseconds: 200_000_000)

        XCTAssertEqual(coordinator.outputVolume, 1.0, accuracy: 0.0001)
    }

    @MainActor
    func testStartingANewRampCancelsThePreviousOne() {
        // Ducking then immediately restoring (tap-again cancel path, PRD
        // 3.5) must end up at the restore target, not get stuck mid-fade
        // or racing between two competing ramp loops.
        let coordinator = PlaybackCoordinator()
        coordinator.duck(to: 0.15, over: 5.0) // long-running ramp
        coordinator.restoreVolume(over: 0)     // instantly supersedes it
        XCTAssertEqual(coordinator.outputVolume, 1.0, accuracy: 0.0001)
    }
}
