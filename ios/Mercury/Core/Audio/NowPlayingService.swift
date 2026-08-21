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

    func update(title: String, durationMs: Int, positionMs: Int, rate: Float, isPlaying: Bool) {
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyPlaybackDuration: Double(durationMs) / 1000.0,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: Double(positionMs) / 1000.0,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? Double(rate) : 0.0,
        ]
        info[MPMediaItemPropertyMediaType] = MPMediaType.podcast.rawValue
        center.nowPlayingInfo = info
    }

    func setPlaybackState(isPlaying: Bool, positionMs: Int, rate: Float) {
        var info = center.nowPlayingInfo ?? [:]
        info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = Double(positionMs) / 1000.0
        info[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? Double(rate) : 0.0
        center.nowPlayingInfo = info
    }
}
