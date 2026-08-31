import XCTest
import SwiftData
@testable import Mercury

final class ResumePositionTests: XCTestCase {

    private func makeInMemoryContainer() throws -> ModelContainer {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return try ModelContainer(for: schema, configurations: [configuration])
    }

    func testResumePositionRoundTripsAfterSaveAndUpdate() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let manifest = try ManifestDecodingTests.loadFixture()

        try await store.save(manifest, localAudioPath: "/tmp/2026-08-21.wav")

        // Freshly saved briefing starts unresumed.
        let initial = try await store.resumePositionMs(for: manifest.briefingId)
        XCTAssertEqual(initial, 0)

        try await store.updateResumePosition(briefingId: manifest.briefingId, positionMs: 13500)

        let resumed = try await store.resumePositionMs(for: manifest.briefingId)
        XCTAssertEqual(resumed, 13500)
    }

    // MARK: - Regression: the store must actually be REACHED

    /// Everything else in this file passed against code no running app
    /// could execute.
    ///
    /// `MercuryApp` built the `ModelContainer` and injected it into the
    /// SwiftUI environment, but nothing ever constructed a `BriefingStore`
    /// from it — so `AppState` never saved a briefing, never read a resume
    /// position, and a six-minute briefing restarted at "Good morning"
    /// every single time. The tests above were green throughout. That is
    /// the most misleading state a test suite can be in: not wrong about
    /// the code, just silent about whether anyone calls it.
    ///
    /// These drive persistence through `AppState`, the way the app does.
    func testPlayBriefingPersistsTheTranscriptThroughAppState() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)
        await MainActor.run { state.store = store }

        // `downloadAudio` throws in RecordingAPI, so playback never starts —
        // but the transcript must already be saved by then, because it is
        // worth keeping whether or not the audio arrives.
        await state.playBriefing()

        let all = try await store.allBriefings()
        XCTAssertEqual(all.count, 1, "the manifest must be persisted, got \(all.count)")
        XCTAssertEqual(all.first?.id, "2026-08-23")
    }

    func testAppStateWithNoStoreStillPlaysRatherThanFailing() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)   // no store attached

        await state.playBriefing()

        // The failure here is the download, not persistence: an AppState
        // with no container must degrade to "no persistence", never crash.
        let calls = api.calls
        XCTAssertTrue(calls.contains("fetchManifest"))
        XCTAssertTrue(calls.contains("downloadAudio"))
    }

    // MARK: - Resume policy

    func testResumeTargetSkipsAPositionNotWorthResumingTo() {
        XCTAssertNil(AppState.resumeTarget(savedMs: nil, durationMs: 360_000))
        XCTAssertNil(AppState.resumeTarget(savedMs: 0, durationMs: 360_000))
        XCTAssertNil(AppState.resumeTarget(savedMs: 1_500, durationMs: 360_000),
                     "a second and a half in is not a resume point")
    }

    func testResumeTargetReturnsAMidBriefingPosition() {
        XCTAssertEqual(AppState.resumeTarget(savedMs: 240_000, durationMs: 360_000), 240_000)
    }

    /// A briefing already heard to the end must start over, not drop the
    /// user two seconds from the sign-off.
    func testResumeTargetIgnoresAPositionAtTheVeryEnd() {
        XCTAssertNil(AppState.resumeTarget(savedMs: 359_000, durationMs: 360_000))
        XCTAssertNil(AppState.resumeTarget(savedMs: 360_000, durationMs: 360_000))
    }

    func testResumeTargetWithUnknownDurationStillResumes() {
        XCTAssertEqual(AppState.resumeTarget(savedMs: 240_000, durationMs: 0), 240_000)
    }

    // MARK: - Refuting the reported Int underflow

    /// kilo-code-bot reported that `durationMs - minimumMs` "underflows to a
    /// large negative number and Swift's Int arithmetic traps", with a
    /// 4-second saved position against a 2-second briefing as the example.
    ///
    /// It does not. `Int` is signed: `2000 - 3000` is `-1000`, an ordinary
    /// representable value, and Swift traps on overflow past the bounds of
    /// the type, not on a subtraction crossing zero. `4000 < -1000` is
    /// simply false, so the guard returns nil — which is also the correct
    /// answer, since a 2-second briefing has nothing worth resuming into.
    ///
    /// This test is the negative control for that claim: if the reported
    /// trap were real, it would not fail, it would crash the suite.
    func testAShortBriefingWithALongerSavedPositionDoesNotTrap() {
        XCTAssertNil(AppState.resumeTarget(savedMs: 4_000, durationMs: 2_000))
        XCTAssertNil(AppState.resumeTarget(savedMs: 4_000, durationMs: 1))
        XCTAssertNil(AppState.resumeTarget(savedMs: 1, durationMs: 1))
    }

    /// The suggested replacement — `savedMs + minimumMs < durationMs` — is
    /// algebraically the same for ordinary values and strictly worse at the
    /// edge: it moves the arithmetic to the side that can actually overflow.
    /// This case passes as written and would trap on the suggestion, so it
    /// pins the reason for keeping the current form.
    func testAnAbsurdSavedPositionIsRejectedRatherThanOverflowing() {
        XCTAssertNil(AppState.resumeTarget(savedMs: .max, durationMs: 360_000))
    }

    func testResumePositionForUnknownBriefingIsNil() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)

        let result = try await store.resumePositionMs(for: "does-not-exist")
        XCTAssertNil(result)
    }

    func testSavePersistsTurnsAndSourcesForOfflineTranscriptAccess() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let manifest = try ManifestDecodingTests.loadFixture()

        try await store.save(manifest, localAudioPath: nil)

        let all = try await store.allBriefings()
        XCTAssertEqual(all.count, 1)
        let saved = try XCTUnwrap(all.first)
        XCTAssertEqual(saved.id, manifest.briefingId)
        XCTAssertEqual(saved.turns.count, manifest.turns.count)
        XCTAssertEqual(saved.sources.count, manifest.sources.count)
        XCTAssertNil(saved.localAudioPath)
    }

    func testSavingSameBriefingTwiceUpserts() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let manifest = try ManifestDecodingTests.loadFixture()

        try await store.save(manifest, localAudioPath: "/tmp/first.wav")
        try await store.updateResumePosition(briefingId: manifest.briefingId, positionMs: 9000)
        try await store.save(manifest, localAudioPath: nil)

        let all = try await store.allBriefings()
        XCTAssertEqual(all.count, 1, "saving the same briefing_id again must update, not duplicate")

        // Resume position survives a re-save that doesn't touch it.
        let resumed = try await store.resumePositionMs(for: manifest.briefingId)
        XCTAssertEqual(resumed, 9000)
    }
}
