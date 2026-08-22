import Foundation
import Observation

/// Orchestrates one full Join interaction end to end: drives
/// `JoinStateMachine`, ducks/pauses/resumes `PlaybackCoordinator`, runs
/// `SpeechRecognizing`, calls `VoiceAskClientProtocol`, plays the answer
/// through `AnswerPlaying`, and instruments every PRD 3.5 timing stage via
/// `JoinTimingRecorder`.
///
/// Every collaborator is injected behind a protocol specifically so this
/// class — the actual state-machine-driving *logic* — can be unit tested
/// with fakes standing in for the microphone and the network, neither of
/// which are available (or, for the network, appropriate) in a test target.
/// See `JoinCoordinatorTests`.
@Observable
final class JoinCoordinator {

    /// Mirrors `machine.state`. Kept as its own `@Observable`-tracked stored
    /// property (rather than a computed pass-through to `machine.state`) and
    /// synced explicitly after every `machine.send(_:)` that returns `true`,
    /// so SwiftUI views observing `JoinCoordinator` re-render on every
    /// legal transition without also depending on `JoinStateMachine`
    /// itself being `@Observable`-tracked through a second layer.
    private(set) var state: JoinState = .idle

    /// Live partial transcript while `.listening`, for UI feedback. Cleared
    /// at the start of every tap.
    private(set) var partialTranscript: String = ""
    /// Set (non-nil) when a Join attempt fails — denied permission, network
    /// error, empty transcript. Cleared at the start of every tap. Surfaced
    /// so the UI can show *something* rather than silently snapping back to
    /// idle with no explanation.
    private(set) var lastError: String?

    /// How long `.listening` waits for a final transcript before treating
    /// the interrupt as abandoned. Not in the PRD 3.5 budget table (that
    /// table is about how *fast* the happy path is, not how long to wait for
    /// a silent user) — chosen generously since a false timeout mid-question
    /// is a worse failure than a slightly slow give-up.
    static let listeningTimeoutSeconds: TimeInterval = 8

    private let machine = JoinStateMachine()
    private let playback: VoiceInterruptiblePlayback
    private let speech: SpeechRecognizing
    private let voiceAsk: VoiceAskClientProtocol
    private let answerPlayer: AnswerPlaying
    private let timing: JoinTimingRecorder
    private var briefingId: () -> String?
    private var positionMs: () -> Int

    private var priorQuestions: [String] = []
    private var timeoutTask: Task<Void, Never>?
    private var askTask: Task<Void, Never>?
    private var freezeTask: Task<Void, Never>?
    /// Whether `playback` is actually paused right now for this interrupt.
    /// Only true once `freezeTask` has run — an interaction that resolves
    /// faster than the duck fade (tap-again within ~120ms, say) never
    /// actually pauses the engine, so `finishResuming()` must not blindly
    /// call `play()` in that case; see `scheduleFreezeAfterDuck`.
    private var isPausedForInterrupt = false

    init(
        playback: VoiceInterruptiblePlayback,
        speech: SpeechRecognizing = SFSpeechRecognitionService(),
        voiceAsk: VoiceAskClientProtocol = VoiceAskClient(),
        answerPlayer: AnswerPlaying = AnswerAudioPlayer(),
        timing: JoinTimingRecorder = JoinTimingRecorder(),
        briefingId: @escaping () -> String? = { nil },
        positionMs: @escaping () -> Int = { 0 }
    ) {
        self.playback = playback
        self.speech = speech
        self.voiceAsk = voiceAsk
        self.answerPlayer = answerPlayer
        self.timing = timing
        self.briefingId = briefingId
        self.positionMs = positionMs

        answerPlayer.onFirstChunkStarted = { [weak self] in
            self?.timing.markAudioPlaying()
        }
        answerPlayer.onFinished = { [weak self] in
            self?.handleAnswerFinished()
        }
    }

