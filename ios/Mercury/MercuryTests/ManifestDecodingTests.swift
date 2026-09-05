import XCTest
@testable import Mercury

final class ManifestDecodingTests: XCTestCase {

    func testDecodesSampleManifestFixture() throws {
        let manifest = try Self.loadFixture()

        XCTAssertEqual(manifest.briefingId, "2026-08-21")
        XCTAssertEqual(manifest.durationMs, 26000)
        XCTAssertEqual(manifest.audioUrl, "/briefing/2026-08-21/audio")
        XCTAssertEqual(manifest.segments.count, 4)
        XCTAssertEqual(manifest.turns.count, 8)
        XCTAssertEqual(manifest.sources.count, 2)
    }

    func testDecodesSnakeCaseKeysViaExplicitCodingKeys() throws {
        let manifest = try Self.loadFixture()

        let firstSegment = try XCTUnwrap(manifest.segments.first)
        XCTAssertEqual(firstSegment.id, "cold_open")
        XCTAssertEqual(firstSegment.startMs, 0)

        let firstTurn = try XCTUnwrap(manifest.turns.first)
        XCTAssertEqual(firstTurn.speaker, "A")
        XCTAssertEqual(firstTurn.startMs, 0)
        XCTAssertEqual(firstTurn.endMs, 2000)

        let firstSource = try XCTUnwrap(manifest.sources.first)
        XCTAssertEqual(firstSource.turnIndex, 4)
        XCTAssertEqual(firstSource.sourceType, "calendar")
        XCTAssertNil(firstSource.url)

        let secondSource = manifest.sources[1]
        XCTAssertEqual(secondSource.url, "https://mail.google.com/mail/thread/abc123")
        XCTAssertEqual(secondSource.clusterSize, 1)
    }

    /// The audio format is NOT derivable from `audio_url`.
    ///
    /// The fixture used to carry a hand-written
    /// `https://cdn.example.com/briefings/2026-08-21/audio.wav`, which
    /// invited exactly the assumption the client must never make. A real
    /// manifest (`backend/out/2026-08-23.manifest.json`) carries
    /// `"/briefing/2026-08-23/audio"` — a relative path with no extension
    /// at all. Format comes from the response `Content-Type`, nowhere else.
    func testAudioURLCarriesNoFormatInformation() throws {
        let manifest = try Self.loadFixture()
        XCTAssertEqual(manifest.audioUrl, "/briefing/2026-08-21/audio")
        XCTAssertEqual(manifest.audioURL?.pathExtension, "",
                       "a real backend audio_url has no extension to read")
    }

    func testGeneratedAtDecodesAsISO8601Date() throws {
        let manifest = try Self.loadFixture()
        let calendar = Calendar(identifier: .gregorian)
        let components = calendar.dateComponents(in: TimeZone(identifier: "UTC")!, from: manifest.generatedAt)
        XCTAssertEqual(components.year, 2026)
        XCTAssertEqual(components.month, 8)
        XCTAssertEqual(components.day, 21)
    }

    // MARK: - Regression: fractional seconds

    /// The backend stamps `generated_at` with `datetime.now(UTC)` and
    /// pydantic serialises it at microsecond precision. Foundation's
    /// `.iso8601` strategy rejects fractional seconds outright, so every
    /// real manifest failed to decode — while this suite stayed green,
    /// because the fixture had been hand-written as `"...T07:00:00Z"`.
    ///
    /// This is the exact string out of `backend/out/2026-08-23.manifest.json`.
    func testDecodesTheMicrosecondTimestampTheBackendActuallyEmits() throws {
        let json = Data("""
        {"briefing_id":"2026-08-23","generated_at":"2026-08-23T10:49:22.073032Z",
         "duration_ms":322036,"audio_url":"/briefing/2026-08-23/audio",
         "segments":[],"turns":[],"sources":[]}
        """.utf8)

        let manifest = try JSONDecoder.mercury().decode(BriefingManifest.self, from: json)

        var components = DateComponents()
        components.year = 2026
        components.month = 8
        components.day = 23
        components.hour = 10
        components.minute = 49
        components.second = 22
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let wholeSecond = try XCTUnwrap(calendar.date(from: components))

        XCTAssertEqual(manifest.generatedAt.timeIntervalSince1970,
                       wholeSecond.timeIntervalSince1970 + 0.073032,
                       accuracy: 0.001,
                       "the fractional part must survive, not just parse")
    }

