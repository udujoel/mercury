import Foundation

/// The slice of `PlaybackCoordinator` that `JoinCoordinator` actually needs:
/// duck/restore the briefing's volume, and pause/resume it without ever
/// seeking. `JoinCoordinator` depends on this protocol rather than the
/// concrete `PlaybackCoordinator` specifically so its orchestration —
/// exactly when duck/pause/restore/play are called, and in what order — can
/// be unit tested against a scripted fake instead of a running
/// `AVAudioEngine`, which needs real audio hardware (or at least a working
/// simulator audio route) to do anything meaningful. See
/// `docs/phase3/ios_join.md` for what that means was and wasn't verified by
/// the test suite.
///
/// `PlaybackCoordinator` conforms directly (`final class PlaybackCoordinator:
/// VoiceInterruptiblePlayback`) — the default parameter values on its
/// `duck`/`restoreVolume` methods are call-site sugar only and don't need to
/// (can't) appear in the protocol requirement.
protocol VoiceInterruptiblePlayback: AnyObject {
    var positionMs: Int { get }
    func duck(to level: Float, over seconds: TimeInterval)
    func restoreVolume(over seconds: TimeInterval)
    func pause()
    func play()
}
