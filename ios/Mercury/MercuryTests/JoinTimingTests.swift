import XCTest
@testable import Mercury

/// `JoinTimingRecorder` against an injected clock — deterministic, exact
/// millisecond arithmetic, no real elapsed time. Covers every PRD 3.5
/// budget line plus the "a stage that never happened must not report 0ms"
/// guard, which is the difference between this instrumentation being
/// trustworthy and it silently lying about a cancelled interaction.
final class JoinTimingTests: XCTestCase {

    /// A clock that advances by a fixed step every time it's read, so a
    /// sequence of `mark*()` calls produces exact, predictable deltas
    /// without any real `Task.sleep` or wall-clock dependency.
    private final class StepClock {
        private var current: Date
        private let step: TimeInterval
        init(start: Date = Date(timeIntervalSince1970: 0), step: TimeInterval) {
            self.current = start
            self.step = step
        }
        func now() -> Date {
            let value = current
            current = current.addingTimeInterval(step)
            return value
        }
    }

    func testAllSixBudgetLinesAreMeasuredInOrder() {
        // Each mark call advances the clock by exactly 50ms, so every
        // pairwise delta the recorder computes should be a known multiple
        // of 50ms depending on how many marks separate the two calls.
        let clock = StepClock(step: 0.05)
        let recorder = JoinTimingRecorder(now: clock.now)

        recorder.markTap()                // t=0
        recorder.markDucked()              // t=50   -> tapToDucked = 50
        recorder.markListeningUIVisible()  // t=100  -> tapToListeningUI = 100
        recorder.markSpeechEnded()         // t=150
        recorder.markSpeechDetected()      // t=200  -> speechEndToDetected = 50
        recorder.markTranscriptReady()     // t=250
        recorder.markFirstToken()          // t=300  -> transcriptToFirstToken = 50
        recorder.markAudioPlaying()        // t=350  -> firstTokenToAudio = 50, total(tap->audio) = 350

        let results = Dictionary(uniqueKeysWithValues: recorder.results().map { ($0.stage, $0) })

        XCTAssertEqual(results[.tapToDucked]?.measuredMs, 50)
        XCTAssertEqual(results[.tapToListeningUI]?.measuredMs, 100)
        XCTAssertEqual(results[.speechEndToDetected]?.measuredMs, 50)
        XCTAssertEqual(results[.transcriptToFirstToken]?.measuredMs, 50)
        XCTAssertEqual(results[.firstTokenToAudio]?.measuredMs, 50)
        XCTAssertEqual(results[.total]?.measuredMs, 350)
    }

    func testResultsWithinBudgetWhenUnderTheLimit() {
        let clock = StepClock(step: 0.001) // 1ms between every mark: everything is fast
        let recorder = JoinTimingRecorder(now: clock.now)
        recorder.markTap()
        recorder.markDucked()

        let result = try! XCTUnwrap(recorder.results().first { $0.stage == .tapToDucked })
        XCTAssertEqual(result.budgetMs, 100)
        XCTAssertTrue(result.withinBudget)
    }

    func testResultFlagsOverBudget() {
        let clock = StepClock(step: 0.2) // 200ms between marks: over the 100ms duck budget
        let recorder = JoinTimingRecorder(now: clock.now)
        recorder.markTap()
        recorder.markDucked()

        let result = try! XCTUnwrap(recorder.results().first { $0.stage == .tapToDucked })
        XCTAssertFalse(result.withinBudget)
        XCTAssertGreaterThan(result.measuredMs, result.budgetMs)
    }

    /// A stage whose two marks never both landed (e.g. the interaction was
    /// cancelled before an answer arrived) must be absent from `results()`,
    /// not silently reported as an instant 0ms — see the recorder's own doc
    /// comment on `results()` for why that distinction matters.
    func testUnmeasuredStageIsAbsentNotZero() {
        let clock = StepClock(step: 0.01)
        let recorder = JoinTimingRecorder(now: clock.now)
        recorder.markTap()
        recorder.markDucked()
        recorder.markListeningUIVisible()
        // Cancelled here — speechEnded/speechDetected/transcript/firstToken/
        // audioPlaying never get marked.

        let stages = Set(recorder.results().map(\.stage))
        XCTAssertTrue(stages.contains(.tapToDucked))
        XCTAssertTrue(stages.contains(.tapToListeningUI))
        XCTAssertFalse(stages.contains(.speechEndToDetected))
        XCTAssertFalse(stages.contains(.transcriptToFirstToken))
        XCTAssertFalse(stages.contains(.firstTokenToAudio))
        XCTAssertFalse(stages.contains(.total))
    }

    func testResetClearsAllMarksBeforeTheNextInteraction() {
        // A stale mark from a previous tap leaking into this one's math is
        // exactly the kind of silent corruption this test guards against —
        // it would make the second interaction's numbers meaningless
        // without any visible failure.
        let clock = StepClock(step: 0.05)
        let recorder = JoinTimingRecorder(now: clock.now)
        recorder.markTap()
        recorder.markDucked()
        XCTAssertFalse(recorder.results().isEmpty)

        recorder.reset()
        XCTAssertTrue(recorder.results().isEmpty)

        recorder.markTap()
        recorder.markDucked()
        let result = try! XCTUnwrap(recorder.results().first { $0.stage == .tapToDucked })
        XCTAssertEqual(result.measuredMs, 50)
    }

    func testBudgetConstantsMatchPRD35Verbatim() {
        // These six numbers ARE the phase gate (phase brief: "the gate for
        // this whole phase is a number") — pin them so a typo'd constant
        // fails loudly instead of silently shipping a looser budget.
        XCTAssertEqual(JoinTimingStage.tapToDucked.budgetMs, 100)
        XCTAssertEqual(JoinTimingStage.tapToListeningUI.budgetMs, 200)
        XCTAssertEqual(JoinTimingStage.speechEndToDetected.budgetMs, 600)
        XCTAssertEqual(JoinTimingStage.transcriptToFirstToken.budgetMs, 1200)
        XCTAssertEqual(JoinTimingStage.firstTokenToAudio.budgetMs, 800)
        XCTAssertEqual(JoinTimingStage.total.budgetMs, 2500)
    }
}