    /// The other half: a timestamp landing exactly on a whole second
    /// serialises without a fractional part, so the parser must accept that
    /// form too. A fix that only handled microseconds would have the same
    /// bug on a one-in-a-million morning.
    func testDecodesAWholeSecondTimestampToo() throws {
        let json = Data("""
        {"briefing_id":"2026-08-23","generated_at":"2026-08-23T10:49:22Z",
         "duration_ms":1,"audio_url":"/briefing/2026-08-23/audio",
         "segments":[],"turns":[],"sources":[]}
        """.utf8)

        let manifest = try JSONDecoder.mercury().decode(BriefingManifest.self, from: json)

        var components = DateComponents()
        components.year = 2026
        components.month = 8
        components.day = 23
        components.hour = 10
        components.minute = 49
        components.second = 22
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        XCTAssertEqual(manifest.generatedAt, try XCTUnwrap(calendar.date(from: components)))
    }

    /// Pins the PARSER, not the decoder.
    ///
    /// The decoder-level tests above can only fail on an OS whose built-in
    /// `.iso8601` strategy is strict — which no simulator on this machine
    /// is (iOS 26.5 / 27.0 only). This one fails on any OS if
    /// `ISO8601Timestamp` is ever narrowed back to a single
    /// `.withInternetDateTime` formatter, which is the actual regression
    /// to guard against.
    /// cubic reported that on iOS 17 the fractional-seconds formatter still
    /// rejects the backend's six-digit fraction outright. That cannot be
    /// measured here — only iOS 26.5 and 27.0 runtimes are installed, both
    /// on the swift-foundation rewrite — so rather than bet either way,
    /// `ISO8601Timestamp` now hands the formatter exactly the three digits
    /// it is specified for. These cover every fraction width the wire could
    /// carry, and they are runtime-independent because the normalisation is
    /// pure string work.
    func testFractionalSecondsAreNormalisedToThreeDigits() {
        // What pydantic's `model_dump_json` actually emits.
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.073032Z"),
            "2026-08-23T10:49:22.073Z"
        )
        // Fewer than three digits is padded, not left short: a formatter
        // specified for exactly three has no obligation to accept one.
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.07Z"),
            "2026-08-23T10:49:22.070Z"
        )
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.7Z"),
            "2026-08-23T10:49:22.700Z"
        )
        // Nanoseconds, in case a future backend stops using pydantic.
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.073032123Z"),
            "2026-08-23T10:49:22.073Z"
        )
        // The timezone designator is not a fraction and must survive intact,
        // in either sign — the naive "find the next + or -" scan gets this
        // wrong for a negative offset.
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.073032+01:00"),
            "2026-08-23T10:49:22.073+01:00"
        )
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22.073032-05:00"),
            "2026-08-23T10:49:22.073-05:00"
        )
        // No fractional part: left alone for the whole-seconds formatter.
        XCTAssertEqual(
            ISO8601Timestamp.normalisingFraction("2026-08-23T10:49:22Z"),
            "2026-08-23T10:49:22Z"
        )
    }

    /// Every shape above must also actually parse, not merely normalise.
    func testEveryFractionWidthParsesToTheSameSecond() throws {
        let base = try XCTUnwrap(ISO8601Timestamp.date(from: "2026-08-23T10:49:22Z"))
        for text in ["2026-08-23T10:49:22.073032Z",
                     "2026-08-23T10:49:22.073Z",
                     "2026-08-23T10:49:22.073032123Z"] {
            let parsed = try XCTUnwrap(ISO8601Timestamp.date(from: text),
                                       "\(text) must parse")
            XCTAssertEqual(parsed.timeIntervalSince1970 - base.timeIntervalSince1970,
                           0.073, accuracy: 0.000_5, "\(text)")
        }
        // An offset timestamp names the same instant as its Z equivalent.
        let offset = try XCTUnwrap(
            ISO8601Timestamp.date(from: "2026-08-23T11:49:22.073032+01:00")
        )
        XCTAssertEqual(offset.timeIntervalSince1970 - base.timeIntervalSince1970,
                       0.073, accuracy: 0.000_5)
    }

    func testTimestampParserAcceptsBothWireShapes() throws {
        let fractional = try XCTUnwrap(
            ISO8601Timestamp.date(from: "2026-08-23T10:49:22.073032Z"),
            "the shape pydantic actually emits"
        )
        let whole = try XCTUnwrap(
            ISO8601Timestamp.date(from: "2026-08-23T10:49:22Z"),
            "the shape a whole-second timestamp emits"
        )
        // MILLISECOND precision, deliberately pinned.
        // `ISO8601DateFormatter` with `.withFractionalSeconds` parses three
        // fractional digits and drops the rest, so pydantic's `.073032`
        // arrives as `.073`. That is ample for a field used to date and
        // sort one briefing per day, but it is a real difference from the
        // built-in strategy on iOS 18+, which keeps all six digits — so it
        // is asserted rather than left to be discovered later.
        XCTAssertEqual(fractional.timeIntervalSince1970 - whole.timeIntervalSince1970,
                       0.073, accuracy: 0.000_5,
                       "the sub-second part must survive to millisecond precision")

        // A strict `[.withInternetDateTime]` formatter is exactly what the
        // old strategy used on iOS 17, and exactly what must not come back.
        let strict = ISO8601DateFormatter()
        strict.formatOptions = [.withInternetDateTime]
        XCTAssertNil(strict.date(from: "2026-08-23T10:49:22.073032Z"),
                     "documents why a fractional-seconds formatter is needed")
    }

    /// An unparseable timestamp must throw, not silently become some
    /// default date — a briefing dated "now" would sort and cache wrong
    /// everywhere downstream.
    func testAGarbageTimestampFailsLoudly() {
        let json = Data("""
        {"briefing_id":"x","generated_at":"not a date","duration_ms":1,
         "audio_url":"/x","segments":[],"turns":[],"sources":[]}
        """.utf8)

        XCTAssertThrowsError(try JSONDecoder.mercury().decode(BriefingManifest.self, from: json))
    }

    // MARK: - Fixture loading

    /// Decodes through `JSONDecoder.mercury()` — the SAME configuration
    /// `APIClient` uses. Building a local `JSONDecoder` here, as this did
    /// before, meant the decoding suite was testing a copy of the
    /// production configuration rather than the configuration itself: a
    /// wrong `dateDecodingStrategy` in `APIClient` could not have been
    /// caught here at any fixture fidelity.
    static func loadFixture() throws -> BriefingManifest {
        let url = try XCTUnwrap(
            Bundle(for: ManifestDecodingTests.self)
                .url(forResource: "sample_manifest", withExtension: "json")
        )
        return try JSONDecoder.mercury().decode(BriefingManifest.self, from: Data(contentsOf: url))
    }
}

