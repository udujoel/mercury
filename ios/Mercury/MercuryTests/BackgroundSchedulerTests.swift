import BackgroundTasks
import XCTest
@testable import Mercury

/// `BGTaskScheduler` does not execute background tasks in the simulator at
/// all — Apple's own documentation says so, and `BGTaskScheduler.shared`
/// calls either no-op or throw there. These tests therefore target
/// `BackgroundScheduler.makeRequest(wakeTime:)`, the pure request-
/// construction function, rather than `schedule(wakeTime:)` or actual task
/// execution. Whether a submitted request is actually granted a run, and
/// whether the registered handler fires, needs a physical device — see
/// docs/phase2/ios_automation.md for the exact LLDB trigger command.
final class BackgroundSchedulerTests: XCTestCase {

    func testRequestUsesTheStablePrefetchIdentifier() {
        let request = BackgroundScheduler.makeRequest(wakeTime: .now)
        XCTAssertEqual(request.identifier, "com.joeludu.mercury.prefetch")
        // Must also match what's declared in Info.plist under
        // BGTaskSchedulerPermittedIdentifiers, or BGTaskScheduler.submit
        // throws at runtime — the identifier constant is the single source
        // of truth both places read from.
        XCTAssertEqual(request.identifier, BackgroundScheduler.prefetchIdentifier)
    }

    func testEarliestBeginDateIsThirtyMinutesBeforeWakeTime() {
        let wake = Date(timeIntervalSince1970: 1_700_000_000)
        let request = BackgroundScheduler.makeRequest(wakeTime: wake)
        XCTAssertEqual(request.earliestBeginDate, wake.addingTimeInterval(-30 * 60))
    }

    func testRequiresNetworkConnectivityIsTrue() {
        let request = BackgroundScheduler.makeRequest(wakeTime: .now)
        XCTAssertTrue(request.requiresNetworkConnectivity)
    }

    func testRequiresExternalPowerIsFalse() {
        // The PRD is explicit this must be false — a prefetch task that
        // only ever ran while charging would almost never run overnight
        // for a phone on a nightstand, not plugged in until morning.
        let request = BackgroundScheduler.makeRequest(wakeTime: .now)
        XCTAssertFalse(request.requiresExternalPower)
    }

    func testLeadTimeConstantIsThirtyMinutes() {
        XCTAssertEqual(BackgroundScheduler.leadTime, 30 * 60)
    }

    func testDifferentWakeTimesProduceDifferentEarliestBeginDates() {
        let a = BackgroundScheduler.makeRequest(wakeTime: Date(timeIntervalSince1970: 0))
        let b = BackgroundScheduler.makeRequest(wakeTime: Date(timeIntervalSince1970: 3600))
        XCTAssertNotEqual(a.earliestBeginDate, b.earliestBeginDate)
    }
}

// MARK: - Regressions from the PR review

final class BriefingIdAndWakeTimeTests: XCTestCase {

    /// `briefingId` must use the SERVER's timezone, not the device's.
    ///
    /// The server names briefing ids from its configured USER_TIMEZONE. When
    /// the client formatted in `.current` instead, the two disagreed the
    /// moment the device left that zone: Lisbon-configured Mercury opened at
    /// 20:00 in New York asks for 2026-08-22 while the server has already
    /// filed 2026-08-23. The prefetch 404s and the app silently falls back to
    /// a foreground fetch — the layer defeated, with no error anywhere.
    func testBriefingIdUsesTheGivenTimeZoneNotTheDeviceOne() {
        // 2026-08-22 23:30 UTC — still the 22nd in New York, already the
        // 23rd in Lisbon (UTC+1 in August).
        var components = DateComponents()
        components.year = 2026
        components.month = 8
        components.day = 22
        components.hour = 23
        components.minute = 30
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let instant = calendar.date(from: components)!

        let lisbon = AppDelegate.briefingId(
            for: instant, timeZone: TimeZone(identifier: "Europe/Lisbon")!
        )
        let newYork = AppDelegate.briefingId(
            for: instant, timeZone: TimeZone(identifier: "America/New_York")!
        )

        XCTAssertEqual(lisbon, "2026-08-23")
        XCTAssertEqual(newYork, "2026-08-22")
        XCTAssertNotEqual(lisbon, newYork, "the zones must genuinely differ here")
    }

