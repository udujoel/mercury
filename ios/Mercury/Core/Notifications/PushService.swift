import Foundation
import UIKit
import UserNotifications

/// The PRIMARY layer of the three-layer delivery strategy (ADR-001, PRD
/// 3.6): the server cron generates a briefing, then sends a silent
/// (`content-available`) push as a wake hint, backed by a user-visible
/// notification with a "Play" action (PRD F-17).
///
/// Read `backend/app/notifications/apns.py` before assuming a missing push
/// is a client bug: APNs accepting a push with 200 does not mean the device
/// receives it — iOS throttles and drops silent pushes based on battery,
/// Low Power Mode, usage patterns and undocumented signals. This class
/// exists to act correctly on whatever pushes DO arrive; it is not what
/// guarantees delivery. That guarantee is the foreground fetch on launch
/// (`AppState.playBriefing`), which runs regardless of whether any push or
/// background task ever fired.
final class PushService: NSObject {

    static let shared = PushService()

    /// Identifier for the action button on the wake notification (PRD
    /// F-17: "Tapping the notification starts playback without opening to
    /// the home screen first").
    static let playActionIdentifier = "com.joeludu.mercury.play"
    static let localWakeIdentifier = "mercury.localWake"
    static let briefingReadyCategory = "com.joeludu.mercury.briefingReady"

    /// Custom payload key the server cron sends alongside the standard
    /// `aps` dictionary. Backend push construction lives in
    /// `backend/app/notifications/apns.py::send_silent`, which accepts an
    /// arbitrary `payload` dict merged into `aps` — this client reads
    /// whichever of those carries the briefing id. If a push arrives
    /// without one, that's treated as "nothing to do" rather than a crash:
    /// see `extractBriefingId`.
    static let briefingIdPayloadKey = "briefing_id"

    /// Called with a briefing id when a silent push (or a background task,
    /// see `BackgroundScheduler`) determines new content should be
    /// prefetched but NOT necessarily played immediately. Wired to
    /// `AppState.prefetchBriefing` in `MercuryApp.swift`.
    var onPrefetch: ((String) async -> Void)?

    /// Called when the user taps the wake notification itself or its
    /// "Play" action — playback should start directly, no intermediate
    /// screen. Wired to `AppState.playPrefetched`.
    var onPlayRequested: ((String?) async -> Void)?

    private let api: APIClientProtocol

    init(api: APIClientProtocol = APIClient()) {
        self.api = api
        super.init()
    }

    // MARK: - Setup

    /// Registers the notification category (with its "Play" action) and
    /// installs `self` as the `UNUserNotificationCenter` delegate. Call
    /// once, early — from `AppDelegate.application(_:didFinishLaunchingWithOptions:)`.
    func configureCategories() {
        UNUserNotificationCenter.current().delegate = self

        let playAction = UNNotificationAction(
            identifier: Self.playActionIdentifier,
            title: "Play",
            options: [.foreground]
        )
        let category = UNNotificationCategory(
            identifier: Self.briefingReadyCategory,
            actions: [playAction],
            intentIdentifiers: [],
            options: []
        )
        UNUserNotificationCenter.current().setNotificationCategories([category])
    }

    /// Requests notification authorization (alert/sound/badge, for the
    /// user-visible wake notification) and registers for remote
    /// notifications (for the silent APNs push). Both are needed — the
    /// silent push doesn't require alert authorization to be delivered to
    /// `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`,
    /// but the visible "briefing is ready" notification does.
    /// Guards against re-running on every foreground transition. The device
    /// log showed the "APNs registration failed / local wake notification
    /// scheduled" pair six times in one session, each occurrence removing and
    /// re-adding the pending notification — a small window with none armed at
    /// all, and a log too noisy to read.
    private var hasRequestedAuthorization = false

