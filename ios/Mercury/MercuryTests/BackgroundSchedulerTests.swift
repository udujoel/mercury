import BackgroundTasks
import Observation
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

    /// This test USED to read:
    ///
    ///     let wake = Date(timeIntervalSince1970: 1_700_000_000)   // Nov 2023
    ///     XCTAssertEqual(request.earliestBeginDate, wake - 30 * 60)
    ///
    /// — a wake time permanently in the past, asserting that the window
    /// opens in the past too. It did not merely fail to catch the
    /// reschedule bug below; it pinned the behaviour that IS the bug. Now
    /// `now` is passed explicitly, and the wake time is ahead of it.
    func testEarliestBeginDateIsThirtyMinutesBeforeAFutureWakeTime() {
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let wake = now.addingTimeInterval(6 * 60 * 60)
        let request = BackgroundScheduler.makeRequest(wakeTime: wake, now: now)
        XCTAssertEqual(request.earliestBeginDate, wake.addingTimeInterval(-30 * 60))
    }

    /// THE RESCHEDULE BUG.
    ///
    /// `handlePrefetch` reschedules from inside the fired task's own
    /// handler, passing `AppState.nextWakeDate` — the next occurrence of the
    /// configured hour. Fire the task at 06:35 for an 07:00 wake and "next"
    /// is today's 07:00, so the resubmitted window opened at 06:30: already
    /// past, therefore immediately eligible again. The one prefetch before
    /// the alarm became a request to run repeatedly, on exactly the budget
    /// iOS grants least willingly.
    func testReschedulingFromInsideThePrefetchWindowLandsInTheFuture() {
        // 06:35, with the wake at 07:00 the same morning.
        let wake = Date(timeIntervalSince1970: 1_700_000_000)
        let now = wake.addingTimeInterval(-25 * 60)

        let request = BackgroundScheduler.makeRequest(wakeTime: wake, now: now)
        let begin = request.earliestBeginDate

        XCTAssertNotNil(begin)
        XCTAssertGreaterThan(
            begin!, now,
            "a window in the past makes the task immediately eligible again"
        )
        // Tomorrow's window, not some arbitrary offset from `now`.
        XCTAssertEqual(begin!.timeIntervalSince(wake.addingTimeInterval(-30 * 60)),
                       24 * 60 * 60, accuracy: 60 * 60,
                       "it must roll forward a day, not drift by the lead time")
    }

    /// The boundary: firing exactly at the moment the window opens must
    /// still produce a future window, not the same instant back again.
    func testReschedulingExactlyAtTheWindowOpeningRollsForward() {
        let wake = Date(timeIntervalSince1970: 1_700_000_000)
        let now = wake.addingTimeInterval(-30 * 60)

        let begin = BackgroundScheduler.makeRequest(wakeTime: wake, now: now).earliestBeginDate
        XCTAssertNotNil(begin)
        XCTAssertGreaterThan(begin!, now)
    }

    /// A task that fires late — after the wake time has passed — gets the
    /// same treatment.
    func testReschedulingAfterTheWakeTimeLandsInTheFuture() {
        let wake = Date(timeIntervalSince1970: 1_700_000_000)
        let now = wake.addingTimeInterval(90 * 60)

        let begin = BackgroundScheduler.makeRequest(wakeTime: wake, now: now).earliestBeginDate
        XCTAssertNotNil(begin)
        XCTAssertGreaterThan(begin!, now)
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
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let a = BackgroundScheduler.makeRequest(
            wakeTime: now.addingTimeInterval(3 * 60 * 60), now: now
        )
        let b = BackgroundScheduler.makeRequest(
            wakeTime: now.addingTimeInterval(4 * 60 * 60), now: now
        )
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

    // MARK: - Observation

    /// The regression the round-trip tests above could never catch.
    ///
    /// A computed property over `UserDefaults` round-trips perfectly and
    /// persists perfectly — which is all the two tests above assert — while
    /// emitting no observation event at all, because `@Observable` only
    /// instruments *stored* properties. That is exactly the shape the bug
    /// came back in: every persistence assertion stayed green and Done in
    /// onboarding still did nothing until the app was relaunched.
    ///
    /// `withObservationTracking` is the only thing that tells the two apart:
    /// it reads the property inside a tracked scope and fails if mutating it
    /// does not call back.
    func testCompletingOnboardingPublishesAnObservationChange() {
        let state = AppState()
        let didChange = ChangeFlag()

        withObservationTracking {
            _ = state.hasCompletedOnboarding
        } onChange: {
            didChange.set()
        }

        state.hasCompletedOnboarding = true

        XCTAssertTrue(
            didChange.isSet,
            "RootView switches on hasCompletedOnboarding; without an "
            + "observation event it never re-renders and Done appears dead"
        )
    }

    func testWakeTimeChangePublishesAnObservationChange() {
        let state = AppState()
        let didChange = ChangeFlag()

        withObservationTracking {
            _ = state.wakeTimeMinutesFromMidnight
        } onChange: {
            didChange.set()
        }

        state.wakeTimeMinutesFromMidnight = 6 * 60 + 15

        XCTAssertTrue(didChange.isSet, "the schedule picker binds to this")
    }

    func testBriefingLengthChangePublishesAnObservationChange() {
        let state = AppState()
        let didChange = ChangeFlag()

        withObservationTracking {
            _ = state.briefingLength
        } onChange: {
            didChange.set()
        }

        state.briefingLength = .express

        XCTAssertTrue(didChange.isSet, "the length picker binds to this")
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

/// A flag the `@Sendable` `onChange` of `withObservationTracking` can set.
///
/// The tests captured a `var` and mutated it from that closure, which is a
/// data race the compiler warns is an error in the Swift 6 language mode
/// (ROADMAP task 54).
final class ChangeFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    func set() { lock.withLock { value = true } }
    var isSet: Bool { lock.withLock { value } }
}
