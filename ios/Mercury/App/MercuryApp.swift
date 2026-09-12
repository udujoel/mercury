import SwiftUI
import SwiftData
import UIKit

/// One process, one `AppState`, one `ModelContainer`.
///
/// Both of those used to be reachable only through `RootView.task`, and a
/// view task requires a foreground scene. iOS launches this app with no
/// scene at all for a silent push or a `BGProcessingTask` — which is
/// precisely when the overnight prefetch runs (PRD 3.6, the PRIMARY and
/// SECONDARY layers). The delegate found `appState` nil, completed the
/// task as a failure, and the prefetch never ran; once a store was being
/// built in the same place, the background path could not have saved a
/// prefetched transcript either.
///
/// Building both at process scope is what makes the background entry points
/// and the UI the same instance rather than two that disagree. `static let`
/// so it happens exactly once, whichever door the process was opened
/// through.
enum AppBootstrap {

    static let container: ModelContainer = makeContainer()

    static let appState: AppState = {
        let state = AppState()
        state.store = BriefingStore(modelContainer: container)
        return state
    }()

    /// Falls back to an in-memory store rather than trapping.
    ///
    /// A `fatalError` here means a corrupt or unmigratable store file kills
    /// the app on launch with no way back — for a cache of the last seven
    /// briefings, which the backend can serve again. Degrading loses resume
    /// positions and offline transcripts for the session; trapping loses the
    /// app.
    private static func makeContainer() -> ModelContainer {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        do {
            return try ModelContainer(
                for: schema,
                configurations: [ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)]
            )
        } catch {
            print("[Mercury] On-disk SwiftData store unavailable (\(error)); "
                  + "continuing without persistence across launches.")
        }
        do {
            return try ModelContainer(
                for: schema,
                configurations: [ModelConfiguration(schema: schema, isStoredInMemoryOnly: true)]
            )
        } catch {
            fatalError("Could not create even an in-memory ModelContainer: \(error)")
        }
    }
}

@main
struct MercuryApp: App {

    // Both come from `AppBootstrap` so that the push and background-task
    // callbacks — which fire outside any SwiftUI view lifecycle, and often
    // with no scene at all — reach the same wired instance this scene does.
    @State private var appState = AppBootstrap.appState
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var sharedModelContainer: ModelContainer { AppBootstrap.container }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                // No .preferredColorScheme here. ADR-012 makes light the
                // primary design target and dark a first-class second, and
                // the whole semantic token layer resolves through
                // `traits.userInterfaceStyle` — which this line pinned to
                // .dark, making light unreachable in the running app.
                //
                // Nothing caught it: every contrast test calls
                // `resolved(dark:)` directly, so the maths was verified
                // while the resolution it depends on never ran. It took
                // launching the app and flipping the simulator's appearance.
                //
                // The app follows the system now.
                .task {
                    // Before anything is fetched: last night's briefing is
                    // already on disk, and showing it beats showing a
                    // spinner or an error while the network is asked.
                    await appState.restoreLastBriefing()
                    // FALLBACK layer safety net regardless of onboarding
                    // state: if neither the push nor the background task
                    // ran overnight, request notification authorization
                    // (harmless to ask again) so the wake notification/
                    // "Play" action is available once onboarding finishes.
                    if appState.hasCompletedOnboarding {
                        // Lets PushService arm the local wake notification at
                        // the right time if APNs registration fails — which is
                        // the normal case without a paid Apple Developer
                        // membership.
                        PushService.shared.wakeTimeMinutesFromMidnight = {
                            appState.wakeTimeMinutesFromMidnight
                        }
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

    /// The process-wide `AppState`, resolved on first use.
    ///
    /// This used to be assigned by `MercuryApp` from `RootView.task`, and
    /// stayed nil until a scene appeared. Every callback on this delegate
    /// fires in cases where one may never appear — a silent push, a
    /// `BGProcessingTask` — so the prefetch handler below hit `guard let
    /// appState = self?.appState else { setTaskCompleted(success: false) }`
    /// and gave up, on exactly the launches it exists to serve. Defaulting
    /// to `AppBootstrap.appState` means there is always one, already wired
    /// to the store, and it is the same instance the UI uses.
    ///
    /// Still a settable var so a test can substitute one.
    lazy var appState: AppState? = AppBootstrap.appState

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
            // This used to derive the id from the clock, on the reasoning
            // that `briefing_id` is a "YYYY-MM-DD" date string (PRD 3.7,
            // `sample_manifest.json`) and so needs no round trip. That was
            // true until PR #33 gave a day four briefings named for the
            // minute each ran; the derived id then matched nothing in R2 and
            // every nightly prefetch 404ed for five days without a sound.
            //
            // `AppState.prefetchTodaysBriefing` asks the server what it
            // actually has. See `briefingToPrefetch` for which one it picks.
            BackgroundScheduler.handlePrefetch(task, nextWakeTime: nextWake) {
                await appState.prefetchTodaysBriefing()
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
        briefingId(for: date, timeZone: BriefingClock.serverTimeZone)
    }

    /// Exposed for tests, and to make the timezone dependency explicit
    /// rather than an ambient `.current`.
    static func briefingId(for date: Date, timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}

/// Routes between onboarding and the main app based on `AppState`.
struct RootView: View {
    @Environment(AppState.self) private var appState

    /// The one place the app asks the system about Reduce Motion.
    ///
    /// `MercuryMotionContext` shipped with a `false` default and no caller
    /// anywhere, so `\.mercuryMotion` was a constant: the resolve/substitute
    /// logic was fully tested and could never run. Read here rather than in
    /// `MercuryApp` because this value comes from the trait environment, which
    /// an `App` is not part of.
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if appState.hasCompletedOnboarding {
                HomeView()
            } else {
                WelcomeView()
            }
        }
        .mercuryMotionEnvironment(reduceMotion: reduceMotion)
    }
}
