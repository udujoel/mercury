import XCTest
@testable import Mercury

// MARK: - Fakes
//
// Speech recognition and the network are both deliberately faked rather
// than real here — `SFSpeechRecognizer` needs a microphone and a physical
// device (see docs/phase3/ios_join.md), and a live `/voice/ask` call would
// be non-deterministic and (per the phase brief) must never happen from a
// test. What IS genuinely unit-testable, and what these tests exercise, is
// `JoinCoordinator`'s orchestration: given a scripted sequence of
// speech/network events, does it drive `JoinStateMachine` through the right
// states, in the right order, with the right side effects on playback?

@MainActor
private final class FakePlayback: VoiceInterruptiblePlayback {
    var positionMs: Int = 42_000
    private(set) var duckCalls: [(level: Float, seconds: TimeInterval)] = []
    private(set) var restoreCalls: [TimeInterval] = []
    private(set) var pauseCallCount = 0
    private(set) var playCallCount = 0
    private(set) var callOrder: [String] = []

    func duck(to level: Float, over seconds: TimeInterval) {
        duckCalls.append((level, seconds))
        callOrder.append("duck")
    }
    func restoreVolume(over seconds: TimeInterval) {
        restoreCalls.append(seconds)
        callOrder.append("restore")
    }
    func pause() {
        pauseCallCount += 1
        callOrder.append("pause")
    }
    func play() {
        playCallCount += 1
        callOrder.append("play")
    }
}

@MainActor
private final class FakeSpeech: SpeechRecognizing {
    var authResult: SpeechAuthStatus = .authorized
    private(set) var startCallCount = 0
    private(set) var stopCallCount = 0
    private var onEvent: ((SpeechRecognitionEvent) -> Void)?

    func requestAuthorization() async -> SpeechAuthStatus { authResult }

    func startListening(onEvent: @escaping (SpeechRecognitionEvent) -> Void) throws {
        startCallCount += 1
        self.onEvent = onEvent
    }

    func stopListening() {
        stopCallCount += 1
    }

    /// Simulates a transcript event arriving from the recognizer, as if
    /// spoken by the user right now.
    func emit(_ event: SpeechRecognitionEvent) {
        onEvent?(event)
    }
}

@MainActor
private final class FakeVoiceAsk: VoiceAskClientProtocol {
    var chunksToYield: [VoiceAskChunk] = []
    var errorToThrow: Error?
    private(set) var requests: [(briefingId: String, question: String, positionMs: Int, priorQuestions: [String])] = []

    func ask(
        briefingId: String, question: String, positionMs: Int, priorQuestions: [String]
    ) -> AsyncThrowingStream<VoiceAskChunk, Error> {
        requests.append((briefingId, question, positionMs, priorQuestions))
        let chunks = chunksToYield
        let error = errorToThrow
        return AsyncThrowingStream { continuation in
            for chunk in chunks {
                continuation.yield(chunk)
            }
            if let error {
                continuation.finish(throwing: error)
            } else {
                continuation.finish()
            }
        }
    }
}

@MainActor
private final class FakeAnswerPlayer: AnswerPlaying {
    var onFirstChunkStarted: (() -> Void)?
    var onFinished: (() -> Void)?
    private(set) var enqueued: [Data] = []
    private(set) var finishEnqueuingCallCount = 0
    private(set) var stopAllCallCount = 0

    func enqueue(audio: Data) {
        enqueued.append(audio)
        if enqueued.count == 1 { onFirstChunkStarted?() }
    }

    func finishEnqueuing() {
        finishEnqueuingCallCount += 1
        // Deliberately does NOT auto-fire `onFinished` — real playback
        // finishes some time after the last chunk is merely enqueued.
        // Tests call `simulateFinished()` to model that separately.
    }

    func stopAll() {
        stopAllCallCount += 1
    }

    func simulateFinished() {
        onFinished?()
    }
}

// MARK: - Tests

@MainActor
final class JoinCoordinatorTests: XCTestCase {

    private struct Harness {
        let coordinator: JoinCoordinator
        let playback: FakePlayback
        let speech: FakeSpeech
        let voiceAsk: FakeVoiceAsk
        let answerPlayer: FakeAnswerPlayer
    }

    private func makeHarness(briefingId: String? = "2026-08-22") -> Harness {
        let playback = FakePlayback()
        let speech = FakeSpeech()
        let voiceAsk = FakeVoiceAsk()
        let answerPlayer = FakeAnswerPlayer()
        let coordinator = JoinCoordinator(
            playback: playback,
            speech: speech,
            voiceAsk: voiceAsk,
            answerPlayer: answerPlayer,
            timing: JoinTimingRecorder(),
            briefingId: { briefingId },
            positionMs: { 42_000 }
        )
        return Harness(
            coordinator: coordinator, playback: playback, speech: speech,
            voiceAsk: voiceAsk, answerPlayer: answerPlayer
        )
    }

