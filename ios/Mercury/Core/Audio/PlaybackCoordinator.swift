import AVFoundation
import Observation
import SwiftUI

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
    private(set) var rate: Float = 1.0

    /// The briefing played to the end. Nothing used to set this because
    /// nothing ever ended playback: `scheduleFile` was called with no
    /// completion handler at all, so the node drained, the display link
    /// kept firing, `isPlaying` stayed true and the transport sat there
    /// claiming to play silence until the app was killed.
    private(set) var hasFinished = false

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

    /// Bumped on every (re)schedule and on every deliberate stop.
    ///
    /// `AVAudioPlayerNode` fires a segment's completion handler when the
    /// node is STOPPED as well as when its audio drains — so a seek, a
    /// reload, or an engine teardown would otherwise all look exactly like
    /// "the briefing ended". Each handler captures the generation it was
    /// scheduled under and does nothing if it has since been superseded.
    private var scheduleGeneration = 0

    /// Whether playback was running when an interruption began, so
    /// `.shouldResume` only resumes something that was actually playing.
    private var wasPlayingBeforeInterruption = false

    /// Token handles for the audio-session notifications, removed in
    /// `deinit`.
    private var sessionObservers: [NSObjectProtocol] = []

    /// Absolute file position, in ms, of the start of whatever is currently
    /// scheduled on `player`. `playerTime(forNodeTime:)` reports time
    /// relative to the start of the *current* schedule, not the file, so
    /// this offset must be added back on every tick to get an absolute
    /// position — see `tick()`.
    private var scheduleBaseMs = 0

    init() {
        observeAudioSessionEvents()
    }

    deinit {
        // A `CADisplayLink` added to a run loop is retained by that run
        // loop, and it retains its target. Without this, a coordinator that
        // went out of scope mid-playback left a live link firing forever.
        stopTicking()
        sessionObservers.forEach(NotificationCenter.default.removeObserver)
    }

    // MARK: - Loading

    func load(manifest: BriefingManifest, audioURL: URL) throws {
        // Loading a second briefing into an already-loaded coordinator: stop
        // and clear whatever the previous file had scheduled so state
        // doesn't leak across briefings.
        if file != nil {
            // Bump BEFORE stopping: `stop()` fires the completion handler of
            // whatever was scheduled, and that handler must not be mistaken
            // for the previous briefing having ended.
            scheduleGeneration &+= 1
            player.stop()
        }
        stopTicking()
        hasScheduled = false
        hasFinished = false
        isPlaying = false
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
        guard file != nil else { return }

        // Nothing queued, or the last schedule already drained. A finished
        // briefing replays from the top; anything else picks up where the
        // reported position left off.
        if !hasScheduled || hasFinished {
            schedule(fromMs: hasFinished ? 0 : positionMs, startPlaying: true)
            return
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

    /// Seeking PRESERVES the transport state.
    ///
    /// This used to call `player.play()` and set `isPlaying = true`
    /// unconditionally, so scrubbing or tapping a chapter tab while paused
    /// started the audio playing — and, because `pause()` had already torn
    /// the display link down and nothing restarted it, did so with a
    /// transcript frozen for the rest of the session.
    func seek(toMs ms: Int) {
        guard file != nil else { return }
        let wasPlaying = isPlaying
        if !schedule(fromMs: ms, startPlaying: wasPlaying) {
            // At or past the end: there is nothing left to schedule. Land on
            // the end rather than no-op, so the transport does not keep
            // claiming to play a briefing that has no audio left.
            finish()
        }
    }

    func seek(toSegment id: String) {
        guard let seg = manifest?.segments.first(where: { $0.id == id }) else {
            return
        }
        seek(toMs: seg.startMs)
    }

    // MARK: - Scheduling

    /// Frame arithmetic for a seek, as a pure function.
    ///
    /// The crash this replaces: `AVAudioFrameCount(file.length - frame)`
    /// computed the remaining frames in the SIGNED domain and then converted
    /// to an unsigned type. Any seek past the end of the file — a chapter
    /// tab on a truncated download, a lock-screen scrub, a manifest whose
    /// `duration_ms` runs slightly past the audio — made that difference
    /// negative, and `AVAudioFrameCount(-1)` traps. The `remaining > 0`
    /// guard was one line too late to help.
    ///
    /// Clamping happens entirely in the signed domain, before any
    /// conversion. Returns `nil` when the position is at or past the end,
    /// i.e. when there is genuinely nothing left to schedule.
    ///
    /// Pure and engine-free specifically so it can be tested past the end of
    /// the file without a simulator, an `AVAudioFile`, or real audio
    /// hardware — which is why the crash survived a green suite.
    static func scheduleWindow(
        forMs ms: Int,
        fileLength: AVAudioFramePosition,
        sampleRate: Double
    ) -> (startFrame: AVAudioFramePosition, frameCount: AVAudioFrameCount, positionMs: Int)? {
        guard sampleRate > 0, fileLength > 0 else { return nil }

        let requested = (Double(max(ms, 0)) / 1000.0 * sampleRate).rounded()
        // Clamp in Double first: a large enough `ms` overflows
        // AVAudioFramePosition (Int64) on conversion, which also traps.
        let startFrame = AVAudioFramePosition(min(max(requested, 0), Double(fileLength)))
        let remaining = fileLength - startFrame
        guard remaining > 0 else { return nil }

        let positionMs = Int((Double(startFrame) / sampleRate * 1000).rounded())
        return (startFrame, AVAudioFrameCount(remaining), positionMs)
    }

    /// (Re)schedules the file from `ms` and sets the transport accordingly.
    /// Returns false when `ms` is at or past the end of the audio.
    @discardableResult
    private func schedule(fromMs ms: Int, startPlaying: Bool) -> Bool {
        guard let file,
              let window = Self.scheduleWindow(
                  forMs: ms,
                  fileLength: file.length,
                  sampleRate: file.processingFormat.sampleRate
              )
        else { return false }

        // Bump before `stop()`, which fires the outgoing schedule's
        // completion handler.
        scheduleGeneration &+= 1
        let generation = scheduleGeneration
        player.stop()

        // `.dataPlayedBack` — not `.dataRendered` — is the only callback
        // type that means the audio actually reached the output. Without
        // any completion callback at all (the previous `scheduleFile(_:at:)`
        // call), nothing in the app ever learned that the briefing ended.
        player.scheduleSegment(
            file,
            startingFrame: window.startFrame,
            frameCount: window.frameCount,
            at: nil,
            completionCallbackType: .dataPlayedBack
        ) { [weak self] _ in
            // Delivered on an AVAudioEngine-internal queue.
            DispatchQueue.main.async { self?.playbackDidComplete(generation: generation) }
        }

        hasScheduled = true
        hasFinished = false
        scheduleBaseMs = window.positionMs
        positionMs = window.positionMs
        resolveTurn(atMs: window.positionMs)

        if startPlaying {
            player.play()
            isPlaying = true
            startTicking()
        } else {
            isPlaying = false
            stopTicking()
        }
        nowPlaying.setPlaybackState(
            isPlaying: startPlaying, positionMs: window.positionMs, rate: rate
        )
        return true
    }

    /// How far from the end a completion callback still counts as "the
    /// audio drained". One 20 Hz tick is 50 ms of wall clock, up to 100 ms
    /// of audio at 2x, so a second is comfortable margin.
    static let endOfPlaybackToleranceMs = 1_000

    /// Distinguishes "the audio drained" from "something stopped the node".
    ///
    /// A completion handler also fires when the engine is torn down, which
    /// is exactly what an audio-session interruption does. Treating that as
    /// the end of the briefing would clear the transport mid-sentence and
    /// make the interruption unresumable. Pure, so the boundary is testable
    /// without an engine.
    static func isEndOfPlayback(
        positionMs: Int,
        audioDurationMs: Int,
        toleranceMs: Int = endOfPlaybackToleranceMs
    ) -> Bool {
        guard audioDurationMs > 0 else { return true }
        return positionMs >= audioDurationMs - toleranceMs
    }

    private func playbackDidComplete(generation: Int) {
        guard generation == scheduleGeneration else { return }
        guard Self.isEndOfPlayback(
            positionMs: positionMs, audioDurationMs: audioDurationMs
        ) else { return }
        finish()
    }

    /// Length of the loaded audio, from the FILE rather than the manifest.
    /// The manifest's `duration_ms` is the stitcher's arithmetic; the file
    /// is the thing that actually stops.
    private var audioDurationMs: Int {
        guard let file, file.processingFormat.sampleRate > 0 else { return 0 }
        return Int(Double(file.length) / file.processingFormat.sampleRate * 1000)
    }

    /// The explicit finished state: node stopped, transport idle, position
    /// clamped to the end, ticking stopped, Now Playing told.
    private func finish() {
        scheduleGeneration &+= 1
        player.stop()
        hasScheduled = false
        hasFinished = true
        isPlaying = false
        stopTicking()
        let end = audioDurationMs > 0 ? audioDurationMs : (manifest?.durationMs ?? positionMs)
        positionMs = max(0, end)
        scheduleBaseMs = positionMs
        nowPlaying.setPlaybackState(isPlaying: false, positionMs: positionMs, rate: rate)
    }

    // MARK: - Interruptions and route changes

    private func observeAudioSessionEvents() {
        guard sessionObservers.isEmpty else { return }
        let center = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()

        sessionObservers.append(
            center.addObserver(
                forName: AVAudioSession.interruptionNotification,
                object: session, queue: .main
            ) { [weak self] note in
                self?.handleInterruption(note)
            }
        )

        sessionObservers.append(
            center.addObserver(
                forName: AVAudioSession.routeChangeNotification,
                object: session, queue: .main
            ) { [weak self] note in
                self?.handleRouteChange(note)
            }
        )
    }

    /// A phone call, a Siri invocation, an alarm.
    ///
    /// Without this the system stopped the audio and the app never found
    /// out: `isPlaying` stayed true, the play/pause button showed pause, and
    /// tapping it called `player.pause()` on a node that was already
    /// silent. The player was simply dead for the rest of the session.
    func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw)
        else { return }

        switch type {
        case .began:
            wasPlayingBeforeInterruption = isPlaying
            if isPlaying { pause() }
        case .ended:
            guard let optionsRaw =
                    note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
            else { return }
            let options = AVAudioSession.InterruptionOptions(rawValue: optionsRaw)
            // `.shouldResume` is the system's permission to start again. It
            // is absent when the user chose something else — resuming anyway
            // would talk over whatever they picked.
            guard options.contains(.shouldResume), wasPlayingBeforeInterruption else {
                wasPlayingBeforeInterruption = false
                return
            }
            wasPlayingBeforeInterruption = false
            resumeAfterInterruption()
        @unknown default:
            break
        }
    }

    /// PRIVACY, not convenience.
    ///
    /// When headphones or a Bluetooth device disappear, iOS reroutes to the
    /// built-in speaker. A Mercury briefing is the user's inbox and calendar
    /// read out loud, so an unplugged pair of headphones would broadcast
    /// their email to the room. Pause; never resume automatically when a
    /// device appears.
    func handleRouteChange(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: raw),
              reason == .oldDeviceUnavailable
        else { return }
        if isPlaying { pause() }
    }

    private func resumeAfterInterruption() {
        do {
            try AVAudioSession.sharedInstance().setActive(true)
            if !engine.isRunning { try engine.start() }
        } catch {
            print("[Mercury] Could not reactivate audio after interruption: \(error)")
            return
        }
        // An interruption tears the engine down, which flushes the player
        // node — so this is a fresh schedule from where we stopped, not a
        // `player.play()` on something still queued.
        schedule(fromMs: positionMs, startPlaying: true)
    }

    // MARK: - Sync

    /// Holds the coordinator WEAKLY.
    ///
    /// `CADisplayLink` retains its target for as long as it is on a run
    /// loop, and `startTicking()` used to build a new one on every call
    /// without invalidating the last. Two taps on play left two links
    /// running, each retaining the coordinator, neither reachable to be
    /// stopped — so the coordinator (and its `AVAudioEngine`) leaked and
    /// kept ticking for the lifetime of the process. Idempotence below
    /// fixes the orphaning; this proxy makes a link that somehow outlives
    /// its owner harmless rather than resurrecting it.
    private final class DisplayLinkProxy {
        weak var coordinator: PlaybackCoordinator?

        init(coordinator: PlaybackCoordinator) {
            self.coordinator = coordinator
        }

        @objc func tick() {
            coordinator?.tick()
        }
    }

    private func startTicking() {
        // Idempotent: a second call while a link is live is a no-op, not a
        // second link.
        guard displayLink == nil else { return }
        let proxy = DisplayLinkProxy(coordinator: self)
        let link = CADisplayLink(target: proxy, selector: #selector(DisplayLinkProxy.tick))
        link.preferredFramesPerSecond = 20
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    private func stopTicking() {
        displayLink?.invalidate()
        displayLink = nil
    }

    fileprivate func tick() {
        guard
            let nodeTime = player.lastRenderTime,
            let playerTime = player.playerTime(forNodeTime: nodeTime)
        else { return }

        let ms = scheduleBaseMs + Int(Double(playerTime.sampleTime)
                     / playerTime.sampleRate * 1000)
        positionMs = ms
        resolveTurn(atMs: ms, animated: true)
    }

    /// Moves the transcript and chapter to whatever covers `ms`.
    ///
    /// Called from the tick AND immediately after a seek — waiting for the
    /// next tick meant a seek while paused never updated the transcript at
    /// all, because a paused coordinator has no display link.
    private func resolveTurn(atMs ms: Int, animated: Bool = false) {
        guard let manifest else { return }

        // O(log n) binary search over the pre-computed rendered list; safe
        // to run at 20 Hz on the main actor.
        guard let turn = manifest.turn(containing: ms) ?? manifest.turn(startingAtOrAfter: ms),
              turn.id != currentTurn?.id
        else { return }

        let apply = {
            self.currentTurn = turn
            self.currentSegment = manifest.segment(for: turn)
        }
        if animated {
            withAnimation(.easeInOut(duration: 0.22), apply)
        } else {
            apply()
        }
    }
}
