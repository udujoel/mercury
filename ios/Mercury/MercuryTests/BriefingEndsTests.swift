import XCTest
import MediaPlayer
@testable import Mercury

/// The briefing has to END.
///
/// Reported 2026-09-12 after listening to one all the way through: "when the
/// narration is done, the podcast doesn't seem to stop. it still looks like
/// its playing, even on the lock screen."
///
/// Three defects shared that one path, and the reason none of them was
/// caught is the same in each case — every one is an ABSENCE. There was no
/// `clear()`, no `setActive(false)`, and no navigation edge back to Home.
/// Nothing was wrong with the code that existed; the code that should have
/// existed was never written, and a test suite only tests what is there.
final class BriefingEndsTests: XCTestCase {

    private let center = MPNowPlayingInfoCenter.default()

    override func tearDown() {
        center.nowPlayingInfo = nil
        super.tearDown()
    }

    // MARK: - The lock-screen card

    /// Rate 0 is PAUSED, and the difference is the whole bug.
    ///
    /// `finish()` reported the end of the briefing by setting the playback
    /// rate to 0, which is exactly how iOS spells a paused podcast — so the
    /// card stayed on the lock screen with a play button on it, for a
    /// briefing that had finished. This pins the distinction: after the old
    /// treatment there is still an item, and after `clear()` there is none.
    func testClearRemovesTheCardWhereRateZeroOnlyPausesIt() {
        let service = NowPlayingService()
        service.update(title: "Sign-off", briefing: "Friday",
                       durationMs: 490_000, positionMs: 489_000,
                       rate: 1.0, isPlaying: true)
        XCTAssertNotNil(center.nowPlayingInfo)

        // What the old finish() did, and all it did.
        service.setPlaybackState(isPlaying: false, positionMs: 490_000, rate: 1.0)
        XCTAssertNotNil(
            center.nowPlayingInfo,
            "rate 0 leaves the card up — this is the reported bug, not the fix")
        XCTAssertEqual(
            center.nowPlayingInfo?[MPNowPlayingInfoPropertyPlaybackRate] as? Double, 0.0)

        service.clear()
        XCTAssertNil(center.nowPlayingInfo,
                     "a finished briefing leaves no card on the lock screen")
    }

    /// Clearing twice, and clearing something never published, must be safe:
    /// `finish()` is reachable from a failed schedule as well as from the
    /// end of the audio.
    func testClearingIsIdempotentAndSafeWhenNothingWasPublished() {
        let service = NowPlayingService()
        service.clear()
        service.clear()
        XCTAssertNil(center.nowPlayingInfo)
    }

    /// Clearing a card obliges you to own rebuilding it.
    ///
    /// `setPlaybackState` merges into `center.nowPlayingInfo ?? [:]`, so on a
    /// cleared card it would happily publish a card made of nothing but an
    /// elapsed time and a rate — no title, no briefing, no artwork. That is
    /// why the rebuild goes through `update` and not through a state poke.
    func testAStatePokeOnAClearedCardWouldPublishAnEmptyOne() {
        let service = NowPlayingService()
        service.clear()
        service.setPlaybackState(isPlaying: true, positionMs: 0, rate: 1.0)

        let info = center.nowPlayingInfo
        XCTAssertNotNil(info, "this is the trap, demonstrated")
        XCTAssertNil(info?[MPMediaItemPropertyTitle],
                     "…and it has no title, which is why replay must republish")
    }

    /// The republish path, end to end: a full card comes back.
    func testRepublishingAfterAClearRestoresTheWholeCard() throws {
        let service = NowPlayingService()
        service.update(title: "Your world", briefing: "Friday",
                       durationMs: 490_000, positionMs: 0,
                       rate: 1.0, isPlaying: false)
        service.clear()
        XCTAssertNil(center.nowPlayingInfo)

        service.update(title: "Your world", briefing: "Friday",
                       durationMs: 490_000, positionMs: 0,
                       rate: 1.0, isPlaying: false)
        let info = try XCTUnwrap(center.nowPlayingInfo)
        XCTAssertEqual(info[MPMediaItemPropertyTitle] as? String, "Your world")
        XCTAssertEqual(info[MPMediaItemPropertyArtist] as? String, "Friday")
        XCTAssertNotNil(info[MPMediaItemPropertyArtwork],
                        "the mark has to survive a replay too")
    }

    // MARK: - The rebuilt card names the right segment

    private func manifest(segments: [(String, String, Int)]) throws -> BriefingManifest {
        let segs = segments.map {
            "{\"id\":\"\($0.0)\",\"label\":\"\($0.1)\",\"start_ms\":\($0.2)}"
        }.joined(separator: ",")
        let json = """
        {"briefing_id":"2026-09-12-0535","generated_at":"2026-09-12T05:35:00Z",
         "duration_ms":490000,"audio_url":"/briefing/2026-09-12-0535/audio",
         "segments":[\(segs)],"turns":[],"sources":[]}
        """
        return try JSONDecoder.mercury().decode(
            BriefingManifest.self, from: Data(json.utf8))
    }

    private var sevenSegments: [(String, String, Int)] {
        [("cold_open", "Good morning", 8_000), ("weather", "Weather", 31_768),
         ("calendar", "Your day", 52_488), ("inbox", "Your inbox", 68_576),
         ("world", "Your world", 259_576), ("follow_ups", "Follow-ups", 442_416),
         ("sign_off", "Sign-off", 467_840)]
    }

    /// A replay from the top names the first segment.
    func testTheRebuiltCardNamesTheOpeningSegmentOnAReplay() throws {
        let m = try manifest(segments: sevenSegments)
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 0, in: m), "Good morning")
    }

    /// Resuming mid-briefing names the segment it resumes INTO. Naming the
    /// cold open there would put a card on the lock screen that contradicts
    /// the audio coming out of the speaker.
    func testTheRebuiltCardNamesTheSegmentItResumesInto() throws {
        let m = try manifest(segments: sevenSegments)
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 300_000, in: m), "Your world")
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 68_576, in: m), "Your inbox",
                       "exactly on a boundary belongs to the segment starting there")
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 489_000, in: m), "Sign-off")
    }

    /// Music offsets the first segment past zero (ADR-006), so a position
    /// before any segment start is reachable and must not return nil.
    func testAPositionInsideTheIntroMusicStillNamesASegment() throws {
        let m = try manifest(segments: sevenSegments)
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 0, in: m), "Good morning")
        XCTAssertEqual(PlaybackCoordinator.segmentLabel(at: 4_000, in: m), "Good morning")
    }

    /// A manifest with no segments must not crash the rebuild; the caller
    /// falls back to its own title.
    func testAManifestWithNoSegmentsYieldsNoLabel() throws {
        let m = try manifest(segments: [])
        XCTAssertNil(PlaybackCoordinator.segmentLabel(at: 0, in: m))
    }
}
