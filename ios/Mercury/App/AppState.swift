import Foundation
import Observation
import SwiftData

enum LoadState: Equatable {
    case idle
    case loading
    case ready
    case failed(String)

    static func == (lhs: LoadState, rhs: LoadState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.ready, .ready):
            return true
        case (.failed(let a), .failed(let b)):
            return a == b
        default:
            return false
        }
    }
}

/// Root app state: onboarding completion, the current briefing, and the
/// shared playback coordinator. Views observe this rather than talking to
/// services directly.
@Observable
final class AppState {

    /// STORED, not computed, and that distinction is the whole bug.
    ///
    /// `@Observable` instruments stored properties only. When this was a
    /// computed property reading and writing `UserDefaults` directly, setting
    /// it persisted the value and emitted no change event, so `RootView` —
    /// which switches on exactly this flag — never re-rendered. Tapping Done
    /// on the schedule screen did nothing visible until the app was
    /// relaunched. The value was right the whole time; the view had no idea.
    ///
    /// `UserDefaults` is still the storage layer: seeded in `init`, written
    /// through in `didSet`. Regression covered by
    /// `OnboardingStatePersistenceTests.testCompletingOnboardingPublishes...`,
    /// which drives it through `withObservationTracking` — the only check
    /// that can tell a stored property from a computed one, since both
    /// round-trip and both persist.
    var hasCompletedOnboarding: Bool {
        didSet { UserDefaults.standard.set(hasCompletedOnboarding, forKey: Self.onboardingKey) }
    }

    /// The length chosen during onboarding. Sent with every generate request
    /// so the backend can apply the matching word budget (PRD F-18). Stored
    /// rather than held in the onboarding view, which is torn down the moment
    /// onboarding finishes.
    var briefingLength: BriefingLength {
        didSet { UserDefaults.standard.set(briefingLength.rawValue, forKey: Self.lengthKey) }
    }

    var manifest: BriefingManifest?
    var loadState: LoadState = .idle
    let playback = PlaybackCoordinator()

    /// (not as a default property initializer — those run before `self` is
    /// available, and `playback` is an instance member); `init` also wires
    /// the real `briefingId`/`positionMs` closures via `configure(...)`
    /// `self` is safe to capture.

    /// User's configured wake time, stored as minutes since midnight local
    /// time (set from `ScheduleSetupView`). Drives both the background
    /// prefetch window (`BackgroundScheduler`, PRD 3.6) and — eventually —
    /// the server-side cron schedule. Defaults to 7:00 AM, matching the
    /// default shown in the onboarding picker.
    var wakeTimeMinutesFromMidnight: Int {
        didSet {
            UserDefaults.standard.set(wakeTimeMinutesFromMidnight, forKey: Self.wakeTimeKey)
        }
    }

    /// The next calendar date/time matching the configured wake time —
    /// today if it hasn't passed yet, tomorrow otherwise. Used as the
    /// `wakeTime` argument to `BackgroundScheduler.schedule`.
    var nextWakeDate: Date {
        // Match on hour/minute ONLY. Including year/month/day pinned the
        // match to today, so once today's wake time had passed nothing could
        // satisfy it: nextDate returned nil and the fallback `.now + 24h`
        // took over — which is 24 hours from whenever the app happened to be
        // opened, not tomorrow's wake time. Opening at 09:00 with a 07:00
        // wake produced a prefetch window at 08:30 the next day, drifting
        // another two hours every time it recurred.
        var components = DateComponents()
        components.hour = wakeTimeMinutesFromMidnight / 60
        components.minute = wakeTimeMinutesFromMidnight % 60
        components.second = 0
        return Calendar.current.nextDate(
            after: .now, matching: components, matchingPolicy: .nextTime
        ) ?? .now.addingTimeInterval(24 * 60 * 60)
    }

    private let api: APIClientProtocol
    private static let onboardingKey = "mercury.hasCompletedOnboarding"
    private static let lengthKey = "mercury.briefingLength"
    private static let wakeTimeKey = "mercury.wakeTimeMinutesFromMidnight"

    /// The default wake time shown in the onboarding picker, and the value
    /// used until the user chooses one.
    static let defaultWakeTimeMinutes = 7 * 60

    init(api: APIClientProtocol = APIClient()) {
        self.api = api

        // Seed the stored properties from UserDefaults. Assigning inside
        // `init` does not fire `didSet`, so this reads without writing back.
        let defaults = UserDefaults.standard
        self.hasCompletedOnboarding = defaults.bool(forKey: Self.onboardingKey)
        self.briefingLength = defaults.string(forKey: Self.lengthKey)
            .flatMap(BriefingLength.init(rawValue:)) ?? .standard
        self.wakeTimeMinutesFromMidnight =
            defaults.object(forKey: Self.wakeTimeKey) as? Int ?? Self.defaultWakeTimeMinutes
    }

