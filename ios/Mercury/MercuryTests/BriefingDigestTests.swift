import XCTest
@testable import Mercury

/// Home's rows used to be fiction.
///
/// `WeatherCard` hardcoded "64°", "Partly cloudy" and "San Francisco";
/// `InboxCard` invented "Priya Shah" and "Invoice #4471"; `CalendarCard`
/// invented a standup and a design review. All three shipped as `static let
/// placeholder` arrays behind defaulted initialisers, so a caller that
/// supplied nothing got a plausible, entirely fabricated morning — read at
/// 7am as the user's own life.
///
/// Everything this file asserts comes down to one rule: a number on the Home
/// screen must be countable in the manifest, and where it is not countable
/// the screen says so in words rather than filling the gap.
final class BriefingDigestTests: XCTestCase {

    // MARK: - Fixtures

    /// A briefing with weather, calendar, inbox and world segments, and a
    /// source trail that attributes two events, three emails and four
    /// news articles (two of which are the same story from two outlets).
    private func manifest(
        durationMs: Int = 372_000,
        segments: [BriefingSegment]? = nil,
        turns: [Turn]? = nil,
        sources: [SourceRef]? = nil
    ) -> BriefingManifest {
        let defaultSegments = [
            BriefingSegment(id: "cold_open", label: "Good morning", startMs: 0),
            BriefingSegment(id: "weather", label: "Weather", startMs: 20_000),
            BriefingSegment(id: "calendar", label: "Your day", startMs: 50_000),
            BriefingSegment(id: "inbox", label: "Your inbox", startMs: 140_000),
            BriefingSegment(id: "world", label: "Your world", startMs: 240_000),
            BriefingSegment(id: "sign_off", label: "Sign-off", startMs: 350_000),
        ]
        // One turn per segment keeps turn indices readable: index 0 is
        // cold_open, 1 weather, 2 calendar, 3 inbox, 4 world, 5 sign_off.
        let defaultTurns = [
            turn("cold_open", 0),
            turn("weather", 20_000),
            turn("calendar", 50_000),
            turn("inbox", 140_000),
            turn("world", 240_000),
            turn("sign_off", 350_000),
        ]
        let defaultSources = [
            source(2, "Standup", "Calendar", "calendar"),
            source(2, "Design review", "Calendar", "calendar"),
            source(3, "Q3 planning doc", "Priya Shah", "email"),
            source(3, "Invoice due Friday", "Billing", "email"),
            source(3, "Re: onboarding", "Sam Ortiz", "email"),
            source(4, "Chip export rules tighten", "Reuters", "rss", clusterSize: 2),
            source(4, "Export controls widen", "The Verge", "rss", clusterSize: 2),
            source(4, "Rate decision holds", "FT", "rss"),
            source(4, "Fusion milestone", "Nature", "rss"),
        ]
        return BriefingManifest(
            briefingId: "2026-09-04",
            generatedAt: Date(timeIntervalSince1970: 1_788_000_000),
            durationMs: durationMs,
            audioUrl: "https://cdn.example.com/a.m4a",
            segments: segments ?? defaultSegments,
            turns: turns ?? defaultTurns,
            sources: sources ?? defaultSources
        )
    }

    private func turn(_ segment: String, _ startMs: Int) -> Turn {
        Turn(speaker: "A", segment: segment, text: "…",
             startMs: startMs, endMs: startMs + 10_000)
    }

    private func source(_ turnIndex: Int, _ title: String, _ name: String,
                        _ type: String, clusterSize: Int = 1,
                        itemId: String? = nil) -> SourceRef {
        SourceRef(turnIndex: turnIndex, title: title, url: nil,
                  sourceName: name, sourceType: type, clusterSize: clusterSize,
                  itemId: itemId)
    }

    // MARK: - Which day the briefing is for

    private func day(_ s: String) -> Date {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.date(from: s)!
    }

