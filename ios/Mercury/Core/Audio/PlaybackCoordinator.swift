import AVFoundation
import Observation
import SwiftUI

/// Drives transcript sync and chapter highlighting from playback position.
///
/// We use AVAudioEngine rather than AVPlayer because AVPlayer's position
/// reporting is not precise enough to keep large on-screen text in sync with
/// speech. AVAudioEngine gives sample-accurate frame positions.
@Observable
final class PlaybackCoordinator: VoiceInterruptiblePlayback {

    private(set) var currentTurn: Turn?
    private(set) var currentSegment: BriefingSegment?
    private(set) var isPlaying = false
    private(set) var positionMs: Int = 0
    private(set) var rate: Float = 1.0

    /// Playback speeds exposed by the transport control (PRD 3.4, Zone 3).
    static let availableRates: [Float] = [1.0, 1.25, 1.5, 2.0]

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let timePitch = AVAudioUnitTimePitch()
    private var manifest: BriefingManifest?
    private var file: AVAudioFile?
    private var displayLink: CADisplayLink?
    private let nowPlaying = NowPlayingService()

    /// Whether a segment is currently scheduled on `player`. `AVAudioPlayerNode`
    /// queues buffers rather than replacing them, so calling `scheduleFile`
    /// or `scheduleSegment` again while something is already queued would
    /// enqueue a second, overlapping playback instead of resuming. `play()`
    /// only (re)schedules when nothing is queued; otherwise it just resumes
    /// the paused node.
    private var hasScheduled = false

    /// Absolute file position, in ms, of the start of whatever is currently
    /// scheduled on `player`. `playerTime(forNodeTime:)` reports time
    /// relative to the start of the *current* schedule, not the file, so
    /// this offset must be added back on every tick to get an absolute
    /// position — see `tick()`.
    private var scheduleBaseMs = 0

    // MARK: - Loading

    func load(manifest: BriefingManifest, audioURL: URL) throws {
        // Loading a second briefing into an already-loaded coordinator: stop
        // and clear whatever the previous file had scheduled so state
        // doesn't leak across briefings.
        if file != nil {
            player.stop()
        }
        hasScheduled = false
        scheduleBaseMs = 0
        positionMs = 0
        currentTurn = nil
        currentSegment = nil

        self.manifest = manifest
        self.file = try AVAudioFile(forReading: audioURL)

        guard let file else { return }

        // player -> timePitch -> mainMixer so rate control (1x/1.25x/1.5x/2x)
        // doesn't shift pitch, since AVAudioPlayerNode has no rate of its own.
        // Attaching/connecting an already-attached node is a no-op error we
        // guard against so `load()` is safe to call more than once.
        if player.engine == nil {
            engine.attach(player)
        }
        if timePitch.engine == nil {
            engine.attach(timePitch)
        }
        engine.disconnectNodeOutput(player)
        engine.connect(player, to: timePitch, format: file.processingFormat)
        engine.connect(timePitch, to: engine.mainMixerNode,
                       format: file.processingFormat)

        try AVAudioSession.sharedInstance().setCategory(
            .playback, mode: .spokenAudio, options: [.allowBluetooth]
        )
        try AVAudioSession.sharedInstance().setActive(true)
        if !engine.isRunning {
            try engine.start()
        }

        nowPlaying.configureRemoteCommands(
            onPlay: { [weak self] in self?.play() },
            onPause: { [weak self] in self?.pause() },
            onSeek: { [weak self] ms in self?.seek(toMs: ms) }
        )
        nowPlaying.update(title: "Your briefing", durationMs: manifest.durationMs,
                          positionMs: 0, rate: rate, isPlaying: false)
    }

    // MARK: - Transport

    func play() {
        guard let file else { return }
        if !hasScheduled {
            player.scheduleFile(file, at: nil)
            scheduleBaseMs = 0
            hasScheduled = true
        }
        player.play()
        isPlaying = true
        startTicking()
        nowPlaying.setPlaybackState(isPlaying: true, positionMs: positionMs, rate: rate)
    }

    func pause() {
        player.pause()
        isPlaying = false
        stopTicking()
        nowPlaying.setPlaybackState(isPlaying: false, positionMs: positionMs, rate: rate)
    }

    /// Cycles 1x -> 1.25x -> 1.5x -> 2x -> 1x.
    func cycleRate() {
        let rates = Self.availableRates
        let currentIndex = rates.firstIndex(of: rate) ?? 0
        setRate(rates[(currentIndex + 1) % rates.count])
    }

    func setRate(_ newRate: Float) {
        rate = newRate
        timePitch.rate = newRate
        nowPlaying.setPlaybackState(isPlaying: isPlaying, positionMs: positionMs, rate: rate)
    }

