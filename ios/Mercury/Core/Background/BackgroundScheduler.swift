import BackgroundTasks
import Foundation

/// The SECONDARY layer of the three-layer delivery strategy (ADR-001,
/// PRD 3.6): an opportunistic `BGProcessingTask` that prefetches a briefing
/// the server cron has *already generated*.
///
/// Treat background execution as unreliable by design. iOS grants this
/// window based on usage patterns, battery, thermals and network — with no
/// guarantee, and on many days it will not run at all. That is expected,
/// not a bug: the PRIMARY layer (server cron + APNs silent push) and the
/// FALLBACK layer (foreground fetch on launch, see `AppState.playBriefing`)
/// are what actually guarantee the user gets a briefing. This task is a
/// bonus that shaves latency off the morning open when the system happens
/// to cooperate.
///
/// Never generate here. Generation is a two-to-four-minute LLM + TTS
/// pipeline run against a background budget of roughly thirty seconds. The
/// PRD is explicit: this task downloads, it never generates.
enum BackgroundScheduler {

    /// Must match `BGTaskSchedulerPermittedIdentifiers` in Info.plist and
    /// never change — identifiers must be stable forever (PRD 3.6).
    static let prefetchIdentifier = "com.joeludu.mercury.prefetch"

    /// How far before the user's configured wake time the earliest-begin
    /// window opens.
    static let leadTime: TimeInterval = 30 * 60

    /// Registers the task handler with the system. Must be called before
    /// the app finishes launching — Apple requires every permitted
    /// identifier to be registered before
    /// `application(_:didFinishLaunchingWithOptions:)` returns, so this is
    /// called from `AppDelegate.application(_:didFinishLaunchingWithOptions:)`
    /// (see `PushService.swift` / `MercuryApp.swift`), not from a view or
    /// from `onAppear`.
    ///
    /// The handler is injected as a closure rather than hardcoded here so
    /// this type stays free of networking/persistence dependencies — those
    /// live on `AppState` — and so `makeRequest(wakeTime:)` below can be
    /// unit tested with no `BGTaskScheduler` involved at all.
    static func register(handler: @escaping (BGProcessingTask) -> Void) {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: prefetchIdentifier,
            using: nil
        ) { task in
            guard let processingTask = task as? BGProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            handler(processingTask)
        }
    }

    /// Builds (but does not submit) the request for a prefetch window ending
    /// at `wakeTime`. Pulled out as a pure function, independent of
    /// `BGTaskScheduler.shared`, specifically so it can be unit tested —
    /// `BGTaskScheduler` itself does not execute background tasks in the
    /// simulator at all, so tests target this construction logic instead of
    /// end-to-end scheduling. See `docs/phase2/ios_automation.md` for what
    /// still needs a physical device.
    static func makeRequest(wakeTime: Date) -> BGProcessingTaskRequest {
        let request = BGProcessingTaskRequest(identifier: prefetchIdentifier)
        request.earliestBeginDate = wakeTime.addingTimeInterval(-leadTime)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        return request
    }

    /// Submits a prefetch request for the given wake time.
    ///
    /// `submit` throws if the app has too many pending requests already, or
    /// if the user has disabled Background App Refresh — neither is fatal
    /// here: the PRIMARY and FALLBACK layers still cover the user, so this
    /// logs and swallows rather than propagating.
    static func schedule(wakeTime: Date) {
        let request = makeRequest(wakeTime: wakeTime)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("[Mercury] Could not schedule background prefetch: \(error)")
        }
    }

    /// Installs the per-invocation behaviour for a fired task.
    ///
    /// Reschedules FIRST, before `work` runs — a task that does not
    /// reschedule itself inside its own handler runs exactly once, ever
    /// (PRD 3.6). `work` should be a bounded, cancellable prefetch (fetch
    /// manifest + download audio for whatever the server has ready — never
    /// a generate call); its `Bool` return communicates success to
    /// `task.setTaskCompleted`.
    ///
    /// The expiration handler cancels the in-flight work rather than doing
    /// nothing: iOS kills the process outright if the task overruns its
    /// budget, and an unhandled cancellation there is worse than a clean
    /// one here.
    static func handlePrefetch(
        _ task: BGProcessingTask,
        nextWakeTime: Date,
        work: @escaping () async -> Bool
    ) {
        schedule(wakeTime: nextWakeTime)

        // Guards against calling `setTaskCompleted` twice — once from the
        // expiration handler, once from the (racing) work task — which iOS
        // treats as a programmer error. `NSLock` rather than an actor: this
        // needs to be callable synchronously from the non-async expiration
        // handler.
        let completionLock = NSLock()
        var hasCompleted = false
        func completeOnce(success: Bool) {
            completionLock.lock()
            let alreadyDone = hasCompleted
            hasCompleted = true
            completionLock.unlock()
            guard !alreadyDone else { return }
            task.setTaskCompleted(success: success)
        }

        let workTask = Task {
            let success = await work()
            completeOnce(success: success)
        }

        task.expirationHandler = { [workTask] in
            workTask.cancel()
            completeOnce(success: false)
        }
    }
}