    /// The regression this exists for. ADR-001 generates overnight, so the
    /// briefing FOR the 4th is made at 23:30 on the 3rd. Comparing
    /// `generatedAt` to the calendar day labels the morning's own briefing
    /// "Thursday's" — the bug this labelling was added to fix, one field over.
    func testABriefingGeneratedLastNightIsStillTodaysBriefing() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: "2026-09-04",
                                 generatedAt: day("2026-09-03 23:30"),
                                 now: day("2026-09-04 07:00")),
            "Today's briefing")
    }

    func testAnOlderBriefingIsNamedByItsDay() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: "2026-09-03",
                                 generatedAt: day("2026-09-02 23:30"),
                                 now: day("2026-09-04 07:00")),
            "Wednesday's briefing")
    }

    /// Nothing on the device yet: the card invites you to today's, because
    /// that is what tapping it fetches.
    func testNoManifestReadsAsToday() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: nil, generatedAt: nil,
                                 now: day("2026-09-04 07:00")),
            "Today's briefing")
    }

    // MARK: - Two meetings with the same name are two meetings

    /// `_build_source_refs` matches a calendar event BY TITLE, and every
    /// calendar ref carries the same `source_name` of "Calendar". So before
    /// `item_id` existed the dedup key could not tell two events apart at
    /// all, and a day with two "1:1"s reported one event. Recurring meetings
    /// make that ordinary rather than exotic.
    func testTwoCalendarEventsSharingATitleAreCountedTwice() {
        let m = manifest(sources: [
            source(2, "1:1", "Calendar", "calendar", itemId: "ev-morning"),
            source(2, "1:1", "Calendar", "calendar", itemId: "ev-afternoon"),
        ])

        XCTAssertEqual(row("calendar", in: manifestRows(m))?.detail, "2 events")
    }

    /// The same two events in a manifest generated before the field existed —
    /// every briefing already in R2. They still collapse to one, and that is
    /// the documented limit of the fallback rather than a behaviour to
    /// preserve: the point of this test is that such a manifest keeps
    /// decoding and rendering at all.
    func testAManifestWithoutItemIdsStillRenders() {
        let m = manifest(sources: [
            source(2, "1:1", "Calendar", "calendar"),
            source(2, "1:1", "Calendar", "calendar"),
        ])

        XCTAssertEqual(row("calendar", in: manifestRows(m))?.detail, "1 event")
    }

    /// Distinct titles never depended on the id, and must not start to.
    func testDistinctCalendarTitlesAreStillCountedSeparatelyWithoutIds() {
        let m = manifest(sources: [
            source(2, "Standup", "Calendar", "calendar"),
            source(2, "Design review", "Calendar", "calendar"),
        ])

        XCTAssertEqual(row("calendar", in: manifestRows(m))?.detail, "2 events")
    }

    private func manifestRows(_ m: BriefingManifest) -> [BriefingDigest.Row] {
        BriefingDigest.rows(for: m)
    }

    private func row(_ id: String, in rows: [BriefingDigest.Row]) -> BriefingDigest.Row? {
        rows.first { $0.id == id }
    }

    // MARK: - Counts are counted, not invented

    func testEveryRowDetailIsCountedFromTheSourceTrail() {
        let rows = BriefingDigest.rows(for: manifest())

        XCTAssertEqual(row("calendar", in: rows)?.detail, "2 events")
        XCTAssertEqual(row("inbox", in: rows)?.detail, "3 emails")
        XCTAssertEqual(row("world", in: rows)?.detail, "4 sources")
    }

    func testASourceMentionedInTwoTurnsIsCountedOnce() {
        // `_build_source_refs` in the backend emits one ref per (turn, item)
        // pair, so an email the hosts come back to later appears twice in
        // `sources`. Counting rows naively would report four emails where
        // three arrived.
        var sources = [
            source(3, "Q3 planning doc", "Priya Shah", "email"),
            source(3, "Invoice due Friday", "Billing", "email"),
            source(3, "Re: onboarding", "Sam Ortiz", "email"),
        ]
        sources.append(source(3, "Q3 planning doc", "Priya Shah", "email"))
        let rows = BriefingDigest.rows(for: manifest(sources: sources))

        XCTAssertEqual(row("inbox", in: rows)?.detail, "3 emails")
    }

    func testSingularNounForASingleItem() {
        let rows = BriefingDigest.rows(for: manifest(sources: [
            source(2, "Standup", "Calendar", "calendar"),
            source(3, "Q3 planning doc", "Priya Shah", "email"),
        ]))

        XCTAssertEqual(row("calendar", in: rows)?.detail, "1 event")
        XCTAssertEqual(row("inbox", in: rows)?.detail, "1 email")
    }

    // MARK: - Empty states are words, not zeroes and not fiction

    func testASegmentTheBriefingDoesNotContainReadsAsAnEmptyState() {
        // A briefing generated on a day with no meetings has no calendar
        // segment at all. The old CalendarCard would have shown a standup,
        // a 1:1 with Priya and a design review anyway.
        let rows = BriefingDigest.rows(for: manifest(
            segments: [
                BriefingSegment(id: "cold_open", label: "Good morning", startMs: 0),
                BriefingSegment(id: "inbox", label: "Your inbox", startMs: 20_000),
            ],
            turns: [turn("cold_open", 0), turn("inbox", 20_000)],
            sources: [source(1, "Q3 planning doc", "Priya Shah", "email")]
        ))

        let calendar = row("calendar", in: rows)
        XCTAssertEqual(calendar?.detail, "Nothing scheduled.")
        XCTAssertNil(calendar?.seekMs,
                     "a segment that is not in the briefing has nowhere to seek to")
        XCTAssertEqual(row("world", in: rows)?.detail, "No news this morning.")
        XCTAssertEqual(row("weather", in: rows)?.detail, "No forecast.")
    }

    func testNoManifestMeansNoRowsRatherThanEmptyPlaceholders() {
        // Before a briefing has been fetched there is nothing to summarise.
        // Rows full of "Nothing scheduled." would be a claim about the
        // user's day that the app has not made a single request to support.
        XCTAssertTrue(BriefingDigest.rows(for: nil).isEmpty)
        XCTAssertNil(BriefingDigest.summary(for: nil))
    }

    func testASegmentPresentButUnattributedFallsBackToItsLengthNotAnEmptyState() {
        // Weather is the standing case: the backend never emits a weather
        // SourceRef, so the weather segment always has zero attributed
        // sources. "No forecast." would be false — the briefing has one, it
        // just is not in the source trail — so the row reports what it can
        // prove: how long the hosts spend on it.
        let rows = BriefingDigest.rows(for: manifest())

        XCTAssertEqual(row("weather", in: rows)?.detail, "30 sec")
        XCTAssertEqual(row("weather", in: rows)?.seekMs, 20_000)
    }

    // MARK: - Order and identity

    func testRowsFollowTheOrderTheBriefingIsSpokenIn() {
        // The rows sit under "IN TODAY'S BRIEFING" and each one seeks to its
        // segment, so listing them out of order would misdescribe the audio.
        let rows = BriefingDigest.rows(for: manifest())
        let present = rows.filter { $0.seekMs != nil }.map(\.id)

        XCTAssertEqual(present, ["weather", "calendar", "inbox", "world"])
    }

    func testFramingSegmentsAreNotRows() {
        // The cold open and the sign-off are how the briefing starts and
        // stops, not things in it.
        let ids = BriefingDigest.rows(for: manifest()).map(\.id)

        XCTAssertFalse(ids.contains("cold_open"))
        XCTAssertFalse(ids.contains("sign_off"))
    }

    func testAbsentStandingRowsComeAfterThePresentOnes() {
        let rows = BriefingDigest.rows(for: manifest(
            segments: [BriefingSegment(id: "world", label: "Your world", startMs: 0)],
            turns: [turn("world", 0)],
            sources: []
        ))

        XCTAssertEqual(rows.map(\.id), ["world", "weather", "calendar", "inbox"])
    }

    func testLabelsComeFromTheManifestNotFromTheClient() {
        // If the backend renames a segment, Home follows. Hardcoding
        // "Your day" here would make the row and the chapter ribbon in the
        // player disagree about what the same audio is called.
        let rows = BriefingDigest.rows(for: manifest(
            segments: [BriefingSegment(id: "calendar", label: "Today's diary", startMs: 0)],
            turns: [turn("calendar", 0)],
            sources: []
        ))

        XCTAssertEqual(row("calendar", in: rows)?.label, "Today's diary")
    }

    // MARK: - The header summary

    func testSummaryIsDurationAndDistinctSourceCount() {
        XCTAssertEqual(BriefingDigest.summary(for: manifest()), "6 min · 9 sources")
    }

    func testSummaryDropsTheSourceCountWhenTheTrailIsEmpty() {
        // An empty grounding trail is a backend warning, not something to
        // render as "0 sources".
        XCTAssertEqual(BriefingDigest.summary(for: manifest(sources: [])), "6 min")
    }

    func testDurationRoundsToTheNearestMinuteAndFallsBackToSeconds() {
        XCTAssertEqual(BriefingDigest.durationLabel(ms: 372_000), "6 min")
        XCTAssertEqual(BriefingDigest.durationLabel(ms: 90_000), "2 min")   // 1.5 rounds up
        XCTAssertEqual(BriefingDigest.durationLabel(ms: 60_000), "1 min")
        XCTAssertEqual(BriefingDigest.durationLabel(ms: 29_000), "29 sec")
        XCTAssertEqual(BriefingDigest.durationLabel(ms: 0), "0 sec")
        XCTAssertEqual(BriefingDigest.durationLabel(ms: -5), "0 sec",
                       "a negative duration is a decoding failure, not a negative label")
    }

    // MARK: - Robustness against a manifest the client did not generate

    func testASourceIndexPastTheEndOfTheTurnsIsIgnoredRatherThanCrashing() {
        // `turn_index` is guaranteed against the compacted turn list today.
        // A stale cached manifest is not covered by that guarantee, and
        // `BriefingManifest.turn(atSourceIndex:)` already returns nil rather
        // than trapping — this proves the digest respects that.
        let rows = BriefingDigest.rows(for: manifest(sources: [
            source(999, "From the future", "Nowhere", "email"),
            source(3, "Q3 planning doc", "Priya Shah", "email"),
        ]))

        XCTAssertEqual(row("inbox", in: rows)?.detail, "1 email")
    }

    func testUnrenderedTurnsDoNotShiftSourceAttribution() {
        // `SourceRef.turnIndex` indexes the COMPACTED turn list, which is
        // what `turn(atSourceIndex:)` reads. A turn whose TTS failed carries
        // sentinel timing and is filtered out; indexing the raw array would
        // attribute every later source to the wrong segment.
        let turns = [
            Turn(speaker: "A", segment: "cold_open", text: "…", startMs: -1, endMs: -1),
            turn("calendar", 0),
            turn("inbox", 50_000),
        ]
        let rows = BriefingDigest.rows(for: manifest(
            segments: [
                BriefingSegment(id: "calendar", label: "Your day", startMs: 0),
                BriefingSegment(id: "inbox", label: "Your inbox", startMs: 50_000),
            ],
            turns: turns,
            sources: [source(0, "Standup", "Calendar", "calendar")]
        ))

        XCTAssertEqual(row("calendar", in: rows)?.detail, "1 event",
                       "source 0 belongs to the first RENDERED turn, which is calendar")
        XCTAssertEqual(row("inbox", in: rows)?.detail, "5 min",
                       "and nothing should have leaked into inbox")
    }
}

