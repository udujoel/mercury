import AVFoundation
import Observation

/// Drives transcript sync and chapter highlighting from playback position.
///
/// We use AVAudioEngine rather than AVPlayer because AVPlayer's position
/// reporting is not precise enough to keep large on-screen text in sync with
/// speech. AVAudioEngine gives sample-accurate frame positions.
@Observable
final class PlaybackCoordinator {

    private(set) var currentTurn: Turn?
    private(set) var currentSegment: BriefingSegment?
    private(set) var isPlaying = false
    private(set) var positionMs: Int = 0

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var manifest: BriefingManifest?
    private var file: AVAudioFile?
    private var displayLink: CADisplayLink?

    // MARK: - Loading

    func load(manifest: BriefingManifest, audioURL: URL) throws {
        self.manifest = manifest
        self.file = try AVAudioFile(forReading: audioURL)

        guard let file else { return }

        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode,
                       format: file.processingFormat)

        try AVAudioSession.sharedInstance().setCategory(
            .playback, mode: .spokenAudio, options: [.allowBluetooth]
        )
        try AVAudioSession.sharedInstance().setActive(true)
        try engine.start()
    }

    // MARK: - Transport

    func play() {
        guard let file else { return }
        player.scheduleFile(file, at: nil)
        player.play()
        isPlaying = true
        startTicking()
    }

    func pause() {
        player.pause()
        isPlaying = false
        stopTicking()
    }

    /// Ducks rather than stops. Used by the voice interrupt — a hard stop
    /// reads as a bug, a fade reads as the hosts pausing to listen.
    func duck(to level: Float = 0.15, over seconds: TimeInterval = 0.12) {
        // Ramp mainMixerNode.outputVolume over `seconds`.
    }

    func restoreVolume(over seconds: TimeInterval = 0.12) {
        // Ramp back to 1.0.
    }

    func seek(toMs ms: Int) {
        guard let file else { return }
        let rate = file.processingFormat.sampleRate
        let frame = AVAudioFramePosition(Double(ms) / 1000.0 * rate)
        let remaining = AVAudioFrameCount(file.length - frame)
        guard remaining > 0 else { return }

        player.stop()
        player.scheduleSegment(file, startingFrame: frame,
                               frameCount: remaining, at: nil)
        player.play()
        isPlaying = true
    }

    func seek(toSegment id: String) {
        guard let seg = manifest?.segments.first(where: { $0.id == id }) else {
            return
        }
        seek(toMs: seg.startMs)
    }

    // MARK: - Sync

    private func startTicking() {
        displayLink = CADisplayLink(target: self, selector: #selector(tick))
        displayLink?.preferredFramesPerSecond = 20
        displayLink?.add(to: .main, forMode: .common)
    }

    private func stopTicking() {
        displayLink?.invalidate()
        displayLink = nil
    }

    @objc private func tick() {
        guard
            let manifest,
            let nodeTime = player.lastRenderTime,
            let playerTime = player.playerTime(forNodeTime: nodeTime)
        else { return }

        let ms = Int(Double(playerTime.sampleTime)
                     / playerTime.sampleRate * 1000)
        positionMs = ms

        // Binary search — turns are sorted by startMs, so this is O(log n)
        // and safe to run at 20 Hz on the main actor.
        guard let turn = manifest.turn(containing: ms),
              turn.id != currentTurn?.id else { return }

        withAnimation(.easeInOut(duration: 0.22)) {
            currentTurn = turn
            currentSegment = manifest.segment(for: turn)
        }
    }
}