    /// PRD 3.5: "fade briefing audio to 15 percent over 120 milliseconds".
    /// Shared with `JoinCoordinator`, which needs to know how long the fade
    /// takes to know when it's safe to freeze the exact resume position —
    /// a single source of truth beats two files agreeing on `0.12` by
    /// coincidence.
    static let duckLevel: Float = 0.15
    static let duckSeconds: TimeInterval = 0.12

    /// Ducks rather than stops. Used by the voice interrupt — a hard stop
    /// reads as a bug, a fade reads as the hosts pausing to listen.
    ///
    /// `AVAudioMixerNode.outputVolume` has no native ramp of its own — unlike
    /// e.g. `AVAudioUnitEQ` there is no `rampTo(gain:duration:)` on the main
    /// mixer, so this steps the value by hand on a short-interval `Task`,
    /// using `Self.rampedVolume` (a pure function, see its doc comment) for
    /// the actual interpolation so the maths can be unit tested without
    /// waiting on real time or a running `AVAudioEngine`.
    func duck(to level: Float = PlaybackCoordinator.duckLevel, over seconds: TimeInterval = PlaybackCoordinator.duckSeconds) {
        rampVolume(to: level, over: seconds)
    }

    func restoreVolume(over seconds: TimeInterval = PlaybackCoordinator.duckSeconds) {
        rampVolume(to: 1.0, over: seconds)
    }

    /// How long between volume steps while ramping. 12ms gives a 120ms ramp
    /// ~10 steps — smooth enough to read as a fade rather than a zipper of
    /// discrete jumps, without waking the CPU absurdly often.
    private static let rampStepInterval: TimeInterval = 0.012

    private var volumeRampTask: Task<Void, Never>?

    private func rampVolume(to target: Float, over seconds: TimeInterval) {
        volumeRampTask?.cancel()

        let start = engine.mainMixerNode.outputVolume
        guard seconds > 0 else {
            engine.mainMixerNode.outputVolume = target
            return
        }

        let startedAt = DispatchTime.now()
        let mixer = engine.mainMixerNode
        volumeRampTask = Task { @MainActor [mixer] in
            while true {
                if Task.isCancelled { return }
                let elapsed = TimeInterval(DispatchTime.now().uptimeNanoseconds
                                            - startedAt.uptimeNanoseconds) / 1_000_000_000
                mixer.outputVolume = Self.rampedVolume(
                    start: start, target: target, elapsed: elapsed, duration: seconds
                )
                if elapsed >= seconds { return }
                try? await Task.sleep(nanoseconds: UInt64(Self.rampStepInterval * 1_000_000_000))
            }
        }
    }

    /// Linear interpolation between `start` and `target` at `elapsed` of
    /// `duration` seconds. Pure and side-effect free on purpose: this is the
    /// one piece of the duck/restore behaviour that can be checked with exact
    /// arithmetic rather than by observing a running ramp, which is what the
    /// "volume ramp maths" tests in `PlaybackCoordinatorTests` exercise.
    ///
    /// Clamps `elapsed` to `[0, duration]` so a caller that ticks slightly
    /// past the end (as the real ramp loop's last step usually does) still
    /// lands exactly on `target`, never overshoots it.
    static func rampedVolume(start: Float, target: Float, elapsed: TimeInterval, duration: TimeInterval) -> Float {
        guard duration > 0 else { return target }
        let clamped = min(max(elapsed / duration, 0), 1)
        return start + (target - start) * Float(clamped)
    }

    /// Read-only window onto the live mixer volume, for
    /// `PlaybackCoordinatorTests` to assert what `duck`/`restoreVolume`
    /// actually landed on. Deliberately not part of the transport API used
    /// by the rest of the app — nothing outside tests should reach for raw
    /// mixer volume instead of going through `duck`/`restoreVolume`.
    var outputVolume: Float { engine.mainMixerNode.outputVolume }

    func seek(toMs ms: Int) {
        guard let file else { return }
        let sampleRate = file.processingFormat.sampleRate
        let frame = AVAudioFramePosition(Double(ms) / 1000.0 * sampleRate)
        let remaining = AVAudioFrameCount(file.length - frame)
        guard remaining > 0 else { return }

        player.stop()
        player.scheduleSegment(file, startingFrame: frame,
                               frameCount: remaining, at: nil)
        hasScheduled = true
        scheduleBaseMs = ms
        player.play()
        isPlaying = true
        positionMs = ms
        nowPlaying.setPlaybackState(isPlaying: true, positionMs: ms, rate: rate)
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

        let ms = scheduleBaseMs + Int(Double(playerTime.sampleTime)
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
