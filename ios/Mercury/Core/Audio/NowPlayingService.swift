import MediaPlayer

/// Lock-screen / Control Center integration: `MPNowPlayingInfoCenter` for
/// display and `MPRemoteCommandCenter` for play/pause/seek from outside the
/// app. Required so audio keeps playing (and stays controllable) when the
/// phone is backgrounded and locked — see `UIBackgroundModes: [audio]` in
/// Info.plist and `AVAudioSession.Category.playback` in
/// `PlaybackCoordinator.load`.
final class NowPlayingService {

    private let center = MPNowPlayingInfoCenter.default()
    private let commandCenter = MPRemoteCommandCenter.shared()
    private var configured = false

    func configureRemoteCommands(
        onPlay: @escaping () -> Void,
        onPause: @escaping () -> Void,
        onSeek: @escaping (Int) -> Void
    ) {
        guard !configured else { return }
        configured = true

        commandCenter.playCommand.addTarget { _ in
            onPlay()
            return .success
        }
        commandCenter.pauseCommand.addTarget { _ in
            onPause()
            return .success
        }
        commandCenter.changePlaybackPositionCommand.addTarget { event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            onSeek(Int(event.positionTime * 1000))
            return .success
        }
    }

    /// Artwork, built once and handed to every update.
    ///
    /// `MPMediaItemArtwork` asks for the image at whatever size the surface
    /// wants — the lock screen, Control Center and CarPlay all ask
    /// differently — so this draws on demand rather than scaling one bitmap.
    private lazy var artwork = MPMediaItemArtwork(
        boundsSize: CGSize(width: 1024, height: 1024)
    ) { size in
        MercuryArtwork.image(side: max(size.width, size.height))
    }

    /// The whole card: what is playing, which briefing it belongs to, and
    /// the mark.
    ///
    /// `title` is the SEGMENT, not the briefing. This is the line a podcast
    /// app uses for the episode, and the briefing's own name is already on
    /// the line below it — putting "Your briefing" in both said nothing
    /// twice and left the lock screen unchanged for eight minutes while the
    /// audio moved through five different subjects.
    func update(
        title: String,
        briefing: String,
        durationMs: Int,
        positionMs: Int,
        rate: Float,
        isPlaying: Bool
    ) {
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyArtist: briefing,
            // Album is what the Lock Screen shows in small type under the
            // artist, and what CarPlay groups by.
            MPMediaItemPropertyAlbumTitle: "Mercury",
            MPMediaItemPropertyPlaybackDuration: Double(durationMs) / 1000.0,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: Double(positionMs) / 1000.0,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? Double(rate) : 0.0,
        ]
        info[MPMediaItemPropertyArtwork] = artwork
        info[MPMediaItemPropertyMediaType] = MPMediaType.podcast.rawValue
        center.nowPlayingInfo = info
    }

    /// Change the line that names what is playing, keeping everything else.
    ///
    /// A segment boundary must not rebuild the whole dictionary: doing that
    /// re-sends duration and elapsed time, and iOS treats a fresh
    /// `nowPlayingInfo` as a new item — the scrubber jumps back to the start
    /// of the bar for a frame each time a chapter changes.
    func setTitle(_ title: String) {
        guard var info = center.nowPlayingInfo, info[MPMediaItemPropertyTitle] as? String != title
        else { return }
        info[MPMediaItemPropertyTitle] = title
        center.nowPlayingInfo = info
    }

    func setPlaybackState(isPlaying: Bool, positionMs: Int, rate: Float) {
        var info = center.nowPlayingInfo ?? [:]
        info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = Double(positionMs) / 1000.0
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? Double(rate) : 0.0
        center.nowPlayingInfo = info
    }

    /// Take the card off the lock screen.
    ///
    /// **Why rate 0 was not enough.** Everything above expresses "stopped"
    /// as `MPNowPlayingInfoPropertyPlaybackRate = 0.0` — but that is how
    /// iOS spells PAUSED, not stopped. A paused card stays on the lock
    /// screen with a play button, which is correct while the user is
    /// halfway through and wrong once the sign-off has played: the briefing
    /// is over and Mercury was still presenting itself as a podcast waiting
    /// to be resumed.
    ///
    /// `nowPlayingInfo = nil` is what removes it. `playbackState` is set
    /// too, and explicitly: on iOS the system infers state from the rate
    /// unless told, and leaving it inferred is what produced the paused
    /// card in the first place. Set it BEFORE nilling the dictionary —
    /// after, there is no item for the state to describe.
    func clear() {
        center.playbackState = .stopped
        center.nowPlayingInfo = nil
    }
}
