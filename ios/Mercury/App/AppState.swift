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

    /// The browse surface for whichever briefing is loaded.
    ///
    /// Fetched separately from the manifest and deliberately never awaited by
    /// the play path: ADR-013 makes the digest a SIBLING of the manifest so
    /// pressing play never waits on a screen the user may not open. A failure
    /// here costs the desk, not the briefing.
    var digest: Digest?

    /// Briefing ids still on the server, newest first. Empty until asked for.
    var archive: [String] = []
    /// Whether the last archive request failed, as distinct from returning
    /// nothing. The two look the same in `archive` and mean opposite things.
    var archiveFailed = false
    /// In flight. A third state, because "not yet asked" also looks like
    /// "nothing there".
    var archiveLoading = false
    let playback = PlaybackCoordinator()

    /// SwiftData persistence (PRD 3.7), handed over by `MercuryApp` from the
    /// shared `ModelContainer`.
    ///
    /// This whole layer — `BriefingStore`, `PersistenceModels`, and the
    /// `ResumePositionTests` that cover them — was written, tested and then
    /// never called by anything. The container was built and injected into
    /// the SwiftUI environment; nothing ever constructed a store from it. So
    /// `ResumePositionTests` passed, in full, against code no running app
    /// could reach: a green suite proving a feature the user did not have.
    ///
    /// Wired rather than deleted, for four reasons. PRD 3.7 requires
    /// persistence, so deleting it means deleting a spec requirement and
    /// rewriting it later. The user harm is concrete and daily: a
    /// six-minute briefing abandoned at minute four restarts at "Good
    /// morning" every time. The code is already complete and correct —
    /// upsert, retention, resume position — so the gap was one wire, not a
    /// subsystem. And offline transcript and show-notes access has nowhere
    /// else to come from.
    ///
    /// Optional because `AppState` is also built in tests and previews with
    /// no container; everything below degrades to "no persistence" rather
    /// than failing.
    @ObservationIgnored var store: BriefingStore?

    /// Below this, resuming is not worth it — and within this much of the
    /// end, a "resume" would drop the user into the sign-off.
    static let minimumResumeMs = 3_000

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

    /// True once the coordinator actually holds audio.
    ///
    /// A manifest is not enough, and that distinction is the whole bug behind
    /// three separate findings on this PR. `restoreLastBriefing` decodes a
    /// transcript from disk and sets `manifest` without loading anything into
    /// the engine; `PlaybackCoordinator.seek(toMs:)` with no file returns
    /// having scheduled nothing. So on that path the scrubber, the chapter
    /// ribbon and every Home row moved no audio and reported no error.
    ///
    /// Asked of the coordinator rather than inferred from `loadState`.
    /// `loadState` describes the last fetch and drifts from the engine in
    /// both directions: `prefetchBriefing` swaps the manifest without
    /// loading, and a failed `playPrefetched` sets `.failed` while the
    /// previous briefing is still loaded and playing.
    @MainActor
    var isPlaybackLoaded: Bool { playback.isLoaded }

    /// True when the loaded audio is the briefing currently on screen.
    ///
    /// The condition every offset-based control needs, and stricter than
    /// `isPlaybackLoaded` in exactly one window: `playBriefing` sets
    /// `manifest` and then awaits the audio download, so during that await
    /// the engine holds the PREVIOUS briefing while the new transcript,
    /// chapter ribbon and scrubber are all on screen describing a different
    /// one. Seeking then applies today's offset to yesterday's audio.
    @MainActor
    var isPlaybackCurrent: Bool {
        guard let id = manifest?.briefingId else { return false }
        return playback.loadedBriefingId == id
    }

    /// Play, pause, or fetch — whichever the current state calls for.
    ///
    /// Every control that starts playback goes through here. Tapping the Home
    /// card while a briefing was already playing called `playBriefing()`
    /// unconditionally, which re-fetched the manifest, re-downloaded the
    /// audio and restarted from zero — so the card drew a pause affordance
    /// and pressing it began the briefing again.
    @MainActor
    func togglePlayback() async {
        guard isPlaybackLoaded else {
            await playBriefing()
            return
        }
        // A rebuild we asked for may have landed since this audio loaded.
        //
        // Home tells the user the new briefing "appears the next time you
        // press play", and for anyone who had already pressed play that was
        // simply false: the loaded file was resumed forever and the rebuilt
        // audio was never fetched. `playBriefing` revalidates by ETag, so
        // this costs a conditional request and resumes from the saved
        // position when the rebuild has not landed yet.
        //
        // Only while STOPPED. Interrupting playback to swap the file under
        // a listener is the thing `prefetchBriefing` refuses to do, and
        // pressing pause is not a request for a different briefing.
        if isRegenerating, !playback.isPlaying {
            await playBriefing()
            return
        }
        playback.isPlaying ? playback.pause() : playback.play()
    }

    /// Jump to a point in the briefing and play from there, loading first if
    /// the engine is empty.
    ///
    /// This is what a chapter tab and a Home row do, and both tell VoiceOver
    /// they play that part of the briefing. A bare seek honoured neither
    /// half: on a restored manifest it did nothing at all, and on a paused
    /// one it moved the playhead and left the audio stopped.
    @MainActor
    func play(fromMs ms: Int) async {
        if !isPlaybackLoaded { await playBriefing() }
        guard isPlaybackLoaded else { return }
        playback.seek(toMs: ms)
        // `seek` past the real end of the audio calls `finish()`, and `play()`
        // reads `hasFinished` as a request to replay from zero — so a chapter
        // whose `startMs` sits beyond the stitched file would have restarted
        // the entire briefing. A seek that landed at the end is already where
        // it was asked to go.
        guard !playback.hasFinished, !playback.isPlaying else { return }
        playback.play()
    }

    /// Move the playhead without changing whether audio is running.
    ///
    /// The scrubber's contract, as distinct from `play(fromMs:)`: dragging
    /// while paused leaves you paused. Callers must check `isPlaybackLoaded`
    /// first — this deliberately does NOT fetch, because a drag is not a
    /// request to start downloading a briefing.
    @MainActor
    func seek(toMs ms: Int) {
        guard isPlaybackLoaded else { return }
        playback.seek(toMs: ms)
    }

    /// Loads the digest for the briefing on screen, quietly.
    ///
    /// Never throws and never blocks playback. Rule 7 at the level of a
    /// screen: a digest that will not load costs you the story cards, and
    /// the briefing still plays.
    @MainActor
    func loadDigest() async {
        guard let id = manifest?.briefingId else {
            digest = nil
            return
        }
        if digest?.briefingId == id { return }

        // Cleared BEFORE the request, not after it fails. Otherwise the
        // previous morning's cards stay on screen under the new briefing —
        // and their `turnIndex` values address a manifest that is no longer
        // loaded, so tapping one seeks today's audio to yesterday's offset.
        digest = nil

        do {
            let fetched = try await api.fetchDigest(briefingId: id)
            // Recheck after the await. Two loads can overlap — a restore
            // finishing while a tap starts today's — and without this the
            // slower one wins whichever briefing it belonged to.
            guard manifest?.briefingId == id else { return }
            digest = fetched
        } catch {
            // Not surfaced. There is no action the user could take, and the
            // play button is unaffected. Rule 7 at the level of a screen.
            print("[Mercury] digest for \(id) did not load: \(error)")
        }
    }

    /// The settings the generation job will read, or nil if never saved.
    ///
    /// Goes through `AppState` rather than handing the view the `APIClient`:
    /// `api` is private for a reason, and a screen reaching past it would be
    /// the second place that knows how Mercury talks to the server.
    @MainActor
    func fetchSavedSettings() async throws -> UserSettings? {
        try await api.fetchSettings()
    }

    /// Saves settings for the JOB, not for this device.
    ///
    /// `UserDefaults` cannot serve these: the cron runs at 04:00 on a machine
    /// that has never seen this phone. A preference written only here would
    /// be a control that appears to work — the thing ROADMAP task 11a exists
    /// to prevent.
    @MainActor
    func saveSettings(_ settings: UserSettings) async throws {
        try await api.saveSettings(settings)
    }

    /// A rebuild this app asked for, from dispatch until it is seen to land.
    ///
    /// The Worker answers as soon as GitHub accepts the dispatch, which is
    /// seconds; the generation behind it takes minutes. So "did the request
    /// succeed" and "is a rebuild running" are different questions, and an
    /// earlier cut answered the second with the first — `defer` cleared the
    /// flag the moment the POST returned, re-enabling a button that spends
    /// real money while the previous run was still going.
    struct PendingRebuild: Equatable {
        let requestedAt: Date
        /// The briefing this rebuild is FOR. A manifest for any other
        /// briefing cannot end it, however new it is.
        let briefingId: String
        /// The `generatedAt` of that briefing when this was asked for, when
        /// it was the one on screen. Anything newer IS the rebuild — the id
        /// cannot say so, because a forced run deliberately reuses it.
        ///
        /// Nil when the briefing on screen was some OTHER morning's. That
        /// case is not hypothetical: open a briefing from the archive, press
        /// rebuild, and an archive `generatedAt` from days ago would be
        /// "superseded" by today's existing, un-rebuilt manifest the moment
        /// anything fetched it.
        let supersedes: Date?
    }

    private(set) var rebuild: PendingRebuild?
    /// Clears `rebuild` when the window runs out.
    ///
    /// Not a comparison against the clock alone. `isRegenerating` reads
    /// `Date()`, and `@Observable` cannot see time pass — so once the window
    /// expired, nothing invalidated the view and the button stayed disabled
    /// until some unrelated state happened to change. A timer that mutates
    /// `rebuild` is a change SwiftUI can actually observe.
    private var rebuildExpiry: Task<Void, Never>?

    /// How long to believe in a rebuild nobody has confirmed.
    ///
    /// A real run took 423 seconds, and a job can fail on the server without
    /// any way to tell the phone. Without a ceiling, one failed generation
    /// would disable the button until the app was killed.
    static let rebuildWindow: TimeInterval = 20 * 60

    /// Whether a regeneration this app asked for is still plausibly running.
    var isRegenerating: Bool {
        guard let rebuild else { return false }
        return Date().timeIntervalSince(rebuild.requestedAt) < Self.rebuildWindow
    }

    /// The last thing the server said about the day's allowance, so the
    /// screen can say "two left" rather than letting someone find the limit
    /// by hitting it.
    private var lastRegeneration: RegenerationStatus?

    /// That status, but only while it is still about TODAY.
    ///
    /// An app left open overnight kept yesterday's `remaining == 0` and
    /// disabled a button whose allowance had reset hours earlier. The id is
    /// the day, so comparing it is the whole check.
    var regeneration: RegenerationStatus? {
        guard let status = lastRegeneration else { return nil }
        let today = AppDelegate.briefingId(for: .now)
        return status.briefingId == today ? status : nil
    }

    /// Ask the server to rebuild today's briefing from mail that has
    /// arrived since.
    ///
    /// Deliberately does NOT touch what is PLAYING. The new briefing takes
    /// several minutes and reuses today's id, so swapping it in mid-listen
    /// would replace the audio under the user — the same rule
    /// `prefetchBriefing` follows. It is picked up on the next play, which
    /// `togglePlayback` now makes true rather than merely promising.
    @MainActor
    func regenerateToday() async throws -> RegenerationStatus {
        // The server's day, before anything is compared against it. The
        // Worker keys the allowance by ITS date, and a device an hour the
        // other side of midnight would otherwise file a perfectly good
        // status under the wrong day and discard it.
        await api.syncServerClock()
        let today = AppDelegate.briefingId(for: .now)
        // Only when the briefing on screen IS today's. See `supersedes`.
        let previous = manifest?.briefingId == today ? manifest?.generatedAt : nil

        do {
            let status = try await api.requestRegeneration()
            lastRegeneration = status
            beginRebuild(of: today, supersedes: previous)
            return status
        } catch {
            // A refusal still carries the counts, and losing them is not
            // harmless: the button stayed enabled at the cap, so every
            // press sent another request the server had already refused.
            if let refused = Self.statusFromRefusal(error) {
                lastRegeneration = refused
            }
            // Nothing was dispatched, so nothing is pending. Clearing here
            // rather than in a `defer` is the difference between the two
            // questions above.
            endRebuild()
            throw error
        }
    }

    /// The allowance out of a 429 or 409 body.
    ///
    /// `APIError.badStatus` carries the body as text because that is what
    /// every other screen wants to show; this is the one caller that wants
    /// the numbers back out of it.
    static func statusFromRefusal(_ error: Error) -> RegenerationStatus? {
        guard case APIError.badStatus(let code, let body) = error,
              code == 429 || code == 409,
              let json = body.range(of: "{").map({ String(body[$0.lowerBound...]) })
        else { return nil }
        return try? JSONDecoder().decode(
            RegenerationStatus.self, from: Data(json.utf8))
    }

    @MainActor
    private func beginRebuild(of briefingId: String, supersedes: Date?) {
        rebuildExpiry?.cancel()
        rebuild = PendingRebuild(
            requestedAt: Date(), briefingId: briefingId, supersedes: supersedes)
        rebuildExpiry = Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.rebuildWindow))
            guard !Task.isCancelled else { return }
            await MainActor.run { self?.rebuild = nil }
        }
    }

    @MainActor
    private func endRebuild() {
        rebuildExpiry?.cancel()
        rebuildExpiry = nil
        rebuild = nil
    }

    /// Called wherever a freshly fetched manifest is adopted.
    ///
    /// A manifest generated after the rebuild was requested IS the rebuild,
    /// so the button comes back and the next play stops refetching.
    @MainActor
    private func noteRebuildLanded(_ manifest: BriefingManifest) {
        guard let rebuild else { return }
        // The rebuild was for ONE briefing. Adopting a different one — an
        // archive morning, say — says nothing about whether it finished.
        guard manifest.briefingId == rebuild.briefingId else { return }
        // With no baseline there is no server timestamp to beat, so the
        // request time stands in. That compares a server clock to a device
        // one, and it is the only comparison here that crosses clocks — but
        // it is reached only when today's briefing was not on screen, where
        // the alternative was treating the FIRST manifest to arrive as the
        // rebuild regardless of when it was made.
        let floor = rebuild.supersedes ?? rebuild.requestedAt
        if manifest.generatedAt <= floor { return }
        endRebuild()
    }

    /// The voice library, for the picker.
    @MainActor
    func fetchVoices() async throws -> VoiceList {
        try await api.fetchVoices()
    }

    /// The wake time as a `Date`, for a `DatePicker`.
    ///
    /// Stored as minutes since midnight because that is what the scheduler
    /// wants and what survives a date rolling over; the picker wants a Date,
    /// and this is the one place the two meet.
    @MainActor
    var wakeTimeDate: Date {
        get {
            var components = DateComponents()
            components.hour = wakeTimeMinutesFromMidnight / 60
            components.minute = wakeTimeMinutesFromMidnight % 60
            return Calendar.current.date(from: components) ?? .now
        }
        set {
            let parts = Calendar.current.dateComponents([.hour, .minute], from: newValue)
            wakeTimeMinutesFromMidnight = (parts.hour ?? 7) * 60 + (parts.minute ?? 0)
            // Both layers re-armed here, not at the next cold launch.
            //
            // `BGTaskScheduler` holds the date it was given, so without this
            // the device kept prefetching at the OLD time until the app
            // happened to be launched from cold.
            //
            // The local wake notification is a REPEATING calendar trigger,
            // deliberately, so it survives the app not being launched — which
            // also means it survives a wake-time change. If APNs registration
            // failed, that notification is the user's only alert, and it
            // would have gone on firing at the old time indefinitely.
            BackgroundScheduler.schedule(wakeTime: nextWakeDate)

            let minutes = wakeTimeMinutesFromMidnight
            Task { @MainActor in
                await PushService.shared.rescheduleLocalWakeNotificationIfArmed(
                    hour: minutes / 60, minute: minutes % 60
                )
            }
        }
    }

    /// The mornings still on the server, for the archive screen.
    ///
    /// `archiveFailed` exists because an empty list and a failed request are
    /// different facts that look identical: without it the archive says
    /// "Nothing earlier yet" when the server was simply unreachable, which
    /// is a confident lie about the user's own data.
    @MainActor
    func loadArchive() async {
        archiveFailed = false
        archiveLoading = true
        defer { archiveLoading = false }
        do {
            archive = try await api.fetchBriefingIndex()
        } catch {
            archiveFailed = true
            print("[Mercury] archive index did not load: \(error)")
        }
    }

    /// Loads an earlier briefing — manifest, audio and digest — and plays it.
    ///
    /// The audio is loaded rather than only the manifest, because every
    /// offset-based control is gated on the loaded briefing matching the one
    /// on screen (`isPlaybackCurrent`). Fetching the manifest alone would
    /// leave the whole player inert with no explanation.
    @MainActor
    func openBriefing(id: String) async {
        guard loadState != .loading else { return }
        loadState = .loading
        do {
            let fetched = try await api.fetchManifest(briefingId: id)
            await persist(fetched, audioPath: nil)
            let audioURL = try await api.downloadAudio(briefingId: id)
            await persist(fetched, audioPath: audioURL.path)
            try playback.load(manifest: fetched, audioURL: audioURL)
            // Assigned only once the audio is in the engine. Setting it
            // before the download meant a failure left the PREVIOUS
            // briefing playing while `manifest` named the requested one —
            // so `isPlaybackCurrent` went false, every control greyed out,
            // and the screen described a briefing that was not playing.
            self.manifest = fetched
            let manifest = fetched
            loadState = .ready
            // `startPlayback`, not a bare `play()`. It re-wires
            // `onPositionPersist` and `onFinished` to THIS briefing id —
            // without it, pausing or finishing the archive briefing saved
            // progress against whichever one was played before, and could
            // mark that earlier briefing complete.
            await startPlayback(of: manifest)
            await loadDigest()
        } catch {
            // `localizedDescription`, matching `playBriefing`. The other
            // spelling put "Error Domain=NSURLErrorDomain Code=-1004" on
            // the card where the user reads it.
            loadState = .failed(error.localizedDescription)
        }
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
            noteRebuildLanded(manifest)
            // Persist the transcript before touching the audio: it is worth
            // keeping even if the download fails.
            await persist(manifest, audioPath: nil)
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            await persist(manifest, audioPath: audioURL.path)
            try playback.load(manifest: manifest, audioURL: audioURL)
            await startPlayback(of: manifest)
            loadState = .ready
        } catch {
            // Offline, or the backend is unreachable. If last night's
            // prefetch already put today's briefing on this device, play
            // that rather than reporting a failure the user cannot act on
            // and that is not true of their briefing — it is on the phone.
            if await playStoredBriefing() {
                loadState = .ready
                return
            }
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
            let audioURL = try await api.downloadAudio(briefingId: id)
            await persist(manifest, audioPath: audioURL.path)
            // Never swap the manifest out from under audio that is STILL
            // GOING. The engine would keep playing last night's briefing
            // while the scrubber, the chapter ribbon and the transcript all
            // described a different one — the displayed duration and the
            // real audio silently belonging to different mornings. It is
            // persisted either way, so the next play picks it up.
            //
            // A finished briefing is a different case and must not be held
            // onto: `finish()` keeps the file so it can be replayed, so
            // without unloading it the new briefing could never be
            // published and the next tap replayed yesterday's.
            noteRebuildLanded(manifest)
            if playback.isLoaded && !playback.hasFinished { return true }
            playback.unload()
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
            noteRebuildLanded(manifest)
            await persist(manifest, audioPath: nil)
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            await persist(manifest, audioPath: audioURL.path)
            try playback.load(manifest: manifest, audioURL: audioURL)
            await startPlayback(of: manifest)
            loadState = .ready
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }

    // MARK: - Persistence (PRD 3.7)

    /// Where to start a briefing, given whatever position was saved.
    ///
    /// Pure so the policy can be checked exactly, with no container: a
    /// briefing already heard to the end starts over rather than dropping
    /// the user into the sign-off, and a few seconds in is not worth
    /// resuming to.
    static func resumeTarget(
        savedMs: Int?,
        durationMs: Int,
        minimumMs: Int = AppState.minimumResumeMs
    ) -> Int? {
        guard let savedMs, savedMs > minimumMs else { return nil }
        guard durationMs > 0 else { return savedMs }
        guard savedMs < durationMs - minimumMs else { return nil }
        return savedMs
    }

    /// Makes the last stored briefing available with no network at all.
    ///
    /// The store was write-only until now: transcripts, show notes and the
    /// cached audio path all went in and nothing ever read them back, so
    /// relaunching offline showed an error where a briefing already on disk
    /// was sitting unused. Called at launch, before any fetch.
    ///
    /// Only fills a gap — never replaces a manifest already loaded, since
    /// anything fetched this session is fresher than anything stored.
    @MainActor
    func restoreLastBriefing() async {
        guard manifest == nil, let store else { return }
        guard let stored = try? await store.latestStoredBriefing() else { return }
        manifest = stored.manifest
    }

    /// Plays today's briefing from disk when the network cannot produce it.
    ///
    /// Restricted to TODAY deliberately. Falling back to whatever is newest
    /// would answer "play my briefing" with yesterday's news and no
    /// indication of it; if last night's prefetch did not land, an error is
    /// the honest answer.
    ///
    /// Returns false when there is nothing playable, leaving the caller to
    /// report the original network failure.
    @MainActor
    private func playStoredBriefing() async -> Bool {
        guard let store,
              let stored = try? await store.storedBriefing(id: AppDelegate.briefingId(for: .now)),
              let path = stored.localAudioPath,
              FileManager.default.fileExists(atPath: path)
        else { return false }

        // Set BEFORE loading the audio: a transcript is worth showing even
        // if the cached file turns out to be unplayable.
        manifest = stored.manifest
        do {
            try playback.load(manifest: stored.manifest,
                              audioURL: URL(fileURLWithPath: path))
        } catch {
            print("[Mercury] Cached audio for \(stored.manifest.briefingId) "
                  + "could not be loaded: \(error)")
            return false
        }
        await startPlayback(of: stored.manifest)
        return true
    }

    private func persist(_ manifest: BriefingManifest, audioPath: String?) async {
        guard let store else { return }
        do {
            try await store.save(manifest, localAudioPath: audioPath)
        } catch {
            print("[Mercury] Could not persist briefing \(manifest.briefingId): \(error)")
        }
    }

    /// Restores the resume point, wires position persistence, and starts.
    @MainActor
    private func startPlayback(of manifest: BriefingManifest) async {
        wirePersistence(for: manifest.briefingId)

        if let store {
            let saved = try? await store.resumePositionMs(for: manifest.briefingId)
            if let target = Self.resumeTarget(
                savedMs: saved ?? nil, durationMs: manifest.durationMs
            ) {
                // Seeking while stopped schedules without starting, so the
                // `play()` below is what actually begins playback.
                playback.seek(toMs: target)
            }
        }
        playback.play()
    }

    private func wirePersistence(for briefingId: String) {
        playback.onPositionPersist = { [weak self] positionMs in
            guard let store = self?.store else { return }
            Task { try? await store.updateResumePosition(
                briefingId: briefingId, positionMs: positionMs
            ) }
        }
        playback.onFinished = { [weak self] in
            guard let store = self?.store else { return }
            // Completed AND rewound: next open starts from the top rather
            // than from the sign-off.
            Task {
                try? await store.markCompleted(briefingId: briefingId)
                try? await store.updateResumePosition(briefingId: briefingId, positionMs: 0)
            }
        }
    }
}