// MARK: - Regression: the card must fetch before generating

/// Records which endpoints the app actually reaches for.
final class RecordingAPI: APIClientProtocol {
    var calls: [String] = []
    var manifestResult: Result<BriefingManifest, Error>

    /// Holds `fetchManifest` open so a second tap can arrive while the
    /// first is still in flight.
    var shouldGateFetch = false
    private var gateWaiters: [CheckedContinuation<Void, Never>] = []

    init(manifestResult: Result<BriefingManifest, Error>) {
        self.manifestResult = manifestResult
    }

    /// Lets every held `fetchManifest` proceed.
    func openGate() {
        shouldGateFetch = false
        let waiters = gateWaiters
        gateWaiters = []
        waiters.forEach { $0.resume() }
    }

    func fetchManifest(briefingId: String) async throws -> BriefingManifest {
        calls.append("fetchManifest")
        if shouldGateFetch {
            await withCheckedContinuation { continuation in
                gateWaiters.append(continuation)
            }
        }
        return try manifestResult.get()
    }

    /// Nil, which is the ORDINARY answer for a briefing generated before
    /// ADR-013 — the caller shows an empty desk rather than an error.
    var digestResult: Digest?
    var archiveResult: [String] = []

    func fetchDigest(briefingId: String) async throws -> Digest? {
        calls.append("fetchDigest")
        return digestResult
    }

    func fetchBriefingIndex() async throws -> [String] {
        calls.append("fetchBriefingIndex")
        return archiveResult
    }

    /// Nil is the never-saved state every deployment starts in.
    var settingsResult: UserSettings?
    var savedSettings: [UserSettings] = []

    func fetchSettings() async throws -> UserSettings? {
        calls.append("fetchSettings")
        return settingsResult
    }

