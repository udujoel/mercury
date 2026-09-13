import XCTest
@testable import Mercury

/// A week across the top of Home — roadmap task 19, asked for twice.
final class CalendarStripTests: XCTestCase {

    private var cal: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = .gmt
        return c
    }
    private func day(_ d: Int) -> Date {
        DateComponents(calendar: cal, timeZone: .gmt,
                       year: 2026, month: 9, day: d, hour: 9).date!
    }

    func testSevenDaysEndingToday() {
        let days = CalendarStrip.days(archive: [], today: day(13),
                                      calendar: cal, timeZone: .gmt)
        XCTAssertEqual(days.count, 7)
        XCTAssertEqual(days.first?.id, "2026-09-07")
        XCTAssertEqual(days.last?.id, "2026-09-13")
        XCTAssertTrue(days.last?.isToday == true)
    }

    /// The strip has to join onto TIMED ids, which is all the server has
    /// written since 2026-09-07.
    func testATimedIdIsFoundForItsDay() {
        let days = CalendarStrip.days(
            archive: ["2026-09-11-0535"], today: day(13),
            calendar: cal, timeZone: .gmt)
        XCTAssertEqual(days.first(where: { $0.id == "2026-09-11" })?.briefingId,
                       "2026-09-11-0535")
    }

    /// Four runs a day: "that day's briefing" is the latest of them, the
    /// same choice `briefingToPrefetch` makes.
    func testTheNewestOfADaysFourRunsWins() {
        let days = CalendarStrip.days(
            archive: ["2026-09-11-0535", "2026-09-11-2155", "2026-09-11-1213"],
            today: day(13), calendar: cal, timeZone: .gmt)
        XCTAssertEqual(days.first(where: { $0.id == "2026-09-11" })?.briefingId,
                       "2026-09-11-2155")
    }

    func testALegacyBareDayIdStillJoins() {
        let days = CalendarStrip.days(archive: ["2026-09-08"], today: day(13),
                                      calendar: cal, timeZone: .gmt)
        XCTAssertEqual(days.first(where: { $0.id == "2026-09-08" })?.briefingId,
                       "2026-09-08")
    }

    /// "Days with no briefing are visibly empty rather than
    /// tappable-and-dead" — the roadmap is specific, and a control that does
    /// nothing teaches people the app is broken.
    func testADayWithNothingHasNoBriefingToOpen() {
        let days = CalendarStrip.days(archive: ["2026-09-11-0535"],
                                      today: day(13), calendar: cal, timeZone: .gmt)
        let empty = days.first { $0.id == "2026-09-09" }
        XCTAssertNotNil(empty, "the day is still SHOWN")
        XCTAssertFalse(empty?.hasBriefing == true)
        XCTAssertNil(empty?.briefingId)
    }

    /// Briefings outside the window must not leak into it.
    func testAnOlderBriefingIsNotShown() {
        let days = CalendarStrip.days(archive: ["2026-08-01-0535"],
                                      today: day(13), calendar: cal, timeZone: .gmt)
        XCTAssertTrue(days.allSatisfy { !$0.hasBriefing })
    }

    /// The key must match what the server writes, not what a locale would
    /// render — `AppDelegate.briefingId` pins en_US_POSIX for the same reason.
    func testTheKeyIsAlwaysAnIsoDayRegardlessOfLocale() {
        var buddhist = Calendar(identifier: .buddhist)
        buddhist.timeZone = .gmt
        let key = CalendarStrip.key(for: day(13), calendar: cal)
        XCTAssertEqual(key, "2026-09-13")
        XCTAssertNotEqual(CalendarStrip.key(for: day(13), calendar: buddhist), key,
                          "a non-Gregorian calendar genuinely differs — which is "
                          + "why the strip is built with the server's calendar")
    }

    func testExactlyOneDayIsToday() {
        let days = CalendarStrip.days(archive: [], today: day(13),
                                      calendar: cal, timeZone: .gmt)
        XCTAssertEqual(days.filter(\.isToday).count, 1)
    }
}
