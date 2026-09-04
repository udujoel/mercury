import XCTest
@testable import Mercury

/// The script view's arithmetic.
///
/// Pulled out of the view for the reason `PlayerTransportTests` documents: a
/// `View` cannot be asserted on, and every one of these has an off-by-one in
/// it — which line is speaking, how far through the segments you are, when a
/// speaker name is drawn.
final class TranscriptScriptTests: XCTestCase {

    private func turn(_ speaker: String, _ text: String, _ startMs: Int) -> Turn {
        Turn(speaker: speaker, segment: "world", text: text,
             startMs: startMs, endMs: startMs + 4_000)
    }

    private var script: [Turn] {
        [
            turn("Maya", "…and it closed on Tuesday.", 0),
            turn("Alex", "Northwind paid four point two billion.", 4_000),
            turn("Alex", "For a company nobody had heard of.", 8_000),
            turn("Maya", "That is a lot with no revenue.", 12_000),
        ]
    }

    // MARK: - Three states

    func testTheLineBeingSpokenIsTheOnlyOneSpeaking() {
        XCTAssertEqual(TranscriptScript.state(ofTurnAt: 1, activeIndex: 1), .speaking)
    }

    func testEarlierLinesAreSpokenAndLaterOnesAreNot() {
        XCTAssertEqual(TranscriptScript.state(ofTurnAt: 0, activeIndex: 2), .spoken)
        XCTAssertEqual(TranscriptScript.state(ofTurnAt: 3, activeIndex: 2), .unspoken)
    }

    /// Before playback starts there is no active line, and nothing has been
    /// heard — so the whole script reads as ahead of you rather than behind.
    func testWithNothingPlayingEveryLineIsUnspoken() {
        XCTAssertEqual(TranscriptScript.state(ofTurnAt: 0, activeIndex: nil), .unspoken)
        XCTAssertEqual(TranscriptScript.state(ofTurnAt: 3, activeIndex: nil), .unspoken)
    }

    /// `finish()` deliberately keeps `currentTurn` set so the last line
    /// stays on screen rather than the transcript blanking. Without the
    /// flag, that line stayed lit as speaking over silence and VoiceOver
    /// went on announcing "Now playing".
    func testOnceTheBriefingHasFinishedNothingIsStillSpeaking() {
        XCTAssertEqual(
            TranscriptScript.state(ofTurnAt: 3, activeIndex: 3, isFinished: true), .spoken)
    }

    func testOnceFinishedNothingIsStillAheadOfYouEither() {
        XCTAssertEqual(
            TranscriptScript.state(ofTurnAt: 0, activeIndex: 3, isFinished: true), .spoken)
        XCTAssertEqual(
            TranscriptScript.state(ofTurnAt: 3, activeIndex: 0, isFinished: true), .spoken)
    }

    // MARK: - Which line is active

    /// By identity, from the turn the coordinator already resolved — not by
    /// re-deriving it from `positionMs`. The coordinator's rule includes the
    /// awkward case where a position lands in the silence between two turns
    /// and has to resolve forward; a second implementation here would be free
    /// to disagree with it.
    func testTheActiveIndexComesFromTheCoordinatorsOwnTurn() {
        XCTAssertEqual(TranscriptScript.activeIndex(of: script[2], in: script), 2)
    }

    func testNoActiveTurnMeansNoActiveIndex() {
        XCTAssertNil(TranscriptScript.activeIndex(of: nil, in: script))
    }

    /// A turn from a different manifest — a restored briefing whose audio was
    /// replaced underneath it. Nil rather than a wrong index.
    func testATurnThatIsNotInThisScriptHasNoIndex() {
        XCTAssertNil(
            TranscriptScript.activeIndex(of: turn("Maya", "A line from elsewhere.", 0),
                                         in: script))
    }

    // MARK: - Speaker labels

    func testTheFirstLineAlwaysCarriesItsSpeaker() {
        XCTAssertTrue(TranscriptScript.showsSpeaker(at: 0, in: script))
    }

    func testASpeakerChangeCarriesALabelAndAContinuationDoesNot() {
        XCTAssertTrue(TranscriptScript.showsSpeaker(at: 1, in: script))
        XCTAssertFalse(TranscriptScript.showsSpeaker(at: 2, in: script))
        XCTAssertTrue(TranscriptScript.showsSpeaker(at: 3, in: script))
    }

    func testAnIndexOffTheEndIsNotACrash() {
        XCTAssertFalse(TranscriptScript.showsSpeaker(at: 99, in: script))
        XCTAssertFalse(TranscriptScript.showsSpeaker(at: 0, in: []))
    }

    // MARK: - Where you are

    private var segments: [BriefingSegment] {
        [
            BriefingSegment(id: "cold_open", label: "Cold open", startMs: 0),
            BriefingSegment(id: "calendar", label: "Your day", startMs: 20_000),
            BriefingSegment(id: "world", label: "Your world", startMs: 60_000),
        ]
    }

    func testPositionIsOneBasedBecauseAPersonReadsIt() {
        let p = TranscriptScript.position(ofSegment: "world", in: segments)
        XCTAssertEqual(p?.index, 3)
        XCTAssertEqual(p?.total, 3)
    }

    /// A manifest whose turns name a segment its own `segments` array omits.
    /// The backend should not produce one; if it ever does, the caption drops
    /// the position rather than the screen dropping the caption.
    func testASegmentMissingFromTheListHasNoPosition() {
        XCTAssertNil(TranscriptScript.position(ofSegment: "sign_off", in: segments))
        XCTAssertEqual(
            TranscriptScript.segmentCaption(label: "Sign-off", position: nil), "Sign-off")
    }

    func testTheCaptionReadsAsALabelAndAPosition() {
        XCTAssertEqual(
            TranscriptScript.segmentCaption(label: "Your world", position: (3, 7)),
            "Your world · 3 of 7")
    }

    func testNoLabelMeansNoCaptionAtAll() {
        XCTAssertNil(TranscriptScript.segmentCaption(label: nil, position: (3, 7)))
    }
}
