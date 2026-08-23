import XCTest
@testable import Mercury

final class ManifestDecodingTests: XCTestCase {

    func testDecodesSampleManifestFixture() throws {
        let manifest = try Self.loadFixture()

        XCTAssertEqual(manifest.briefingId, "2026-08-21")
        XCTAssertEqual(manifest.durationMs, 26000)
        XCTAssertEqual(manifest.audioUrl, "https://cdn.example.com/briefings/2026-08-21/audio.wav")
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

    func testAudioURLDoesNotHardcodeExtension() throws {
        // The fixture emits .wav (the no-ffmpeg fallback path). Decoding
        // must not assume .m4a anywhere.
        let manifest = try Self.loadFixture()
        XCTAssertTrue(manifest.audioUrl.hasSuffix(".wav"))
        XCTAssertEqual(manifest.audioURL?.pathExtension, "wav")
    }

    func testGeneratedAtDecodesAsISO8601Date() throws {
        let manifest = try Self.loadFixture()
        let calendar = Calendar(identifier: .gregorian)
        let components = calendar.dateComponents(in: TimeZone(identifier: "UTC")!, from: manifest.generatedAt)
        XCTAssertEqual(components.year, 2026)
        XCTAssertEqual(components.month, 8)
        XCTAssertEqual(components.day, 21)
    }

    // MARK: - Fixture loading

    static func loadFixture() throws -> BriefingManifest {
        let url = try XCTUnwrap(
            Bundle(for: ManifestDecodingTests.self)
                .url(forResource: "sample_manifest", withExtension: "json")
        )
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(BriefingManifest.self, from: data)
    }
}

// MARK: - Regression: the card must fetch before generating

/// Records which endpoints the app actually reaches for.
final class RecordingAPI: APIClientProtocol {
    var calls: [String] = []
    var manifestResult: Result<BriefingManifest, Error>

    init(manifestResult: Result<BriefingManifest, Error>) {
        self.manifestResult = manifestResult
    }

    func fetchManifest(briefingId: String) async throws -> BriefingManifest {
        calls.append("fetchManifest")
        return try manifestResult.get()
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

        XCTAssertEqual(api.calls.first, "fetchManifest",
                       "the card must ask for today's briefing first")
        XCTAssertFalse(api.calls.contains("generateBriefing"),
                       "it must not POST when a briefing already exists")
    }

    /// A 404 is the ordinary "not generated yet" answer, and only then is
    /// asking the backend to make one appropriate.
    func testCardFallsBackToGenerateOnlyOn404() async {
        let api = RecordingAPI(
            manifestResult: .failure(APIError.badStatus(404, "not found"))
        )
        let state = AppState(api: api)

        await state.playBriefing()

        XCTAssertEqual(api.calls.first, "fetchManifest")
        XCTAssertTrue(api.calls.contains("generateBriefing"),
                      "a missing briefing should fall back to generating one")
    }
}

enum ManifestFixtures {
    static var minimal: BriefingManifest {
        let json = """
        {"briefing_id":"2026-08-23","generated_at":"2026-08-23T05:00:00Z",
         "duration_ms":1000,"audio_url":"/briefing/2026-08-23/audio",
         "segments":[],"turns":[],"sources":[]}
        """
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try! decoder.decode(BriefingManifest.self, from: Data(json.utf8))
    }
}