    /// Lets whatever `Task { @MainActor in ... }` hops are currently queued
    /// actually run. Every one of `JoinCoordinator`'s internal hops is a
    /// handful of synchronous statements, so a short sleep is a generous
    /// margin, not a fragile exact-timing dependency.
    private func settle(_ ms: UInt64 = 30) async {
        try? await Task.sleep(nanoseconds: ms * 1_000_000)
    }

    // MARK: Scripted chunk builders
    //
    // Mirror the real `/voice/ask` contract (backend/app/pipeline/voice.py):
    // `isFinal` marks the one chunk per stream that carries the backend's
    // own timing instrumentation, not a distinct "done" event.

    private func answerChunk(_ text: String, audio: Data, isFinal: Bool = false) -> VoiceAskChunk {
        VoiceAskChunk(kind: .answer, index: 0, speaker: "A", text: text, audio: audio, audioFailed: false, isFinal: isFinal)
    }

    private func bridgeChunk(_ text: String, audio: Data, isFinal: Bool = true) -> VoiceAskChunk {
        VoiceAskChunk(kind: .bridge, index: 0, speaker: "A", text: text, audio: audio, audioFailed: false, isFinal: isFinal)
    }

    /// The fallback end-of-stream marker for when nothing playable was ever
    /// produced — see `VoiceAskChunk.Kind.done`'s doc comment.
    private func doneChunk() -> VoiceAskChunk {
        VoiceAskChunk(kind: .done, index: 0, speaker: "", text: "", audio: nil, audioFailed: true, isFinal: true)
    }

    // MARK: Tap from idle

    func testTapFromIdleEntersListeningAndDucksImmediately() {
        let h = makeHarness()
        h.coordinator.tap()
        XCTAssertEqual(h.coordinator.state, .listening)
        XCTAssertEqual(h.playback.duckCalls.count, 1)
        XCTAssertEqual(h.playback.duckCalls.first?.level, PlaybackCoordinator.duckLevel)
        XCTAssertEqual(h.playback.duckCalls.first?.seconds, PlaybackCoordinator.duckSeconds)
    }

    func testTapWhileAnsweringIsIgnoredAndDoesNotReenterListening() async {
        let audio = Data([0x00])
        let h = makeHarness()
        h.voiceAsk.chunksToYield = [answerChunk("what's next", audio: audio, isFinal: true)]

        h.coordinator.tap()
        await settle()
        h.speech.emit(.final("what's next"))
        await settle()
        XCTAssertEqual(h.coordinator.state, .answering)

        let duckCallsBefore = h.playback.duckCalls.count
        h.coordinator.tap() // must be a no-op
        await settle()

        XCTAssertEqual(h.coordinator.state, .answering, "a tap mid-answer must not re-enter listening")
        XCTAssertEqual(h.playback.duckCalls.count, duckCallsBefore, "no second duck from the ignored tap")
        XCTAssertEqual(h.speech.startCallCount, 1, "no second listening session started")
    }

    // MARK: Cancel path (tap again)

