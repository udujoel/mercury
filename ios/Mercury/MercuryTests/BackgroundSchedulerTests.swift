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