/// `LoadState.failed` carries `error.localizedDescription`, and
/// `APIError.errorDescription` interpolates its underlying error with
/// `\(error)` — the *debug* description. So Home rendered
/// `Error Domain=NSURLErrorDomain Code=-1004 "Could not connect to the
/// server." UserInfo={_kCFStreamErrorCodeKey=61, …}` verbatim, in red, at
/// caption size, on the screen a person opens at 7am. Seen on the simulator,
/// not imagined.
/// Labelling ids now that a day can hold several briefings.
final class TimedBriefingIdTests: XCTestCase {

    func testATimeIsReadStraightFromTheDigits() {
        XCTAssertEqual(ArchiveView.timeOfDay(in: "2026-09-07-1612"), "16:12")
        XCTAssertEqual(ArchiveView.timeOfDay(in: "2026-09-07-0000"), "00:00")
    }

    /// Every briefing generated before ids carried a time is day-only, and
    /// there are fourteen of them live in R2. They must label as they always
    /// did rather than growing a spurious ", 00:00".
    func testADayOnlyIdHasNoTime() {
        XCTAssertNil(ArchiveView.timeOfDay(in: "2026-09-07"))
    }

    /// Not a time, and must not be rendered as one.
    func testMalformedSuffixesAreNotTimes() {
        for id in ["2026-09-07-16", "2026-09-07-161234", "2026-09-07-16aa",
                   "2026-09-07x1612"] {
            XCTAssertNil(ArchiveView.timeOfDay(in: id), id)
        }
    }

