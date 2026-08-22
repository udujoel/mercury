import XCTest
@testable import Mercury

/// Exhaustive coverage of `JoinStateMachine`'s transition table: every legal
/// edge in PRD 3.5's five-state diagram, and — per the phase acceptance
/// criteria — a representative illegal edge from every state, since the
/// whole point of modelling this as an enum-driven machine rather than
/// scattered booleans is that illegal transitions are supposed to be
/// impossible to reach by construction.
final class JoinStateMachineTests: XCTestCase {

    // MARK: - Legal transitions

    func testTapFromIdleEntersListening() {
        let machine = JoinStateMachine()
        XCTAssertTrue(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .listening)
    }

    func testSpeechEndedFromListeningEntersThinking() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        XCTAssertTrue(machine.send(.speechEnded))
        XCTAssertEqual(machine.state, .thinking)
    }

    func testTapAgainFromListeningCancelsToResuming() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        XCTAssertTrue(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .resuming)
    }

    func testTimeoutFromListeningEntersResuming() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        XCTAssertTrue(machine.send(.timedOut))
        XCTAssertEqual(machine.state, .resuming)
    }

    func testAnswerReadyFromThinkingEntersAnswering() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertTrue(machine.send(.answerReady))
        XCTAssertEqual(machine.state, .answering)
    }

    func testAnswerFailedFromThinkingEntersResuming() {
        // Graceful degradation (CLAUDE.md rule 7): a backend/network failure
        // while forming the answer must still hand control back, not strand
        // the UI in `.thinking`.
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertTrue(machine.send(.answerFailed))
        XCTAssertEqual(machine.state, .resuming)
    }

    func testAnswerFinishedFromAnsweringEntersResuming() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        machine.send(.answerReady)
        XCTAssertTrue(machine.send(.answerFinished))
        XCTAssertEqual(machine.state, .resuming)
    }

    func testResumeCompleteFromResumingReturnsToIdle() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.timedOut)
        XCTAssertEqual(machine.state, .resuming)
        XCTAssertTrue(machine.send(.resumeComplete))
        XCTAssertEqual(machine.state, .idle)
    }

    func testFullHappyPathReachesIdle() {
        let machine = JoinStateMachine()
        XCTAssertTrue(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .listening)
        XCTAssertTrue(machine.send(.speechEnded))
        XCTAssertEqual(machine.state, .thinking)
        XCTAssertTrue(machine.send(.answerReady))
        XCTAssertEqual(machine.state, .answering)
        XCTAssertTrue(machine.send(.answerFinished))
        XCTAssertEqual(machine.state, .resuming)
        XCTAssertTrue(machine.send(.resumeComplete))
        XCTAssertEqual(machine.state, .idle)
    }

    // MARK: - Illegal transitions: every state rejects the wrong event

    /// The specific acceptance-criteria case: a tap while the hosts are
    /// mid-answer must not re-enter `.listening`.
    func testTapWhileAnsweringDoesNotReenterListening() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        machine.send(.answerReady)
        XCTAssertEqual(machine.state, .answering)

        XCTAssertFalse(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .answering, "an illegal tap must leave the state unchanged")
    }

    func testTapWhileThinkingIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertEqual(machine.state, .thinking)

        XCTAssertFalse(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .thinking)
    }

    func testTapWhileResumingIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.timedOut)
        XCTAssertEqual(machine.state, .resuming)

        XCTAssertFalse(machine.send(.tapMic))
        XCTAssertEqual(machine.state, .resuming)
    }

    func testSpeechEndedFromIdleIsIgnored() {
        let machine = JoinStateMachine()
        XCTAssertFalse(machine.send(.speechEnded))
        XCTAssertEqual(machine.state, .idle)
    }

    func testSpeechEndedFromThinkingIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertFalse(machine.send(.speechEnded))
        XCTAssertEqual(machine.state, .thinking)
    }

    func testAnswerReadyFromIdleIsIgnored() {
        let machine = JoinStateMachine()
        XCTAssertFalse(machine.send(.answerReady))
        XCTAssertEqual(machine.state, .idle)
    }

    func testAnswerReadyFromListeningIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        XCTAssertFalse(machine.send(.answerReady))
        XCTAssertEqual(machine.state, .listening)
    }

    func testAnswerFinishedFromThinkingIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertFalse(machine.send(.answerFinished))
        XCTAssertEqual(machine.state, .thinking)
    }

    func testTimedOutFromIdleIsIgnored() {
        let machine = JoinStateMachine()
        XCTAssertFalse(machine.send(.timedOut))
        XCTAssertEqual(machine.state, .idle)
    }

    func testTimedOutFromThinkingIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        XCTAssertFalse(machine.send(.timedOut))
        XCTAssertEqual(machine.state, .thinking)
    }

    func testResumeCompleteFromIdleIsIgnored() {
        let machine = JoinStateMachine()
        XCTAssertFalse(machine.send(.resumeComplete))
        XCTAssertEqual(machine.state, .idle)
    }

    func testResumeCompleteFromAnsweringIsIgnored() {
        let machine = JoinStateMachine()
        machine.send(.tapMic)
        machine.send(.speechEnded)
        machine.send(.answerReady)
        XCTAssertFalse(machine.send(.resumeComplete))
        XCTAssertEqual(machine.state, .answering)
    }

    /// Exhaustive sweep: for every `(state, event)` pair not in the known
    /// legal table, `transition(from:on:)` must return `nil`. This is the
    /// single test most likely to catch a typo'd extra edge added later —
    /// every other test here only proves specific pairs behave correctly,
    /// this one proves nothing *else* does.
    func testEveryTransitionNotInTheLegalTableIsIllegal() {
        let legalPairs: Set<TransitionPair> = [
            TransitionPair(.idle, .tapMic),
            TransitionPair(.listening, .tapMic),
            TransitionPair(.listening, .timedOut),
            TransitionPair(.listening, .speechEnded),
            TransitionPair(.thinking, .answerReady),
            TransitionPair(.thinking, .answerFailed),
            TransitionPair(.answering, .answerFinished),
            TransitionPair(.resuming, .resumeComplete),
        ]

        for state in JoinState.allCases {
            for event in JoinEvent.allCases {
                let pair = TransitionPair(state, event)
                let result = JoinStateMachine.transition(from: state, on: event)
                if legalPairs.contains(pair) {
                    XCTAssertNotNil(result, "\(state) + \(event) is documented as legal but returned nil")
                } else {
                    XCTAssertNil(result, "\(state) + \(event) is not in the legal table but returned \(String(describing: result))")
                }
            }
        }
    }

    private struct TransitionPair: Hashable {
        let state: JoinState
        let event: JoinEvent
        init(_ state: JoinState, _ event: JoinEvent) {
            self.state = state
            self.event = event
        }
    }
}
