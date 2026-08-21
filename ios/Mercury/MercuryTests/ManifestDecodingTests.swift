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
