import Foundation

/// The arithmetic behind the player's transport row.
///
/// The player shipped without elapsed time, duration, a scrubber or skip —
/// and the app already had every number those need: `positionMs` from
/// `PlaybackCoordinator` and `durationMs` from the manifest. Nothing was
/// missing except the four controls that read them.
///
/// Kept out of the view because most of what is interesting here is edge
/// cases — a zero-duration manifest, a drag that leaves the screen, a
/// position that overruns the file — and none of them are worth an audio
/// engine and a simulator to check.
enum PlayerTransport {

    /// PRD 3.4's skip interval, and the one every podcast player uses.
    static let skipInterval = 15_000

    // MARK: - Timecodes

    /// `m:ss`, truncated.
    ///
    /// Truncating rather than rounding matters at both ends: a rounding clock
    /// shows the next second a fifth of a second early all the way through,
    /// and shows the full duration while the hosts are still speaking.
    static func timecode(ms: Int) -> String {
        formatted(seconds: max(ms, 0) / 1000)
    }

    /// The right-hand figure: how much is left, counting down.
    ///
    /// Rounded UP, deliberately, where elapsed truncates. The two figures sit
    /// on one line and a reader adds them: with both truncating they sum to
    /// one second less than the briefing for most of every second, and the
    /// remaining side hits "−0:00" a full second before the audio stops.
    static func remainingLabel(positionMs: Int, durationMs: Int) -> String {
        let remaining = max(durationMs - positionMs, 0)
        // The minus is U+2212, not a hyphen: at the tabular figure sizes this
        // sets at, a hyphen reads as a dash in the text rather than as a sign.
        return "−" + formatted(seconds: (remaining + 999) / 1000)
    }

    private static func formatted(seconds: Int) -> String {
        String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    // MARK: - The progress line

    /// Fraction played, clamped to 0…1.
    ///
    /// The guard is not defensive padding. A degraded briefing can carry
    /// `duration_ms` 0 — that is exactly what ADR's degraded-briefing path
    /// produces — and the quotient goes into a SwiftUI frame width, where a
    /// NaN is a crash rather than a glitch.
    static func progress(positionMs: Int, durationMs: Int) -> Double {
        guard durationMs > 0 else { return 0 }
        return min(max(Double(positionMs) / Double(durationMs), 0), 1)
    }

    /// Where a drag at `x` across a track of `trackWidth` points lands.
    ///
    /// Clamped at both ends because a drag that starts on the line and
    /// carries off the edge of the screen reports an out-of-track x on every
    /// frame after that, and at width 0 — which is what the first layout pass
    /// reports — because that is the same NaN through a different door.
    static func seekTarget(atX x: CGFloat, trackWidth: CGFloat, durationMs: Int) -> Int {
        guard trackWidth > 0, durationMs > 0 else { return 0 }
        let fraction = min(max(Double(x / trackWidth), 0), 1)
        return Int((fraction * Double(durationMs)).rounded())
    }

    // MARK: - Skip

    /// Clamped inside the briefing. `PlaybackCoordinator.scheduleWindow`
    /// already clamps the audio frame, but an unclamped target means the
    /// scrubber and the transport disagree about where playback is until the
    /// next tick corrects one of them.
    static func skipTarget(positionMs: Int, byMs delta: Int, durationMs: Int) -> Int {
        min(max(positionMs + delta, 0), max(durationMs, 0))
    }

    // MARK: - Rate

    /// "1x", "1.25x", "1.5x", "2x".
    ///
    /// The label this replaces was `String(format: "%.2gx", …)`, which
    /// renders 1.25 — one of the four rates the coordinator actually cycles
    /// through — as "1.2x", a speed the player never plays at.
    static func rateLabel(_ rate: Float) -> String {
        String(format: "%gx", Double(rate))
    }

    // MARK: - Accessibility

    /// What VoiceOver reads off the scrubber. A slider announcing "37 per
    /// cent" tells a listener nothing they can act on; the two figures a
    /// sighted user gets do.
    static func progressAccessibilityValue(positionMs: Int, durationMs: Int) -> String {
        "\(spoken(ms: positionMs)) of \(spoken(ms: durationMs))"
    }

    static func spoken(ms: Int) -> String {
        let total = max(ms, 0) / 1000
        let minutes = total / 60
        let seconds = total % 60
        let m = "\(minutes) \(minutes == 1 ? "minute" : "minutes")"
        let s = "\(seconds) \(seconds == 1 ? "second" : "seconds")"
        if minutes == 0 { return s }
        if seconds == 0 { return m }
        return "\(m) \(s)"
    }
}