    @MainActor
    func requestAuthorization() async {
        guard !hasRequestedAuthorization else { return }
        hasRequestedAuthorization = true

        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            if !granted {
                print("[Mercury] Notification authorization was denied; the wake "
                    + "notification (PRD F-17) will not be shown, but silent "
                    + "prefetch pushes are unaffected.")
            }
        } catch {
            print("[Mercury] Notification authorization request failed: \(error)")
        }
        UIApplication.shared.registerForRemoteNotifications()
    }

    // MARK: - Local wake notification (no paid account required)

    /// Schedules a repeating local notification at the wake time, carrying the
    /// same "Play" action as the APNs wake notification (PRD F-17).
    ///
    /// This exists because **APNs requires the Push Notifications capability,
    /// which requires a paid Apple Developer Program membership.** On a free
    /// personal team `registerForRemoteNotifications()` fails, so the PRIMARY
    /// delivery layer in ADR-001 is unavailable.
    ///
    /// A local notification needs no entitlement at all. It cannot be
    /// server-initiated — it fires on the device's own clock whether or not
    /// the briefing is actually ready — so it is a weaker signal than a silent
    /// push. But paired with the BGProcessingTask prefetch (SECONDARY) and the
    /// foreground fetch on launch (FALLBACK), the user still gets "the
    /// briefing is there when I wake up" without paying Apple 99 USD a year.
    ///
    /// Deliberately a repeating calendar trigger rather than one scheduled per
    /// day: the app cannot rely on being launched daily to re-arm it, which is
    /// the whole point of the wake notification.
    @MainActor
    func scheduleLocalWakeNotification(hour: Int, minute: Int) async {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(
            withIdentifiers: [Self.localWakeIdentifier]
        )

        let content = UNMutableNotificationContent()
        content.title = "Your briefing is ready"
        content.body = "Two hosts, everything that matters this morning."
        content.sound = .default
        content.categoryIdentifier = Self.briefingReadyCategory

        var components = DateComponents()
        components.hour = hour
        components.minute = minute

        let request = UNNotificationRequest(
            identifier: Self.localWakeIdentifier,
            content: content,
            trigger: UNCalendarNotificationTrigger(
                dateMatching: components, repeats: true
            )
        )

        do {
            try await center.add(request)
            print("[Mercury] Local wake notification scheduled for "
                + String(format: "%02d:%02d", hour, minute))
        } catch {
            print("[Mercury] Could not schedule local wake notification: \(error)")
        }
    }

    /// Cancels the local wake notification — used when APNs registration
    /// succeeds, so a paid-account build does not show two notifications.
    @MainActor
    func cancelLocalWakeNotification() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(
            withIdentifiers: [Self.localWakeIdentifier]
        )
    }

    // MARK: - Device token

    /// Hands the APNs device token to the backend. Called from
    /// `AppDelegate.application(_:didRegisterForRemoteNotificationsWithDeviceToken:)`.
    func didRegister(deviceToken: Data) {
        let hex = Self.hexString(deviceToken)
        Task {
            do {
                try await api.registerDevice(token: hex)
            } catch {
                // Not fatal: the FALLBACK (foreground fetch on launch)
                // still gets the user their briefing without a working
                // push path. Retried implicitly next launch since this
                // whole flow re-runs on every cold start.
                print("[Mercury] Failed to register device token with backend: \(error)")
            }
        }
    }

    /// Called when APNs registration fails — which is the NORMAL case on a
    /// free personal team, where the Push Notifications capability is not
    /// available. Falls back to the local wake notification so the user still
    /// gets a morning prompt without a paid Apple Developer membership.
    func didFailToRegister(error: Error) {
        print("[Mercury] APNs registration failed (expected on a free personal "
            + "team — the Push Notifications capability needs a paid Apple "
            + "Developer membership): \(error)")
        // The PRIMARY delivery layer is unavailable, so arm the local
        // notification instead. ADR-001's three layers were designed so any
        // one of them failing still gets the user their briefing; this is
        // that design being used, not a workaround bolted on.
        Task { @MainActor in
            let minutes = self.wakeTimeMinutesFromMidnight?() ?? 7 * 60
            await self.scheduleLocalWakeNotification(
                hour: minutes / 60, minute: minutes % 60
            )
        }
    }

    /// Supplies the configured wake time. Injected rather than read directly
    /// so `PushService` does not depend on `AppState`, which owns it.
    var wakeTimeMinutesFromMidnight: (() -> Int)?

    private static func hexString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: - Silent push handling

    /// Handles a silent (`content-available: 1`) push delivered to
    /// `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`.
    /// Downloads the manifest and audio so playback can start instantly the
    /// next time the user opens the app — but does NOT start playback
    /// itself; a silent push has no user-visible affordance to play from.
    func handleSilentPush(userInfo: [AnyHashable: Any]) async -> UIBackgroundFetchResult {
        guard let briefingId = Self.extractBriefingId(userInfo) else {
            // No briefing id in the payload — either a push shaped for
            // something else, or a malformed one. Either way there is
            // nothing actionable here; graceful degradation (CLAUDE.md
            // rule 7), not a crash.
            return .noData
        }
        await onPrefetch?(briefingId)
        return .newData
    }

    static func extractBriefingId(_ userInfo: [AnyHashable: Any]) -> String? {
        userInfo[briefingIdPayloadKey] as? String
    }
}

// MARK: - UNUserNotificationCenterDelegate

extension PushService: UNUserNotificationCenterDelegate {

    /// Fires when the user taps the notification itself (default action)
    /// or its "Play" action button.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        let briefingId = Self.extractBriefingId(userInfo)

        switch response.actionIdentifier {
        case Self.playActionIdentifier, UNNotificationDefaultActionIdentifier:
            await onPlayRequested?(briefingId)
        default:
            break
        }
    }

    /// Show the wake notification even while the app is foregrounded —
    /// otherwise a briefing that finishes generating while the user has
    /// the app open never surfaces.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }
}
