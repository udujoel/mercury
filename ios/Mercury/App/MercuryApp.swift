import SwiftUI
import SwiftData
import UIKit

@main
struct MercuryApp: App {

    // `AppState` is created once here and handed to the delegate so push
    // and background-task callbacks (which fire outside any SwiftUI view
    // lifecycle) can still reach it.
    @State private var appState = AppState()
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var sharedModelContainer: ModelContainer = {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            return try ModelContainer(for: schema, configurations: [configuration])
        } catch {
            fatalError("Could not create SwiftData ModelContainer: \(error)")
        }
    }()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .preferredColorScheme(.dark)
                .task {
                    appDelegate.appState = appState
                    // FALLBACK layer safety net regardless of onboarding
                    // state: if neither the push nor the background task
                    // ran overnight, request notification authorization
                    // (harmless to ask again) so the wake notification/
                    // "Play" action is available once onboarding finishes.
                    if appState.hasCompletedOnboarding {
                        await PushService.shared.requestAuthorization()
                        BackgroundScheduler.schedule(wakeTime: appState.nextWakeDate)
                    }
                }
        }
        .modelContainer(sharedModelContainer)
    }
}

/// Bridges UIKit app-lifecycle callbacks (APNs registration, background
/// task registration, silent push delivery) into `AppState`/`PushService`.
/// SwiftUI's `App` protocol has no delegate methods of its own for any of
/// these, so a thin `UIApplicationDelegate` is unavoidable here — kept as
/// small as possible, with all real logic living in `PushService` and
/// `BackgroundScheduler`.
final class AppDelegate: NSObject, UIApplicationDelegate {

    /// Set by `MercuryApp` right after launch. Optional because the
    /// delegate is constructed before `MercuryApp`'s `@State` exists;
    /// callbacks that fire before it's set (vanishingly unlikely in
    /// practice — registration happens synchronously in
    /// `didFinishLaunchingWithOptions`) simply no-op.
    var appState: AppState?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        // Both registrations must happen before this method returns, per
        // Apple's requirement that permitted background task identifiers
        // and notification delegates be set up before launch completes.
        PushService.shared.configureCategories()
        PushService.shared.onPrefetch = { [weak self] briefingId in
            await self?.appState?.prefetchBriefing(id: briefingId)
        }
        PushService.shared.onPlayRequested = { [weak self] briefingId in
            await self?.appState?.playPrefetched(id: briefingId)
        }

        BackgroundScheduler.register { [weak self] task in
            guard let appState = self?.appState else {
                task.setTaskCompleted(success: false)
                return
            }
            let nextWake = appState.nextWakeDate
            // The task fires shortly before *today's* wake time, so the
            // briefing it should prefetch is today's — `briefing_id` is a
            // "YYYY-MM-DD" date string (PRD 3.7, `sample_manifest.json`),
            // deterministic from the clock alone. No server round trip is
            // needed to learn which id to ask for.
            let expectedId = AppDelegate.briefingId(for: .now)
            BackgroundScheduler.handlePrefetch(task, nextWakeTime: nextWake) {
                await appState.prefetchBriefing(id: expectedId)
            }
        }

        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        PushService.shared.didRegister(deviceToken: deviceToken)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        PushService.shared.didFailToRegister(error: error)
    }

    /// Silent (`content-available`) push handler — the PRIMARY layer's
    /// actual delivery mechanism (ADR-001, PRD 3.6).
    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        Task {
            let result = await PushService.shared.handleSilentPush(userInfo: userInfo)
            completionHandler(result)
        }
    }

    /// A briefing's id is its generation date as "YYYY-MM-DD" (PRD 3.7 —
    /// `@Attribute(.unique) var id: String // "2026-08-21"`, and
    /// `MercuryTests/Fixtures/sample_manifest.json`'s `"briefing_id":
    /// "2026-08-21"`). That makes it derivable from the clock with no
    /// server round trip, which is exactly what the background task needs
    /// since it has no channel to be told the id some other way.
    static func briefingId(for date: Date) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}

/// Routes between onboarding and the main app based on `AppState`.
struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        if appState.hasCompletedOnboarding {
            HomeView()
        } else {
            WelcomeView()
        }
    }
}