    /// Tapping the greeting card is the primary action of the entire app.
    ///
    /// FETCHES FIRST, generates only as a fallback. Per ADR-001 the phone
    /// never generates: a scheduled job produces the briefing overnight and
    /// this just plays it. Going straight to generate meant the card failed
    /// outright against the deployed read-only Worker, which answers 405 to
    /// any POST — while the briefing the cron had already made sat in R2,
    /// reachable, and simply never asked for.
    ///
    /// The generate fallback is kept for running against a full local
    /// backend, where producing one on demand is genuinely useful.
    @MainActor
    func playBriefing() async {
        // One tap at a time.
        //
        // The greeting card is a big, satisfying target and the first thing
        // it does is go quiet for several seconds, so tapping it twice is
        // the natural thing to do. That started two fetches which collided
        // on the same destination file in `downloadAudio`, and then both
        // called `playback.load` on the same coordinator. `AppState` is
        // `@MainActor`, so this guard is genuinely exclusive: the second tap
        // cannot observe `loadState` before the first has set it.
        guard loadState != .loading else { return }
        loadState = .loading
        do {
            let manifest = try await fetchTodayOrGenerate()
            self.manifest = manifest
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            try playback.load(manifest: manifest, audioURL: audioURL)
            playback.play()
            loadState = .ready
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }

    /// Today's briefing if the scheduled job has produced one, otherwise ask
    /// the backend to make one. A 404 is the ordinary "not generated yet"
    /// answer, not an error worth surfacing.
    private func fetchTodayOrGenerate() async throws -> BriefingManifest {
        // Learn the server's timezone BEFORE deriving the id. BriefingClock
        // falls back to the device zone until it has heard from /health, so
        // on a first launch in a different zone the app would ask for the
        // wrong date, get a 404, and fall through to a generate that the
        // read-only Worker answers with 405. Cheap, and only actually
        // fetches once per launch.
        await api.syncServerClock()
        let today = AppDelegate.briefingId(for: .now)
        do {
            return try await api.fetchManifest(briefingId: today)
        } catch let error as APIError {
            guard case .badStatus(let code, _) = error, code == 404 else { throw error }
            print("[Mercury] No briefing for \(today) yet; requesting generation.")
        }

        do {
            return try await api.generateBriefing(length: briefingLength)
        } catch let error as APIError {
            // A read-only backend (the deployed Worker) answers 405 here.
            // That is not an error the user can act on by retrying — it means
            // the scheduled job has not produced today's briefing yet — so
            // say that rather than surfacing "Backend returned 405".
            if case .badStatus(405, _) = error {
                throw APIError.badStatus(
                    404,
                    "Today's briefing hasn't been generated yet. It's produced "
                    + "automatically each morning — try again shortly."
                )
            }
            throw error
        }
    }

    /// SECONDARY/PRIMARY-layer entry point (ADR-001, PRD 3.6): downloads a
    /// briefing the server already generated so it's ready locally, without
    /// starting playback. Called from `BackgroundScheduler`'s prefetch work
    /// closure and from `PushService.onPrefetch` when a silent push
    /// arrives. Deliberately never calls `generateBriefing` — the whole
    /// point of the background/push paths is that generation, which takes
    /// minutes, already happened server-side.
    ///
    /// Failure here is expected and not surfaced to `loadState`: both call
    /// sites run with the app backgrounded or not running at all, so there
    /// is no UI to show an error to. The FALLBACK foreground fetch in
    /// `playBriefing()` is what the user actually sees if this never runs
    /// or fails.
    @discardableResult
    @MainActor
    func prefetchBriefing(id: String) async -> Bool {
        do {
            let manifest = try await api.fetchManifest(briefingId: id)
            _ = try await api.downloadAudio(briefingId: id)
            self.manifest = manifest
            return true
        } catch {
            print("[Mercury] Background prefetch for briefing \(id) failed: \(error)")
            return false
        }
    }

    /// Starts playback directly in response to the user tapping the wake
    /// notification or its "Play" action (PRD F-17: "Tapping the
    /// notification starts playback without opening to the home screen
    /// first"). Reuses whatever `prefetchBriefing` already cached when
    /// possible, but falls back to fetching fresh — the prefetch is a
    /// best-effort optimization per ADR-001, never a precondition.
    @MainActor
    func playPrefetched(id: String?) async {
        // Same guard as `playBriefing`: the notification's "Play" action and
        // a tap on the card can both land, and the notification path is the
        // one most likely to arrive while a fetch is already running.
        guard loadState != .loading else { return }
        loadState = .loading
        do {
            let manifest: BriefingManifest
            if let cached = self.manifest, cached.briefingId == id {
                manifest = cached
            } else if let id {
                manifest = try await api.fetchManifest(briefingId: id)
            } else {
                // No briefing id at all — e.g. a malformed or stripped
                // notification payload. Nothing to play; fall back to the
                // ordinary tap-to-generate-or-fetch path so the user isn't
                // left staring at a spinner.
                //
                // Release the guard first. This method has already set
                // `loadState` to `.loading`, and `playBriefing` now refuses
                // to start while a load is in flight — so without this the
                // fallback would silently do nothing, producing exactly the
                // spinner it exists to prevent.
                loadState = .idle
                await playBriefing()
                return
            }
            self.manifest = manifest
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            try playback.load(manifest: manifest, audioURL: audioURL)
            playback.play()
            loadState = .ready
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }
}
