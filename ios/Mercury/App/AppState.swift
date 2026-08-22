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

    var hasCompletedOnboarding: Bool {
        get { UserDefaults.standard.bool(forKey: Self.onboardingKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.onboardingKey) }
    }

    /// The length chosen during onboarding. Sent with every generate request
    /// so the backend can apply the matching word budget (PRD F-18). Stored
    /// rather than held in the onboarding view, which is torn down the moment
    /// onboarding finishes.
    var briefingLength: BriefingLength {
        get {
            guard let raw = UserDefaults.standard.string(forKey: Self.lengthKey),
                  let value = BriefingLength(rawValue: raw)
            else { return .standard }
            return value
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: Self.lengthKey) }
    }

    var manifest: BriefingManifest?
    var loadState: LoadState = .idle
    let playback = PlaybackCoordinator()

    /// User's configured wake time, stored as minutes since midnight local
    /// time (set from `ScheduleSetupView`). Drives both the background
    /// prefetch window (`BackgroundScheduler`, PRD 3.6) and — eventually —
    /// the server-side cron schedule. Defaults to 7:00 AM, matching the
    /// default shown in the onboarding picker.
    var wakeTimeMinutesFromMidnight: Int {
        get {
            let stored = UserDefaults.standard.object(forKey: Self.wakeTimeKey) as? Int
            return stored ?? 7 * 60
        }
        set { UserDefaults.standard.set(newValue, forKey: Self.wakeTimeKey) }
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

    init(api: APIClientProtocol = APIClient()) {
        self.api = api
    }

    /// Tapping the greeting card is the primary action of the entire app:
    /// fetch (or generate) the briefing, download its audio, load it into
    /// the playback engine, and start playing — all before the player
    /// screen is shown.
    @MainActor
    func playBriefing() async {
        loadState = .loading
        do {
            let manifest = try await api.generateBriefing(length: briefingLength)
            self.manifest = manifest
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            try playback.load(manifest: manifest, audioURL: audioURL)
            playback.play()
            loadState = .ready
        } catch {
            loadState = .failed(error.localizedDescription)
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
