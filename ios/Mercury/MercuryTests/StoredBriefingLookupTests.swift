import XCTest
import SwiftData
@testable import Mercury

/// The offline fallback, which asked the cache a question it stopped being
/// able to answer on 2026-09-07.
///
/// Found reviewing the id fixes rather than by using the app, and it is the
/// THIRD site of the same bug: a briefing id stopped meaning a day, and code
/// that predicted one kept compiling. `playStoredBriefing` asked the store
/// for `2026-09-12` under an exact-match predicate while every row saved
/// since the change is keyed `2026-09-12-0535`, so a user on a plane was
/// shown a network error for a briefing already on their phone.
final class StoredBriefingLookupTests: XCTestCase {

    private func makeStore() throws -> BriefingStore {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return BriefingStore(
            modelContainer: try ModelContainer(for: schema, configurations: [configuration]))
    }

    private func manifest(id: String, generatedAt: String) throws -> BriefingManifest {
        let json = """
        {"briefing_id":"\(id)","generated_at":"\(generatedAt)",
         "duration_ms":1000,"audio_url":"/briefing/\(id)/audio",
         "segments":[],"turns":[],"sources":[]}
        """
        return try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: Data(json.utf8))
    }

    /// The regression itself: a timed briefing must be findable from a day.
    func testATimedBriefingIsFoundByItsDay() async throws {
        let store = try makeStore()
        try await store.save(try manifest(id: "2026-09-12-0535",
                                          generatedAt: "2026-09-12T05:35:00Z"),
                             localAudioPath: "/tmp/a.m4a")

        let byId = try await store.storedBriefing(id: "2026-09-12")
        XCTAssertNil(byId, "the old lookup — this is the bug, asserted")

        let byDay = try await store.storedBriefing(forDay: "2026-09-12")
        XCTAssertEqual(byDay?.manifest.briefingId, "2026-09-12-0535")
    }

    /// Four runs a day: the fallback must play the most recent one.
    func testTheNewestOfTheDayWins() async throws {
        let store = try makeStore()
        for (id, at) in [("2026-09-12-0535", "2026-09-12T05:35:00Z"),
                         ("2026-09-12-2155", "2026-09-12T21:55:00Z"),
                         ("2026-09-12-1213", "2026-09-12T12:13:00Z")] {
            try await store.save(try manifest(id: id, generatedAt: at),
                                 localAudioPath: "/tmp/\(id).m4a")
        }
        let stored = try await store.storedBriefing(forDay: "2026-09-12")
        XCTAssertEqual(stored?.manifest.briefingId, "2026-09-12-2155")
    }

    /// Restricted to the day ON PURPOSE: falling back to whatever is newest
    /// would answer "play my briefing" with yesterday's news and say nothing
    /// about it. The fix must not quietly become `latestStoredBriefing`.
    func testYesterdaysBriefingIsNotOfferedForToday() async throws {
        let store = try makeStore()
        try await store.save(try manifest(id: "2026-09-11-2155",
                                          generatedAt: "2026-09-11T21:55:00Z"),
                             localAudioPath: "/tmp/y.m4a")
        let stored = try await store.storedBriefing(forDay: "2026-09-12")
        XCTAssertNil(stored, "an empty morning is the honest answer")
    }

    /// Legacy rows from before the change are still reachable.
    func testALegacyBareDayRowIsStillFoundByItsDay() async throws {
        let store = try makeStore()
        try await store.save(try manifest(id: "2026-09-06",
                                          generatedAt: "2026-09-06T04:00:00Z"),
                             localAudioPath: "/tmp/l.m4a")
        let stored = try await store.storedBriefing(forDay: "2026-09-06")
        XCTAssertEqual(stored?.manifest.briefingId, "2026-09-06")
    }
}
