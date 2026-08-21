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
