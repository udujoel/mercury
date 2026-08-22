import Foundation
import os

/// The six timing budget lines from PRD 3.5. Each is measured between two
/// specific instants in one Join interaction; see `JoinTimingRecorder`.
enum JoinTimingStage: String, CaseIterable, Sendable {
    case tapToDucked = "tap → audio ducked"
    case tapToListeningUI = "tap → listening UI visible"
    case speechEndToDetected = "speech end → detected"
    case transcriptToFirstToken = "transcript → LLM first token"
    case firstTokenToAudio = "first token → audio playing"
    case total = "TOTAL perceived gap"

    /// The budget in milliseconds, verbatim from PRD 3.5 / the phase brief.
    var budgetMs: Int {
        switch self {
        case .tapToDucked: return 100
        case .tapToListeningUI: return 200
        case .speechEndToDetected: return 600
        case .transcriptToFirstToken: return 1200
        case .firstTokenToAudio: return 800
        case .total: return 2500
        }
    }
}

/// One measured stage: how long it actually took against how long it was
/// allowed to take.
struct JoinTimingResult: Equatable, Sendable {
    let stage: JoinTimingStage
    let measuredMs: Int
    var budgetMs: Int { stage.budgetMs }
    var withinBudget: Bool { measuredMs <= budgetMs }
}

/// Captures the instants of a single Join interaction and turns them into
/// `JoinTimingResult`s against the PRD 3.5 budget.
///
/// The clock is injected (`now: () -> Date`) rather than reading
/// `Date()`/`DispatchTime` directly, specifically so tests can supply exact,
/// deterministic offsets instead of racing a real clock — the "date/timezone
/// bugs have bitten this project four times" lesson in the phase brief
/// applies just as much to elapsed-time math as to calendar math.
final class JoinTimingRecorder {

    private let now: () -> Date
    private var marks: [Stage: Date] = [:]

    private enum Stage: Hashable {
        case tap, ducked, listeningUI, speechEnd, speechDetected
        case transcript, firstToken, audioPlaying
    }

    init(now: @escaping () -> Date = Date.init) {
        self.now = now
    }

    // MARK: - Marking

    func markTap() { marks[.tap] = now() }
    func markDucked() { marks[.ducked] = now() }
    func markListeningUIVisible() { marks[.listeningUI] = now() }
    func markSpeechEnded() { marks[.speechEnd] = now() }
    func markSpeechDetected() { marks[.speechDetected] = now() }
    func markTranscriptReady() { marks[.transcript] = now() }
    func markFirstToken() { marks[.firstToken] = now() }
    func markAudioPlaying() { marks[.audioPlaying] = now() }

    /// Resets all marks — call at the start of every new tap so a stale mark
    /// from a previous interaction can never leak into this one's math.
    func reset() { marks.removeAll(keepingCapacity: true) }

    // MARK: - Results

    /// Every stage that has both its start and end mark recorded, in PRD
    /// order. A stage whose marks never both landed (e.g. the user cancelled
    /// before an answer arrived) is simply absent, not reported as 0ms —
    /// silently claiming a stage was fast because it never ran would hide
    /// exactly the kind of failure this instrumentation exists to catch.
    func results() -> [JoinTimingResult] {
        var out: [JoinTimingResult] = []
        if let r = delta(.tap, .ducked, .tapToDucked) { out.append(r) }
        if let r = delta(.tap, .listeningUI, .tapToListeningUI) { out.append(r) }
        if let r = delta(.speechEnd, .speechDetected, .speechEndToDetected) { out.append(r) }
        if let r = delta(.transcript, .firstToken, .transcriptToFirstToken) { out.append(r) }
        if let r = delta(.firstToken, .audioPlaying, .firstTokenToAudio) { out.append(r) }
        if let r = delta(.tap, .audioPlaying, .total) { out.append(r) }
        return out
    }

    private func delta(_ start: Stage, _ end: Stage, _ stage: JoinTimingStage) -> JoinTimingResult? {
        guard let a = marks[start], let b = marks[end] else { return nil }
        // `.rounded()` before truncating to `Int`: floating-point elapsed
        // times land at e.g. 49.99999999997 for what is conceptually
        // exactly 50ms, and a plain `Int(...)` truncates that down to 49 —
        // observed as an off-by-one-millisecond flake in
        // `JoinTimingTests`.
        let ms = Int((b.timeIntervalSince(a) * 1000).rounded())
        return JoinTimingResult(stage: stage, measuredMs: ms)
    }

    /// Logs every recorded stage. The phase gate is a number, so every Join
    /// interaction writes its numbers out — pass or fail, so a regression
    /// shows up in the log the moment it happens rather than needing someone
    /// to notice the app "feels" slower.
    func log(using logger: Logger = Logger(subsystem: "com.joeludu.mercury", category: "join.timing")) {
        for result in results() {
            let verdict = result.withinBudget ? "OK" : "OVER BUDGET"
            logger.log(
                "\(result.stage.rawValue, privacy: .public): \(result.measuredMs, privacy: .public)ms / \(result.budgetMs, privacy: .public)ms — \(verdict, privacy: .public)"
            )
        }
    }
}