    /// Wires the closures that read the live briefing id/playback position.
    /// Split out from `init` rather than taking them as required init
    /// parameters because `AppState` needs to pass `self`-capturing
    /// closures, and Swift's definite-initialization check will not allow
    /// that inside the very initializer call that constructs the property
    /// holding them — see `AppState.init` for the call site. Safe to call
    /// more than once; the second call just replaces the closures.
    func configure(briefingId: @escaping () -> String?, positionMs: @escaping () -> Int) {
        self.briefingId = briefingId
        self.positionMs = positionMs
    }

    // MARK: - Entry point

    /// The mic button calls exactly this, every time, regardless of state —
    /// `JoinStateMachine` is what decides whether a tap right now means
    /// "start listening" (from `.idle`), "cancel" (from `.listening`), or
    /// nothing at all (any other state, e.g. `.answering` — PRD acceptance
    /// requirement: a tap during the answer must not re-enter `.listening`).
    @MainActor
    func tap() {
        switch machine.state {
        case .idle:
            beginListening()
        case .listening:
            cancel(via: .tapMic)
        case .thinking, .answering, .resuming:
            break
        }
    }

    // MARK: - idle → listening

    @MainActor
    private func beginListening() {
        timing.reset()
        timing.markTap()
        guard machine.send(.tapMic) else { return }
        state = machine.state
        timing.markListeningUIVisible()

        partialTranscript = ""
        lastError = nil

        playback.duck(to: PlaybackCoordinator.duckLevel, over: PlaybackCoordinator.duckSeconds)
        timing.markDucked()
        scheduleFreezeAfterDuck()

        armTimeout()
        requestSpeech()
    }

    /// Waits out the duck fade, then pauses the engine so the briefing holds
    /// at a fixed, sample-accurate position for the rest of the interrupt
    /// instead of continuing to advance (quietly) underneath it — pausing
    /// immediately, before the fade is audible, would cut the fade short and
    /// read as a stop rather than a duck, which is exactly what PRD 3.5
    /// warns against.
    ///
    /// This is also *why* `PlaybackCoordinator.play()` alone is enough to
    /// satisfy "resume from the exact pause position": nothing seeks, ever —
    /// the position is simply wherever the engine happened to be paused.
    private func scheduleFreezeAfterDuck() {
        freezeTask?.cancel()
        freezeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(PlaybackCoordinator.duckSeconds * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.playback.pause()
            self.isPausedForInterrupt = true
        }
    }

