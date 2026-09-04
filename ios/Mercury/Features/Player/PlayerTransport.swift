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
    ///
    /// Every operand is clamped BEFORE the subtraction, and the ceiling is
    /// taken by division rather than by adding 999. Both matter: Swift's
    /// `Int` arithmetic is checked, so `durationMs - positionMs` traps on
    /// underflow at `durationMs == Int.min`, and `remaining + 999` traps on
    /// overflow at `durationMs == Int.max` — which a position of 0 reaches.
    /// `durationMs` is a manifest field and `Int.max` decodes from JSON
    /// perfectly happily, so both are a crash on the screen whose entire job
    /// is to render a number for a briefing that came out wrong.
    static func remainingLabel(positionMs: Int, durationMs: Int) -> String {
        let duration = max(durationMs, 0)
        let position = min(max(positionMs, 0), duration)
        let remaining = duration - position
        let seconds = remaining / 1000 + (remaining % 1000 == 0 ? 0 : 1)
        // The minus is U+2212, not a hyphen: at the tabular figure sizes this
        // sets at, a hyphen reads as a dash in the text rather than as a sign.
        return "−" + formatted(seconds: seconds)
    }

    /// Interpolated rather than `String(format: "%d:%02d", …)`.
    ///
    /// `%d` reads four bytes and Swift passes an `Int` as eight, so a minute
    /// count past `Int32.max` — which the same absurd manifest that motivates
    /// the clamping above produces — misaligns the second argument and prints
    /// a timecode assembled from the wrong bytes. The seconds field is the
    /// only one that needs padding and it is always 0...59.
    private static func formatted(seconds: Int) -> String {
        let total = max(seconds, 0)
        let s = total % 60
        return "\(total / 60):\(s < 10 ? "0" : "")\(s)"
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
    ///
    /// The final conversion is guarded for the same reason `remainingLabel`
    /// clamps: `Double(Int.max)` rounds UP to exactly 2^63, so a drag to the
    /// far end of a track on a manifest claiming `Int.max` milliseconds hands
    /// `Int(_:)` a value one past the top of its range and traps. Neither
    /// review caught this one; it is the same defect as the timecode, through
    /// the floating-point door.
    static func seekTarget(atX x: CGFloat, trackWidth: CGFloat, durationMs: Int) -> Int {
        guard trackWidth > 0, durationMs > 0 else { return 0 }
        let fraction = min(max(Double(x / trackWidth), 0), 1)
        let target = (fraction * Double(durationMs)).rounded()
        guard target < Double(Int.max) else { return durationMs }
        return Int(target)
    }

    // MARK: - Skip

    /// Clamped inside the briefing. `PlaybackCoordinator.scheduleWindow`
    /// already clamps the audio frame, but an unclamped target means the
    /// scrubber and the transport disagree about where playback is until the
    /// next tick corrects one of them.
    ///
    /// `position + delta` is reported rather than computed directly: the
    /// position comes off the engine and the duration off the manifest, and
    /// an addition of two `Int`s is exactly as trappable here as the
    /// subtraction in `remainingLabel`. An overflow means the skip ran off
    /// the end it was heading for, so that end is the answer.
    static func skipTarget(positionMs: Int, byMs delta: Int, durationMs: Int) -> Int {
        let duration = max(durationMs, 0)
        let position = min(max(positionMs, 0), duration)
        let (sum, overflowed) = position.addingReportingOverflow(delta)
        guard !overflowed else { return delta > 0 ? duration : 0 }
        return min(max(sum, 0), duration)
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
