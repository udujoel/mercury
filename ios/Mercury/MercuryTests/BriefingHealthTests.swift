import XCTest
import SwiftData
@testable import Mercury

/// Task 33: a morning made without your inbox has to say so.
///
/// The manifest has carried `degraded_sources` since the schema was written
/// and the phone never declared it, so on 2026-09-01, 09-02 and 09-03 three
/// briefings with no inbox and no calendar were presented as normal
/// mornings. These tests cover the three places that silence could come
/// back: the decode, the cache, and the sentence.
final class BriefingHealthTests: XCTestCase {

    // MARK: - The sentence

    func testAHealthyMorningSaysNothing() {
        XCTAssertNil(BriefingHealth.notice(for: []))
        XCTAssertNil(BriefingHealth.notice(for: [""]))
    }

    func testTheExpiredTokenMorning() {
        XCTAssertEqual(BriefingHealth.notice(for: ["email", "calendar"]),
                       "Made without your calendar and your inbox.")
    }

    func testASourceAndTheSegmentItFeedsAreSaidOnce() {
        // `email` failing and `inbox` being refused cost the listener the
        // same thing; naming it twice would be wrong, not thorough.
        XCTAssertEqual(BriefingHealth.notice(for: ["email", "inbox"]),
                       "Made without your inbox.")
        XCTAssertEqual(BriefingHealth.notice(for: ["world", "news"]),
                       "Made without the news.")
    }

    func testTheOrderIsTheBriefingsNotTheLists() {
        // The briefing plays calendar, then inbox, then the world segment.
        // Review of #68: the first cut listed inbox first, and its own test
        // asserted that wrong order under this name.
        XCTAssertEqual(BriefingHealth.notice(for: ["world", "calendar", "email"]),
                       "Made without your calendar, your inbox and the news.")
    }

    func testTheRefusalLaddersSegmentsAreNamed() {
        XCTAssertEqual(BriefingHealth.notice(for: ["inbox", "follow_ups"]),
                       "Made without your inbox and your follow-ups.")
    }

    func testAnUnknownLabelIsNeverSilent() {
        // A newer backend adding a source must not turn a degraded morning
        // back into a quiet one, which is the bug this ends.
        XCTAssertEqual(BriefingHealth.notice(for: ["slack"]),
                       "Made without part of what you usually get.")
        XCTAssertEqual(BriefingHealth.notice(for: ["calendar", "slack"]),
                       "Made without your calendar and one other source.")
    }

    func testTwoUnknownLabelsAreTwoThingsMissing() {
        // Review of #68: a flag rather than a count said "one other source"
        // for any number of them.
        XCTAssertEqual(BriefingHealth.notice(for: ["calendar", "slack", "reddit"]),
                       "Made without your calendar and 2 other sources.")
    }

    func testCaseDoesNotHideALabel() {
        XCTAssertEqual(BriefingHealth.notice(for: ["Email"]), "Made without your inbox.")
    }

    // MARK: - The backup voice (task 22)

    func testAFallbackVoiceAloneIsSaid() {
        XCTAssertEqual(BriefingHealth.notice(for: [], narratorFallback: true),
                       "Read by the backup voice today.")
    }

    func testAFallbackVoiceJoinsTheSameSentence() {
        // One line, not two: to the listener both are "this morning is not
        // what you configured".
        XCTAssertEqual(
            BriefingHealth.notice(for: ["email", "calendar"], narratorFallback: true),
            "Made without your calendar and your inbox, and read by the backup voice.")
    }

    func testNoFallbackChangesNothing() {
        XCTAssertNil(BriefingHealth.notice(for: [], narratorFallback: false))
        XCTAssertEqual(BriefingHealth.notice(for: ["calendar"], narratorFallback: false),
                       "Made without your calendar.")
    }

    // MARK: - The decode

    private func manifestJSON(id: String = "2026-09-01-0535",
                              degraded: String? = nil) -> Data {
        let field = degraded.map { #","degraded_sources":\#($0)"# } ?? ""
        return Data("""
        {"briefing_id":"\(id)","generated_at":"2026-09-01T05:35:00Z",
         "duration_ms":1000,"audio_url":"/briefing/\(id)/audio",
         "segments":[],"turns":[],"sources":[]\(field)}
        """.utf8)
    }

    func testTheFieldIsDecoded() throws {
        let m = try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: manifestJSON(degraded: #"["email","calendar"]"#))
        XCTAssertEqual(m.degradedSources, ["email", "calendar"])
    }

    func testAManifestWithoutTheFieldIsHealthyNotBroken() throws {
        let m = try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: manifestJSON())
        XCTAssertEqual(m.degradedSources, [])
    }

    func testTheFallbackFlagIsDecodedAndDefaultsToFalse() throws {
        let flagged = Data("""
        {"briefing_id":"2026-10-10-0535","generated_at":"2026-10-10T05:35:00Z",
         "duration_ms":1000,"audio_url":"/a","segments":[],"turns":[],"sources":[],
         "narrator_fallback":true}
        """.utf8)
        XCTAssertTrue(try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: flagged).narratorFallback)
        XCTAssertFalse(try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: manifestJSON()).narratorFallback,
            "every manifest written before the field existed is not a fallback")
    }

    func testANullFieldIsHealthyNotBroken() throws {
        let m = try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: manifestJSON(degraded: "null"))
        XCTAssertEqual(m.degradedSources, [])
    }

    // MARK: - The cache

    private func makeStore() throws -> BriefingStore {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)
        return BriefingStore(
            modelContainer: try ModelContainer(for: schema, configurations: [configuration]))
    }

    /// The trap task 21 found for word timings: a field that decodes on a
    /// live fetch and is gone after a relaunch, because the SwiftData model
    /// has no column for it. Home renders the RESTORED manifest at launch,
    /// so without this the notice would appear only while online.
    func testTheNoticeSurvivesTheCache() async throws {
        let store = try makeStore()
        let m = try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: manifestJSON(degraded: #"["email","calendar"]"#))
        try await store.save(m, localAudioPath: nil)

        let restored = try await store.storedBriefing(id: m.briefingId)
        XCTAssertEqual(restored?.manifest.degradedSources, ["email", "calendar"])
    }

    func testTheFallbackFlagSurvivesTheCache() async throws {
        let store = try makeStore()
        let m = BriefingManifest(
            briefingId: "2026-10-10-0535", generatedAt: Date(timeIntervalSince1970: 0),
            durationMs: 1000, audioUrl: "/a", segments: [], turns: [], sources: [],
            narratorFallback: true)
        try await store.save(m, localAudioPath: nil)

        let restored = try await store.storedBriefing(id: m.briefingId)
        XCTAssertEqual(restored?.manifest.narratorFallback, true)
    }

    func testAHealthyReplacementClearsTheCachedNotice() async throws {
        // The retry exists so a token that comes back at 06:00 heals the
        // morning. If the cache kept the old notice, the healed briefing
        // would still say it had no inbox.
        let store = try makeStore()
        let id = "2026-09-01-0535"
        try await store.save(
            try JSONDecoder.mercury().decode(
                BriefingManifest.self,
                from: manifestJSON(id: id, degraded: #"["email"]"#)),
            localAudioPath: nil)
        try await store.save(
            try JSONDecoder.mercury().decode(
                BriefingManifest.self, from: manifestJSON(id: id)),
            localAudioPath: nil)

        let restored = try await store.storedBriefing(id: id)
        XCTAssertEqual(restored?.manifest.degradedSources, [])
    }
}
