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

    // MARK: - Regression: the background launch must reach the store too

    /// cubic: wiring the store from `RootView.task` reaches it only when a
    /// foreground scene appears. iOS launches this app with no scene for a
    /// silent push or a `BGProcessingTask` — which is when the overnight
    /// prefetch runs — so on exactly those launches there was no store to
    /// save the prefetched transcript into.
    ///
    /// Worse, and pre-existing: `AppDelegate.appState` was assigned from
    /// the same view task, so the prefetch handler's `guard let appState`
    /// failed and the task completed as a failure without doing anything.
    ///
    /// No scene is constructed here, which is the whole point of the test.
    @MainActor
    func testTheAppDelegateHasAWiredStateWithoutAnyScene() throws {
        let delegate = AppDelegate()

        let state = try XCTUnwrap(
            delegate.appState, "a background launch has no view to wire this up"
        )
        XCTAssertNotNil(
            state.store,
            "and no view to build the BriefingStore either, so a prefetched "
                + "briefing had nowhere to be saved"
        )
    }

    /// One process, one instance — otherwise the background path would save
    /// into a store the UI never reads.
    @MainActor
    func testEveryEntryPointSharesOneAppState() {
        XCTAssertTrue(
            AppDelegate().appState === AppBootstrap.appState,
            "the delegate and the scene must not each get their own"
        )
        XCTAssertTrue(AppBootstrap.appState === AppBootstrap.appState)
    }

    // MARK: - Regression: a regenerated briefing is not the same briefing

    /// A briefing id is a date ("2026-08-21"), so a regeneration REUSES it —
    /// that is exactly what the backend's `--force` / `--briefing-id` path
    /// does when a bad briefing is replaced. Everything below turns on the
    /// id not being evidence of identical content.
    private func manifest(generatedAt: Date, durationMs: Int) -> BriefingManifest {
        BriefingManifest(
            briefingId: "2026-08-21",
            generatedAt: generatedAt,
            durationMs: durationMs,
            audioUrl: "/briefing/2026-08-21/audio",
            segments: [BriefingSegment(id: "inbox", label: "Inbox", startMs: 0)],
            turns: [Turn(speaker: "A", segment: "inbox", text: "morning",
                         startMs: 0, endMs: 1_000)],
            sources: []
        )
    }

    /// macroscope/cubic: the upsert kept the old `lastPositionMs` and
    /// `completed` when the content underneath them had been replaced, so
    /// `startPlaying` seeked four minutes into a briefing the listener had
    /// never heard — or, with `completed` carried over, skipped it.
    func testARegeneratedBriefingStartsFromTheTop() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let original = manifest(generatedAt: Date(timeIntervalSince1970: 1_000), durationMs: 360_000)

        try await store.save(original, localAudioPath: "/tmp/2026-08-21.m4a")
        try await store.updateResumePosition(briefingId: "2026-08-21", positionMs: 240_000)
        try await store.markCompleted(briefingId: "2026-08-21")

        // Same id, regenerated an hour later against a different script.
        let replacement = manifest(generatedAt: Date(timeIntervalSince1970: 4_600), durationMs: 300_000)
        try await store.save(replacement, localAudioPath: nil)

        let resumed = try await store.resumePositionMs(for: "2026-08-21")
        XCTAssertEqual(
            resumed, 0,
            "four minutes into the briefing that was replaced is not four "
                + "minutes into its replacement"
        )
        let stored = try await store.allBriefings()
        XCTAssertEqual(stored.first?.completed, false,
                       "the listener has not heard this one")
        XCTAssertEqual(stored.first?.generatedAt, Date(timeIntervalSince1970: 4_600),
                       "the stored stamp must follow the content, or retention "
                           + "orders briefings by when the id was first seen")
    }

    /// The negative control, and the reason this cannot simply reset on
    /// every save: `AppState.playBriefing` persists the SAME manifest twice
    /// per playback — once when it arrives, once with the local audio path.
    /// Resetting unconditionally would wipe the resume point on every save
    /// and break the feature this PR exists to wire up.
    func testSavingTheSameBriefingAgainKeepsTheResumePoint() async throws {
        let container = try makeInMemoryContainer()
        let store = BriefingStore(modelContainer: container)
        let same = manifest(generatedAt: Date(timeIntervalSince1970: 1_000), durationMs: 360_000)

        try await store.save(same, localAudioPath: nil)
        try await store.updateResumePosition(briefingId: "2026-08-21", positionMs: 240_000)
        try await store.save(same, localAudioPath: "/tmp/2026-08-21.m4a")

        let resumed = try await store.resumePositionMs(for: "2026-08-21")
        XCTAssertEqual(resumed, 240_000, "same content, same place in it")
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