    /// An id it cannot read at all is shown RAW rather than blank — a label
    /// that does not parse is a bug worth seeing, and an empty row is one
    /// you cannot.
    func testAnUnreadableIdFallsBackToItself() {
        XCTAssertEqual(ArchiveView.display("not-an-id"), "not-an-id")
    }
}

final class HomeErrorPresentationTests: XCTestCase {

    func testANetworkFailureReadsAsASentence() {
        let raw = "Network error: Error Domain=NSURLErrorDomain Code=-1004 "
            + "\"Could not connect to the server.\" UserInfo={_kCFStreamErrorCodeKey=61}"
        let shown = HomeErrorPresentation.present(raw)

        XCTAssertEqual(shown.headline, "Mercury couldn't reach the server.")
        XCTAssertNil(shown.detail, "the dump belongs in the console, not on the page")
    }

    func testADecodingFailureReadsAsASentence() {
        let raw = "Could not decode response: keyNotFound(CodingKeys(stringValue: "
            + "\"duration_ms\", intValue: nil), Swift.DecodingError.Context(codingPath: []))"
        let shown = HomeErrorPresentation.present(raw)

        XCTAssertEqual(shown.headline,
                       "Today's briefing arrived in a format this app can't read.")
        XCTAssertNil(shown.detail)
    }

