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

    // MARK: - The word-level sweep

    private func timed(_ text: String, _ spans: [(String, Int, Int)]) -> Turn {
        Turn(speaker: "Maya", segment: "world", text: text,
             startMs: 0, endMs: 10_000,
             words: spans.map { WordSpan(word: $0.0, startMs: $0.1, endMs: $0.2) })
    }

    private var line: Turn {
        timed("Northwind paid four billion.",
              [("Northwind", 0, 500), ("paid", 500, 800),
               ("four", 800, 1_100), ("billion.", 1_100, 1_600)])
    }

    func testTheSweepCoversEveryWordThatHasStarted() {
        let r = TranscriptScript.spokenRange(of: line, atMs: 900)
        XCTAssertEqual(String(line.text[r!]), "Northwind paid four")
    }

    /// A word stays lit until the NEXT one starts. Positions land in the
    /// gaps between words constantly at 20Hz, and a highlight that blinks
    /// off between every word is worse than no highlight at all.
    func testAPositionInTheGapBetweenWordsKeepsTheLastOneLit() {
        // 650ms is inside "paid"; 810 is after "four" begins.
        XCTAssertEqual(
            String(line.text[TranscriptScript.spokenRange(of: line, atMs: 650)!]),
            "Northwind paid")
        XCTAssertEqual(
            String(line.text[TranscriptScript.spokenRange(of: line, atMs: 810)!]),
            "Northwind paid four")
    }

    /// Empty, not nil. Nil is reserved for "this line has no timings at
    /// all", and collapsing the two rendered a line that had not started
    /// yet as fully lit — brightest exactly when nothing had been said.
    func testBeforeTheFirstWordTheSweepIsEmptyRatherThanAbsent() {
        let r = TranscriptScript.spokenRange(of: line, atMs: -1)
        XCTAssertNotNil(r)
        XCTAssertTrue(r!.isEmpty)
    }

    /// A line ending in a punctuation-only token has no timed word reaching
    /// the end of its text, so the last characters stayed muted for good.
    func testTrailingUntimedPunctuationIsLitOnceTheTurnIsOver() {
        let turn = Turn(speaker: "Maya", segment: "world", text: "Really —",
                        startMs: 0, endMs: 1_000,
                        words: [WordSpan(word: "Really", startMs: 0, endMs: 500)])

        let mid = TranscriptScript.spokenRange(of: turn, atMs: 600)
        XCTAssertEqual(String(turn.text[mid!]), "Really")

        let after = TranscriptScript.spokenRange(of: turn, atMs: 1_000)
        XCTAssertEqual(String(turn.text[after!]), "Really —")
    }

    func testPastTheEndTheWholeLineIsLit() {
        let r = TranscriptScript.spokenRange(of: line, atMs: 99_000)
        XCTAssertEqual(String(line.text[r!]), line.text)
    }

    /// Google, and every briefing generated before ElevenLabs existed. The
    /// view falls back to lighting the whole line, which is what it did for
    /// all of them until now.
    func testAManifestWithoutWordTimingsHasNoSweep() {
        let plain = Turn(speaker: "Maya", segment: "world", text: "No timings here.",
                         startMs: 0, endMs: 4_000)
        XCTAssertNil(TranscriptScript.spokenRange(of: plain, atMs: 2_000))
        XCTAssertNil(TranscriptScript.spokenRange(of: timed("x", []), atMs: 2_000))
    }

    // MARK: - Matching timed words back onto the line

    /// Sequentially, not by re-tokenising. Punctuation-only tokens are
    /// dropped on the way out of the provider, so the word list and a fresh
    /// `split` do not line up — and re-tokenising would put the highlight
    /// on the wrong word from the first em dash onward.
    func testWordsAreFoundInOrderEvenWhenTheTextHasTokensTheTimingsSkip() {
        let text = "Yes — really, four."
        let ranges = TranscriptScript.wordRanges(
            in: text,
            words: [WordSpan(word: "Yes", startMs: 0, endMs: 1),
                    WordSpan(word: "really,", startMs: 1, endMs: 2),
                    WordSpan(word: "four.", startMs: 2, endMs: 3)])

        XCTAssertEqual(ranges.map { $0.map { String(text[$0]) } },
                       ["Yes", "really,", "four."])
    }

    /// A word that appears twice matches its OWN occurrence, not the first
    /// one — the cursor only moves forward.
    func testARepeatedWordMatchesTheRightOccurrence() {
        let text = "four and four again"
        let ranges = TranscriptScript.wordRanges(
            in: text,
            words: [WordSpan(word: "four", startMs: 0, endMs: 1),
                    WordSpan(word: "and", startMs: 1, endMs: 2),
                    WordSpan(word: "four", startMs: 2, endMs: 3)])

        let second = ranges[2]!
        XCTAssertEqual(text.distance(from: text.startIndex, to: second.lowerBound), 9)
    }

    /// Should not happen — the timings came from this exact string — but a
    /// word that cannot be found must not crash or silently shift the
    /// highlight onto its neighbour.
    func testAWordThatIsNotInTheTextYieldsNilRatherThanAWrongRange() {
        let text = "one two"
        let ranges = TranscriptScript.wordRanges(
            in: text,
            words: [WordSpan(word: "one", startMs: 0, endMs: 1),
                    WordSpan(word: "elephant", startMs: 1, endMs: 2),
                    WordSpan(word: "two", startMs: 2, endMs: 3)])

        XCTAssertNil(ranges[1])
        XCTAssertEqual(ranges[2].map { String(text[$0]) }, "two")
    }

    // MARK: - What the sweep does when a word cannot be located

    /// A word missing from the text does not hold the sweep back: later
    /// words that WERE found still extend it, because the boundary only
    /// ever sits at a position actually located in the text.
    func testAnUnlocatableWordDoesNotStopLaterOnesFromExtendingTheSweep() {
        let turn = Turn(speaker: "Maya", segment: "world", text: "one two four",
                        startMs: 0, endMs: 9_000,
                        words: [WordSpan(word: "one", startMs: 0, endMs: 100),
                                WordSpan(word: "two", startMs: 100, endMs: 200),
                                WordSpan(word: "three", startMs: 200, endMs: 300),
                                WordSpan(word: "four", startMs: 300, endMs: 400)])

        XCTAssertEqual(String(turn.text[TranscriptScript.spokenRange(of: turn, atMs: 350)!]),
                       "one two four")
    }

    /// When the words that have started cannot be located at all, the sweep
    /// LAGS at the last confirmed position rather than guessing forward.
    /// Under-highlighting is a degradation; highlighting the wrong word is
    /// the bug the feature exists to avoid.
    func testWhenNothingRecentCanBeLocatedTheSweepLagsRatherThanGuesses() {
        let turn = Turn(speaker: "Maya", segment: "world", text: "one two",
                        startMs: 0, endMs: 9_000,
                        words: [WordSpan(word: "one", startMs: 0, endMs: 100),
                                WordSpan(word: "two", startMs: 100, endMs: 200),
                                WordSpan(word: "three", startMs: 200, endMs: 300),
                                WordSpan(word: "four", startMs: 300, endMs: 400)])

        XCTAssertEqual(String(turn.text[TranscriptScript.spokenRange(of: turn, atMs: 350)!]),
                       "one two")
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