    func saveSettings(_ settings: UserSettings) async throws {
        calls.append("saveSettings")
        savedSettings.append(settings)
    }

    var voicesResult = VoiceList.empty

    func fetchVoices() async throws -> VoiceList {
        calls.append("fetchVoices")
        return voicesResult
    }

    var regenerationResult = RegenerationStatus(
        briefingId: "2026-09-05", used: 1, limit: 3)
    /// A dispatch the Worker refused — no token, GitHub down, over the cap.
    var regenerationError: Error?

    func requestRegeneration() async throws -> RegenerationStatus {
        calls.append("requestRegeneration")
        if let regenerationError { throw regenerationError }
        return regenerationResult
    }

    func generateBriefing(length: BriefingLength) async throws -> BriefingManifest {
        calls.append("generateBriefing")
        return ManifestFixtures.minimal
    }

    func generationStatus(briefingId: String) async throws -> GenerationStatus {
        calls.append("generationStatus")
        return GenerationStatus(
            briefingId: briefingId, status: "ready", elapsedS: nil, error: nil
        )
    }

    func downloadAudio(briefingId: String) async throws -> URL {
        calls.append("downloadAudio")
        throw APIError.badStatus(404, "no audio in this test")
    }

    func registerDevice(token: String) async throws { calls.append("registerDevice") }
    func syncServerClock() async { calls.append("syncServerClock") }
}

final class PlayBriefingRoutingTests: XCTestCase {

    /// The deployed backend is a read-only Worker that answers 405 to any
    /// POST. Going straight to generate meant tapping the greeting card
    /// failed outright, while the briefing the cron had already produced sat
    /// in R2 and was simply never requested.
    func testCardFetchesTodayBeforeGenerating() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertEqual(api.calls.prefix(2).map { $0 },
                       ["syncServerClock", "fetchManifest"],
                       "learn the server zone, then ask for today's briefing")
        XCTAssertFalse(api.calls.contains("generateBriefing"),
                       "it must not POST when a briefing already exists")
    }

    /// A non-404 must propagate WITHOUT generating.
    ///
    /// The test below covers the 404 branch; this covers the other half its
    /// name promises. Without it, a 500 from the backend could quietly
    /// trigger a generate — which is the original generate-first bug wearing
    /// a different hat, and against the read-only Worker would turn one
    /// confusing error into another.
    func testNon404FailurePropagatesWithoutGenerating() async {
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(500, "server exploded"))
        )
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertEqual(api.calls, ["syncServerClock", "fetchManifest"],
                       "a 500 must not be treated as 'not generated yet'")
        if case .failed(let message) = state.loadState {
            XCTAssertTrue(message.contains("500"), "the real error must surface")
        } else {
            XCTFail("expected a failed load state, got \(state.loadState)")
        }
    }

    // MARK: - Regression: one tap at a time

    /// Tapping the greeting card twice used to start two concurrent
    /// fetches. They collided on the same destination file in
    /// `downloadAudio` and both then called `playback.load` on the same
    /// coordinator.
    ///
    /// Deterministic form: a load is already in flight, so a fresh tap must
    /// reach the network not at all.
    func testATapWhileAlreadyLoadingIsIgnored() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)
        state.loadState = .loading

        await state.playBriefing()

        XCTAssertTrue(api.calls.isEmpty,
                      "a second tap must not start a second fetch, got \(api.calls)")
    }

    /// And the real shape of it: two taps genuinely overlapping, with the
    /// first held open inside `fetchManifest`.
    func testTwoOverlappingTapsProduceOneFetch() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        api.shouldGateFetch = true
        let state = AppState(api: api)

        async let first: Void = state.playBriefing()
        // Let the first tap reach the gate before the second one lands.
        await Task.yield()
        await Task.yield()
        async let second: Void = state.playBriefing()
        await Task.yield()
        api.openGate()
        _ = await (first, second)

        XCTAssertEqual(api.calls.filter { $0 == "fetchManifest" }.count, 1,
                       "the second tap must be swallowed, got \(api.calls)")
    }

    /// `playPrefetched(id: nil)` delegates to `playBriefing` — and it does
    /// so having already set `loadState` to `.loading`. If it does not
    /// release the guard first, the fallback silently does nothing and the
    /// user is left on the spinner that fallback exists to prevent.
    func testPlayPrefetchedWithNoIdStillFallsThroughToTheOrdinaryPath() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)

        await state.playPrefetched(id: nil)

        XCTAssertTrue(api.calls.contains("fetchManifest"),
                      "the guard must not swallow its own fallback, got \(api.calls)")
    }

    /// A 404 is the ordinary "not generated yet" answer, and only then is
    /// asking the backend to make one appropriate.
    func testCardFallsBackToGenerateOnlyOn404() async {
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(404, "not found"))
        )
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertTrue(api.calls.contains("fetchManifest"))
        XCTAssertTrue(api.calls.contains("generateBriefing"),
                      "a missing briefing should fall back to generating one")
    }
}