    func testTheSentenceAppStateWritesForTheUserIsPromotedWhole() {
        // `AppState.fetchTodayOrGenerate` turns the read-only Worker's 405
        // into a 404 whose body it wrote for a reader. That is the one
        // message in the stack already fit to show, so it is shown.
        let written = "Today's briefing hasn't been generated yet. It's produced "
            + "automatically each morning — try again shortly."
        let shown = HomeErrorPresentation.present("Backend returned 404: " + written)

        XCTAssertEqual(shown.headline, written)
        XCTAssertNil(shown.detail)
    }

    func testAnUnrecognisedFailureKeepsItsTextAsAQuietDetail() {
        // Legible headline, and the raw string still on screen so it can be
        // reported — the fallback must not throw away information the way a
        // bare "Something went wrong" does.
        let shown = HomeErrorPresentation.present("Backend returned 500: upstream exploded")

        XCTAssertEqual(shown.headline, "Today's briefing didn't load.")
        XCTAssertEqual(shown.detail, "Backend returned 500: upstream exploded")
    }

    func testAnEmptyMessageProducesNoEmptyDetailLine() {
        let shown = HomeErrorPresentation.present("")

        XCTAssertEqual(shown.headline, "Today's briefing didn't load.")
        XCTAssertNil(shown.detail)
    }

    func testAnEmptyBodyOnAPromotedPrefixFallsBackRatherThanShowingNothing() {
        // A 404 with no body would otherwise promote an empty string into
        // the headline and leave the card blank.
        let shown = HomeErrorPresentation.present("Backend returned 404: ")

        XCTAssertEqual(shown.headline, "Today's briefing didn't load.")
    }
}

/// The fourth site of the id-shape bug, found on the simulator.
///
/// `title` compared a briefing id against a bare `yyyy-MM-dd`. That was the
/// same question until 2026-09-07 and a different one since: today's
/// briefing is `2026-09-13-0535`, today is `2026-09-13`, they are never
/// equal — so today's own briefing was titled "Sunday's briefing" every day.
final class BriefingTitleDayTests: XCTestCase {

    private let sunday = DateComponents(
        calendar: Calendar(identifier: .gregorian), timeZone: .gmt,
        year: 2026, month: 9, day: 13, hour: 9).date!

    func testTodaysTimedBriefingIsCalledTodays() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: "2026-09-13-0535",
                                 generatedAt: sunday, now: sunday),
            "Today's briefing",
            "a timed id for today is still today")
    }

    func testALegacyBareDayIdForTodayIsStillTodays() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: "2026-09-13",
                                 generatedAt: sunday, now: sunday),
            "Today's briefing")
    }

    /// The guard must still hold, or the fix is just its removal.
    func testAnotherDayIsNamedByItsWeekday() {
        let friday = DateComponents(
            calendar: Calendar(identifier: .gregorian), timeZone: .gmt,
            year: 2026, month: 9, day: 11, hour: 9).date!
        XCTAssertEqual(
            BriefingDigest.title(briefingId: "2026-09-11-0535",
                                 generatedAt: friday, now: sunday),
            "Friday's briefing")
    }

    func testNoBriefingReadsAsToday() {
        XCTAssertEqual(
            BriefingDigest.title(briefingId: nil, generatedAt: nil, now: sunday),
            "Today's briefing")
    }
}
