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
    @MainActor
    func requestAuthorization() async {
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

    func didFailToRegister(error: Error) {
        print("[Mercury] APNs registration failed: \(error)")
    }

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