enum ManifestFixtures {
    static var minimal: BriefingManifest { made(at: "2026-08-23T05:00:00Z") }

    /// The same briefing, generated at a different moment.
    ///
    /// A forced rebuild reuses the id on purpose, so `generated_at` is the
    /// ONLY field that distinguishes a rebuilt briefing from the one it
    /// replaced — which makes it the field the pending-rebuild state has to
    /// read, and the one a test has to be able to vary.
    static func made(at generatedAt: String,
                     id: String = "2026-08-23") -> BriefingManifest {
        let json = """
        {"briefing_id":"\(id)","generated_at":"\(generatedAt)",
         "duration_ms":1000,"audio_url":"/briefing/\(id)/audio",
         "segments":[],"turns":[],"sources":[]}
        """
        return try! JSONDecoder.mercury().decode(BriefingManifest.self, from: Data(json.utf8))
    }

    /// TODAY's id, in the server's zone — the same call `regenerateToday`
    /// makes. A rebuild is always for today, so any test about one has to
    /// speak in today's id or it is testing the id guard by accident.
    static var todayId: String { AppDelegate.briefingId(for: .now) }
}

/// Rebuilding today's briefing, as a piece of STATE rather than a request.
///
/// The Worker answers the moment GitHub accepts the dispatch; the run
/// behind it takes about seven minutes. Everything here is about that gap.
final class PendingRebuildTests: XCTestCase {

    @MainActor
    func testTheButtonStaysDisabledWhileTheRebuildRuns() async throws {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)

        _ = try await state.regenerateToday()