    func testTapAgainWhileListeningCancelsBackToIdle() async {
        let h = makeHarness()
        h.coordinator.tap()
        XCTAssertEqual(h.coordinator.state, .listening)

        h.coordinator.tap()
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.speech.stopCallCount, 1)
        XCTAssertEqual(h.voiceAsk.requests.count, 0, "cancelling before speech ends must never call the backend")
        XCTAssertEqual(h.playback.restoreCalls.count, 1, "volume is always restored, even on cancel")
    }

    func testFastCancelWithinDuckWindowNeverPausesOrResumesPlayback() async {
        // Cancelling well inside the 120ms duck fade means the engine was
        // never actually paused — nothing to "resume" either.
        let h = makeHarness()
        h.coordinator.tap()
        h.coordinator.tap() // cancel immediately, long before the 120ms freeze fires
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.playback.pauseCallCount, 0)
        XCTAssertEqual(h.playback.playCallCount, 0)
        XCTAssertEqual(h.playback.restoreCalls.count, 1, "volume still ramps back up even though nothing paused")
    }

    // MARK: Resume from the exact pause position

    func testNormalSpeedInterruptFreezesPlaybackThenResumesExactlyOnce() async {
        // Waits past the 120ms duck fade before doing anything else, so the
        // freeze (pause) actually fires — this is the realistic case, since
        // speech recognition and a network round trip always take longer
        // than 120ms.
        let audio = Data([0x00])
        let h = makeHarness()
        h.voiceAsk.chunksToYield = [
            answerChunk("It's sunny.", audio: audio, isFinal: true),
        ]

        h.coordinator.tap()
        await settle(180) // past the 120ms fade — freeze must have fired
        XCTAssertEqual(h.playback.pauseCallCount, 1, "playback should be frozen for a normal-speed interrupt")

        h.speech.emit(.final("what's the weather"))
        await settle()
        XCTAssertEqual(h.coordinator.state, .answering)

        h.answerPlayer.simulateFinished()
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.playback.pauseCallCount, 1, "exactly one freeze for one interrupt")
        XCTAssertEqual(h.playback.playCallCount, 1, "exactly one resume — play() never seeks, so this alone is the exact pause position")

        let pauseIndex = h.playback.callOrder.firstIndex(of: "pause")
        let playIndex = h.playback.callOrder.lastIndex(of: "play")
        XCTAssertNotNil(pauseIndex)
        XCTAssertNotNil(playIndex)
        if let pauseIndex, let playIndex {
            XCTAssertLessThan(pauseIndex, playIndex, "must resume strictly after freezing, never before")
        }
    }

    // MARK: Happy path end to end

    func testFullHappyPathReturnsToIdleAndSendsAnswerToPriorQuestions() async {
        let audio = Data([0xAA, 0xBB])
        let h = makeHarness()
        h.voiceAsk.chunksToYield = [
            answerChunk("The forecast is sunny and 68 degrees.", audio: audio),
            bridgeChunk("Anyway, back to your inbox.", audio: audio, isFinal: true),
        ]

        h.coordinator.tap()
        await settle()
        XCTAssertEqual(h.speech.startCallCount, 1)

        h.speech.emit(.partial("what's the"))
        await settle()
        XCTAssertEqual(h.coordinator.partialTranscript, "what's the")

        h.speech.emit(.final("what's the weather today"))
        await settle()

        XCTAssertEqual(h.coordinator.state, .answering)
        XCTAssertEqual(h.answerPlayer.enqueued.count, 2)
        XCTAssertEqual(h.voiceAsk.requests.first?.question, "what's the weather today")
        XCTAssertEqual(h.voiceAsk.requests.first?.briefingId, "2026-08-22")
        XCTAssertEqual(h.voiceAsk.requests.first?.positionMs, 42_000)

        h.answerPlayer.simulateFinished()
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.playback.restoreCalls.count, 1)
    }

    func testSecondAskIncludesTheFirstQuestionAsPriorContext() async {
        let audio = Data([0x01])
        let h = makeHarness()
        h.voiceAsk.chunksToYield = [
            answerChunk("Sunny.", audio: audio, isFinal: true),
        ]

        h.coordinator.tap()
        await settle()
        h.speech.emit(.final("what's the weather"))
        await settle()
        h.answerPlayer.simulateFinished()
        await settle()
        XCTAssertEqual(h.coordinator.state, .idle)

        h.coordinator.tap()
        await settle()
        h.speech.emit(.final("and tomorrow"))
        await settle()

        XCTAssertEqual(h.voiceAsk.requests.count, 2)
        XCTAssertEqual(h.voiceAsk.requests.last?.priorQuestions, ["what's the weather"])
    }

    // MARK: Graceful degradation (CLAUDE.md rule 7)

    func testEmptyFinalTranscriptNeverCallsTheBackendAndReturnsToIdle() async {
        let h = makeHarness()
        h.coordinator.tap()
        await settle() // let requestSpeech() register FakeSpeech's onEvent before emitting
        h.speech.emit(.final("   "))
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.voiceAsk.requests.count, 0)
        XCTAssertNotNil(h.coordinator.lastError)
    }

    func testBackendFailureReturnsToIdleInsteadOfStrandingInThinking() async {
        let h = makeHarness()
        h.voiceAsk.errorToThrow = VoiceAskError.transport("offline")

        h.coordinator.tap()
        await settle() // let requestSpeech() register FakeSpeech's onEvent before emitting
        h.speech.emit(.final("what's next"))
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertNotNil(h.coordinator.lastError)
        XCTAssertEqual(h.playback.restoreCalls.count, 1, "briefing is still handed back on failure")
    }

    func testStreamWithNoAudioChunksStillReturnsToIdle() async {
        // The ask succeeded but produced nothing playable — must not strand
        // the UI in `.thinking` waiting for audio that will never arrive.
        let h = makeHarness()
        h.voiceAsk.chunksToYield = [doneChunk()]

        h.coordinator.tap()
        await settle() // let requestSpeech() register FakeSpeech's onEvent before emitting
        h.speech.emit(.final("hello"))
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
    }

    func testDeniedAuthorizationCancelsListeningWithoutStranding() async {
        let h = makeHarness()
        h.speech.authResult = .denied

        h.coordinator.tap()
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertNotNil(h.coordinator.lastError)
        XCTAssertEqual(h.speech.startCallCount, 0, "never starts listening without authorization")
        XCTAssertEqual(h.playback.restoreCalls.count, 1)
    }

    func testNoBriefingLoadedFailsGracefullyWithoutCallingSpeechAgain() async {
        let h = makeHarness(briefingId: nil)
        h.coordinator.tap()
        await settle() // let requestSpeech() register FakeSpeech's onEvent before emitting
        h.speech.emit(.final("what's next"))
        await settle()

        XCTAssertEqual(h.coordinator.state, .idle)
        XCTAssertEqual(h.voiceAsk.requests.count, 0)
        XCTAssertNotNil(h.coordinator.lastError)
    }
}