    private func armTimeout() {
        timeoutTask?.cancel()
        timeoutTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.listeningTimeoutSeconds * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.cancel(via: .timedOut)
        }
    }

    /// Shared by the tap-again cancel and the timeout — both leave
    /// `.listening` the same way: no answer, straight to `.resuming`.
    @MainActor
    private func cancel(via event: JoinEvent) {
        timeoutTask?.cancel()
        freezeTask?.cancel()
        speech.stopListening()
        guard machine.send(event) else { return }
        state = machine.state
        finishResuming()
    }

    // MARK: - Speech

    @MainActor
    private func requestSpeech() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            let auth = await self.speech.requestAuthorization()
            guard self.machine.state == .listening else { return }
            guard auth == .authorized else {
                self.lastError = auth == .denied
                    ? "Microphone and Speech Recognition access are required for Join. "
                      + "Enable them in Settings → Privacy → Microphone / Speech Recognition."
                    : "Waiting on microphone permission."
                if auth == .denied {
                    self.cancel(via: .timedOut)
                }
                return
            }
            do {
                try self.speech.startListening { [weak self] event in
                    guard let self else { return }
                    Task { @MainActor in
                        self.handle(event)
                    }
                }
            } catch {
                self.lastError = error.localizedDescription
                self.cancel(via: .timedOut)
            }
        }
    }

    @MainActor
    private func handle(_ event: SpeechRecognitionEvent) {
        guard machine.state == .listening else { return }
        switch event {
        case .partial(let text):
            partialTranscript = text
        case .silenceStarted:
            timing.markSpeechEnded()
        case .final(let text):
            timing.markSpeechDetected()
            handleFinalTranscript(text)
        }
    }

    @MainActor
    private func handleFinalTranscript(_ text: String) {
        timeoutTask?.cancel()
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            lastError = "Didn't catch that — tap the mic to try again."
            cancel(via: .timedOut)
            return
        }
        guard machine.send(.speechEnded) else { return }
        state = machine.state
        partialTranscript = trimmed
        timing.markTranscriptReady()
        ask(question: trimmed)
    }

    // MARK: - thinking → answering

    @MainActor
    private func ask(question: String) {
        guard let briefingId = briefingId() else {
            lastError = "No briefing loaded to ask about."
            failAnswer()
            return
        }
        var firstEventSeen = false
        var answerTextParts: [String] = []

        askTask?.cancel()
        askTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let stream = self.voiceAsk.ask(
                    briefingId: briefingId, question: question,
                    positionMs: self.positionMs(), priorQuestions: self.priorQuestions
                )
                for try await chunk in stream {
                    if !firstEventSeen {
                        firstEventSeen = true
                        self.timing.markFirstToken()
                    }
                    guard self.machine.state == .thinking || self.machine.state == .answering else { return }

                    if chunk.kind == .answer, !chunk.text.isEmpty {
                        answerTextParts.append(chunk.text)
                    }

                    // Any chunk carrying playable audio moves thinking ->
                    // answering, regardless of kind — a bridge chunk could
                    // in principle be the first one with audio if the
                    // answer itself entirely failed to synthesise.
                    if !chunk.audioFailed, let audio = chunk.audio {
                        if self.machine.state == .thinking {
                            guard self.machine.send(.answerReady) else { return }
                            self.state = self.machine.state
                        }
                        self.answerPlayer.enqueue(audio: audio)
                    }
                }
                self.answerPlayer.finishEnqueuing()
                let answerText = answerTextParts.joined(separator: " ")
                if !answerText.isEmpty {
                    self.priorQuestions.append(question)
                }
                if self.machine.state == .thinking {
                    // Stream finished with zero playable audio chunks —
                    // nothing to play, but the ask itself succeeded.
                    // Graceful degradation: don't strand the UI in
                    // `.thinking`.
                    self.failAnswer()
                }
            } catch {
                self.lastError = error.localizedDescription
                self.failAnswer()
            }
        }
    }

    @MainActor
    private func failAnswer() {
        answerPlayer.stopAll()
        guard machine.send(.answerFailed) else { return }
        state = machine.state
        finishResuming()
    }

    // MARK: - answering → resuming → idle

    /// `AnswerAudioPlayer.onFinished` — fires once every chunk (answer and
    /// bridge alike) has finished playing.
    private func handleAnswerFinished() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            guard self.machine.send(.answerFinished) else { return }
            self.state = self.machine.state
            self.finishResuming()
        }
    }

    /// Restores volume and, if the interrupt actually paused playback (see
    /// `isPausedForInterrupt`), resumes from that exact frozen position —
    /// `PlaybackCoordinator.play()` never seeks, it just continues the
    /// scheduled buffer, which is what makes this "the exact pause
    /// position" rather than an approximation. Closes the loop back to
    /// `.idle`.
    @MainActor
    private func finishResuming() {
        freezeTask?.cancel()
        freezeTask = nil

        // Unpause first, ramp volume up second: mirrors the fade-down on the
        // way in, so resuming reads as the hosts turning their attention
        // back rather than a sudden jump to full volume.
        if isPausedForInterrupt {
            playback.play()
        }
        isPausedForInterrupt = false
        playback.restoreVolume(over: PlaybackCoordinator.duckSeconds)

        guard machine.send(.resumeComplete) else { return }
        state = machine.state
        timing.log()
        partialTranscript = ""
    }
}