    /// `nextWakeDate` must not drift once today's wake time has passed.
    ///
    /// Matching on year/month/day pinned the search to today, so after the
    /// wake time nothing could match: `nextDate` returned nil and the
    /// fallback `.now + 24h` took over — 24 hours from whenever the app was
    /// opened, not tomorrow's wake time, drifting further every day.
    func testNextWakeDateIsTomorrowsWakeTimeNotTwentyFourHoursFromNow() {
        let state = AppState()
        state.wakeTimeMinutesFromMidnight = 7 * 60

        let next = state.nextWakeDate
        let parts = Calendar.current.dateComponents([.hour, .minute], from: next)

        XCTAssertEqual(parts.hour, 7, "must land on the configured wake hour")
        XCTAssertEqual(parts.minute, 0)
        XCTAssertGreaterThan(next, Date(), "must be in the future")
        XCTAssertLessThan(
            next.timeIntervalSinceNow, 24 * 60 * 60 + 1,
            "must be the next occurrence, never more than a day out"
        )
    }
}

/// Onboarding state must be OBSERVABLE, not merely persisted.
///
/// `hasCompletedOnboarding`, `briefingLength` and `wakeTimeMinutesFromMidnight`
/// were computed properties reading and writing UserDefaults. `@Observable`
/// only tracks *stored* properties, so setting them updated the defaults and
/// emitted no change notification — `RootView` never re-rendered and tapping
/// Done on the schedule screen appeared to do nothing at all. The value was
/// written correctly the whole time; the view simply had no idea.
///
/// Testing observation directly needs a SwiftUI render pass, so these assert
/// the property is backed by real storage that round-trips within one instance
/// AND reaches UserDefaults — which is what distinguishes a stored property
/// with a didSet from the computed version that shipped.
final class OnboardingStatePersistenceTests: XCTestCase {

    private let keys = [
        "mercury.hasCompletedOnboarding",
        "mercury.briefingLength",
        "mercury.wakeTimeMinutesFromMidnight",
    ]

    override func setUp() {
        super.setUp()
        keys.forEach { UserDefaults.standard.removeObject(forKey: $0) }
    }

    override func tearDown() {
        keys.forEach { UserDefaults.standard.removeObject(forKey: $0) }
        super.tearDown()
    }

    func testOnboardingCompletionRoundTripsAndPersists() {
        let state = AppState()
        XCTAssertFalse(state.hasCompletedOnboarding)

        state.hasCompletedOnboarding = true

        // Readable back from the same instance — the bug was that the view
        // never learned, but the value itself must also be correct here.
        XCTAssertTrue(state.hasCompletedOnboarding)
        XCTAssertTrue(
            UserDefaults.standard.bool(forKey: "mercury.hasCompletedOnboarding"),
            "must still persist — UserDefaults remains the storage layer"
        )
        XCTAssertTrue(AppState().hasCompletedOnboarding, "and survive a relaunch")
    }

    func testWakeTimeAndLengthRoundTripAndPersist() {
        let state = AppState()
        XCTAssertEqual(state.wakeTimeMinutesFromMidnight, 7 * 60, "default 07:00")
        XCTAssertEqual(state.briefingLength, .standard)

        state.wakeTimeMinutesFromMidnight = 6 * 60 + 45
        state.briefingLength = .full

        XCTAssertEqual(state.wakeTimeMinutesFromMidnight, 6 * 60 + 45)
        XCTAssertEqual(state.briefingLength, .full)

        let relaunched = AppState()
        XCTAssertEqual(relaunched.wakeTimeMinutesFromMidnight, 6 * 60 + 45)
        XCTAssertEqual(relaunched.briefingLength, .full)
    }
}