        // It used to clear this in a `defer`, so the button came back a
        // few hundred milliseconds after a press that costs a full
        // generation — long before the generation it started had finished.
        XCTAssertTrue(state.isRegenerating,
                      "a dispatch acknowledged is not a rebuild finished")
    }

    @MainActor
    func testAFailedRequestLeavesNothingPending() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        api.regenerationError = APIError.badStatus(502, "no dispatch")
        let state = AppState(api: api)

        _ = try? await state.regenerateToday()

        XCTAssertFalse(state.isRegenerating,
                       "nothing was dispatched, so nothing is running")
    }

    /// The landing signal, which both the button and the next play read.
    @MainActor
    func testOnlyANewerBriefingEndsTheRebuild() async throws {
        let today = ManifestFixtures.todayId
        let first = ManifestFixtures.made(at: "2026-08-23T05:00:00Z", id: today)
        let api = RecordingAPI(manifestResult: .success(first))
        let state = AppState(api: api)

        // Adopt the pre-rebuild briefing, then ask for a rebuild.
        await state.playBriefing()
        _ = try await state.regenerateToday()
        XCTAssertTrue(state.isRegenerating)

        // The run has not finished: the server still serves the same
        // briefing. Fetching it must not be mistaken for the rebuild.
        await state.playBriefing()
        XCTAssertTrue(state.isRegenerating,
                      "the same briefing coming back is not a new one")

        api.manifestResult = .success(
            ManifestFixtures.made(at: "2026-08-23T09:30:00Z", id: today))
        await state.playBriefing()

        XCTAssertFalse(state.isRegenerating,
                       "a briefing generated after the request IS the rebuild")
    }

    /// Nothing played yet, so there is no briefing timestamp to beat.
    ///
    /// The floor falls back to the request time. Without that fallback a
    /// nil `supersedes` cleared on the FIRST manifest to arrive — including
    /// the pre-rebuild one the server is still serving — which re-enabled
    /// the button and stopped the next play refetching, both early.
    @MainActor
    func testAnOlderBriefingDoesNotEndARebuildAskedForCold() async throws {
        // TODAY's briefing, so the id guard cannot be what makes this pass —
        // the point is the requestedAt floor, and a test that passes for the
        // wrong reason proves nothing about it.
        let stale = ManifestFixtures.made(
            at: "2026-08-23T05:00:00Z", id: ManifestFixtures.todayId)
        let api = RecordingAPI(manifestResult: .success(stale))
        let state = AppState(api: api)

        _ = try await state.regenerateToday()   // nothing on screen
        await state.playBriefing()              // and it predates the request

        XCTAssertTrue(state.isRegenerating,
                      "a briefing made before the request cannot be the rebuild")
    }

    /// A briefing from the ARCHIVE must not act as the baseline.
    ///
    /// `regenerateToday` used whatever manifest was on screen. Open a
    /// briefing from three days ago, press rebuild, and its `generatedAt`
    /// is older than today's existing — un-rebuilt — manifest, so the very
    /// next fetch of today looked like the rebuild landing. The button came
    /// back and the next play stopped revalidating, both while the paid job
    /// was still running.
    @MainActor
    func testAnArchiveBriefingIsNotTheBaseline() async throws {
        let old = ManifestFixtures.made(at: "2026-08-23T05:00:00Z")
        let api = RecordingAPI(manifestResult: .success(old))
        let state = AppState(api: api)

        await state.playBriefing()          // an archive morning is on screen
        _ = try await state.regenerateToday()

        // TODAY's pre-rebuild manifest: newer than the archive one on
        // screen, and generated before the request — so neither the id
        // guard nor the floor may mistake it for the rebuild.
        api.manifestResult = .success(
            ManifestFixtures.made(at: "2026-08-23T06:00:00Z",
                                  id: ManifestFixtures.todayId))
        await state.playBriefing()

        XCTAssertTrue(state.isRegenerating,
                      "a newer briefing is not automatically the rebuild")
    }

    /// A refusal carries the counts, and throwing them away kept the button
    /// live at the cap — so every press sent another request the server had
    /// already refused.
    @MainActor
    func testTheCapRefusalStillUpdatesTheAllowance() async {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let today = AppDelegate.briefingId(for: .now)
        api.regenerationError = APIError.badStatus(
            429,
            #"{"detail":"Already regenerated 3 times today.","used":3,"limit":3,"#
            + #""briefing_id":"\#(today)"}"#)
        let state = AppState(api: api)

        _ = try? await state.regenerateToday()

        XCTAssertEqual(state.regeneration?.remaining, 0,
                       "the button must learn there is nothing left")
        XCTAssertFalse(state.isRegenerating, "nothing was dispatched")
    }

    /// A refused SECOND request must not cancel the first rebuild.
    ///
    /// The button is disabled while one is pending, so this cannot happen
    /// through the UI today — but a 429 clearing state it never created
    /// would re-enable the control and stop the next play refetching while
    /// the first job was still running.
    @MainActor
    func testARefusalDoesNotCancelARebuildAlreadyRunning() async throws {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        let state = AppState(api: api)

        _ = try await state.regenerateToday()
        XCTAssertTrue(state.isRegenerating)

        api.regenerationError = APIError.badStatus(
            429, #"{"detail":"cap","used":3,"limit":3}"#)
        _ = try? await state.regenerateToday()

        XCTAssertTrue(state.isRegenerating,
                      "the first rebuild is still running")
        XCTAssertEqual(state.regeneration?.remaining, nil,
                       "the 429 body carries no briefing_id, so it is not today's")
    }

    /// Left open overnight, the app kept yesterday's `remaining == 0` and
    /// disabled a button whose allowance had reset hours before.
    @MainActor
    func testYesterdaysAllowanceIsNotTodays() async throws {
        let api = RecordingAPI(manifestResult: .success(ManifestFixtures.minimal))
        api.regenerationResult = RegenerationStatus(
            briefingId: "1999-01-01", used: 3, limit: 3)
        let state = AppState(api: api)

        _ = try await state.regenerateToday()

        XCTAssertNil(state.regeneration,
                     "a status about another day says nothing about this one")

        api.regenerationResult = RegenerationStatus(
            briefingId: AppDelegate.briefingId(for: .now), used: 1, limit: 3)
        _ = try await state.regenerateToday()

        XCTAssertEqual(state.regeneration?.remaining, 2)
    }
}
