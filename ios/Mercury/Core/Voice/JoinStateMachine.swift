import Observation

/// The five states of the Join flow (PRD 3.5):
///
/// ```
/// idle ──tap mic──► listening ──speech ends──► thinking
///                       │                          │
///                       │ tap again / timeout       │ answer ready
///                       ▼                          ▼
///                    resuming ◄──answer done── answering
///                       │
///                       ▼
///                     idle
/// ```
///
/// `thinking` also has a failure edge straight to `resuming` — CLAUDE.md rule
/// 7 ("graceful degradation") means a backend/network failure while forming
/// the answer must still hand the briefing back to the user, not strand the
/// UI in `thinking` forever.
enum JoinState: String, Equatable, CaseIterable, Sendable {
    case idle
    case listening
    case thinking
    case answering
    case resuming
}

/// Every event the Join flow can raise. Deliberately does not carry the
/// transcript/answer payload — that's `JoinCoordinator`'s job. This type only
/// answers "is this transition legal right now?".
enum JoinEvent: Equatable, Sendable, CaseIterable {
    /// Mic tapped. Legal from `.idle` (start) and `.listening` (cancel).
    /// Illegal everywhere else — a tap while the hosts are mid-answer must
    /// not re-enter `listening`.
    case tapMic
    /// SFSpeechRecognizer endpointing decided the user stopped talking.
    case speechEnded
    /// No speech end detected within the listening timeout.
    case timedOut
    /// First streamed audio for the answer is ready to play.
    case answerReady
    /// The ask request failed (network, backend error, empty transcript).
    /// Graceful degradation: still returns control to the briefing.
    case answerFailed
    /// The answer (and its bridge line) finished playing.
    case answerFinished
    /// Volume has been restored and playback resumed from the exact pause
    /// position.
    case resumeComplete
}

/// Enum-driven state machine for the Join flow. Deliberately holds no audio,
/// speech, or networking state — `send(_:)` is a pure function of
/// `(state, event) -> state?`, which is what makes every transition (legal
/// and illegal) unit-testable without a simulator, a microphone, or a
/// backend. `JoinCoordinator` is the thing that actually drives it and
/// performs side effects on each transition.
@Observable
final class JoinStateMachine {

    private(set) var state: JoinState = .idle

    /// Attempts `event` against the current state. Returns whether the
    /// transition was legal. An illegal event (e.g. `.tapMic` while
    /// `.answering`) is silently ignored — `state` does not change — rather
    /// than trapping, since a stray tap during the answer is a completely
    /// normal thing for a user to do.
    @discardableResult
    func send(_ event: JoinEvent) -> Bool {
        guard let next = Self.transition(from: state, on: event) else { return false }
        state = next
        return true
    }

    /// The pure transition table. `static` and side-effect free so it can be
    /// exhaustively tested (every legal edge, and a representative illegal
    /// edge from every state) without instantiating the class.
    static func transition(from state: JoinState, on event: JoinEvent) -> JoinState? {
        switch (state, event) {
        case (.idle, .tapMic):
            return .listening

        case (.listening, .tapMic):
            // Tap again cancels the interrupt outright — same exit door as a
            // timeout, since neither has an answer to deliver.
            return .resuming
        case (.listening, .timedOut):
            return .resuming
        case (.listening, .speechEnded):
            return .thinking

        case (.thinking, .answerReady):
            return .answering
        case (.thinking, .answerFailed):
            return .resuming

        case (.answering, .answerFinished):
            return .resuming

        case (.resuming, .resumeComplete):
            return .idle

        default:
            return nil
        }
    }

    /// Resets to `.idle` unconditionally. Used only for recovering from a
    /// state that a bug (not a user action) has left stuck — never called
    /// from the normal flow, which always reaches `.idle` via
    /// `.resumeComplete`.
    func forceIdle() {
        state = .idle
    }
}
