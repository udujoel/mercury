import XCTest
@testable import Mercury

/// The background prefetch (PRD F-17) downloads the briefing overnight so
/// the morning tap is instant. It used to hand the ~4MB file to
/// `FileManager.temporaryDirectory` under a name nothing ever looked for
/// again, so the morning tap downloaded the whole thing a second time —
/// the prefetch cost the user's battery and data and bought nothing.
///
/// Nothing tested `downloadAudio` at all, which is why. These tests drive
/// the real `APIClient` through a stubbed `URLProtocol`, so the caching,
/// the conditional request and the 304 handling are exercised end to end
/// without a backend.
final class APIClientCacheTests: XCTestCase {

    private var directory: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("cache-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        StubURLProtocol.reset()
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
        StubURLProtocol.reset()
        try super.tearDownWithError()
    }

    private func makeClient() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return APIClient(session: URLSession(configuration: configuration))
    }

    private func clearETag(_ briefingId: String) {
        UserDefaults.standard.removeObject(forKey: "mercury.audioETag.\(briefingId)")
    }

    // MARK: - probing both formats

    /// Which extension exists depends on whether the server had ffmpeg when
    /// it generated, so the cache has to look for both.
    func testCachedAudioProbesBothKnownExtensions() throws {
        XCTAssertEqual(APIClient.knownAudioExtensions, ["m4a", "wav"],
                       "must match KNOWN_AUDIO_FORMATS in the backend")

        XCTAssertNil(APIClient.cachedAudioURL(briefingId: "2026-08-21", in: directory))

        let wav = APIClient.audioURL(briefingId: "2026-08-21", ext: "wav", in: directory)
        try Data("wav bytes".utf8).write(to: wav)
        XCTAssertEqual(APIClient.cachedAudioURL(briefingId: "2026-08-21", in: directory), wav,
                       "the no-ffmpeg fallback format must be found too")

        let m4a = APIClient.audioURL(briefingId: "2026-08-21", ext: "m4a", in: directory)
        try Data("m4a bytes".utf8).write(to: m4a)
        XCTAssertEqual(APIClient.cachedAudioURL(briefingId: "2026-08-21", in: directory), m4a,
                       "m4a is the intended production format and wins the probe")
    }

    // MARK: - collision-safe install

    /// `removeItem` then `moveItem` leaves a window in which the destination
    /// does not exist. Two downloads of the same briefing could interleave
    /// there and the loser threw. `replaceItemAt` has no such window.
    func testInstallOverwritesAnExistingFile() throws {
        let destination = directory.appendingPathComponent("dest.wav")
        try Data("old".utf8).write(to: destination)

        let source = directory.appendingPathComponent("incoming.tmp")
        try Data("new".utf8).write(to: source)

        try APIClient.install(source, at: destination)

        XCTAssertEqual(try Data(contentsOf: destination), Data("new".utf8))
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path),
                       "the temp file must not be left behind")
    }

    func testInstallCreatesTheFileWhenNothingIsThere() throws {
        let destination = directory.appendingPathComponent("fresh.wav")
        let source = directory.appendingPathComponent("incoming.tmp")
        try Data("new".utf8).write(to: source)

        try APIClient.install(source, at: destination)

        XCTAssertEqual(try Data(contentsOf: destination), Data("new".utf8))
    }

    // MARK: - ETag round trip

    func testETagRoundTripsAndClearsWhenAbsent() {
        clearETag("2026-08-21")
        XCTAssertNil(APIClient.cachedETag(briefingId: "2026-08-21"))

        APIClient.rememberETag("\"abc123\"", briefingId: "2026-08-21")
        XCTAssertEqual(APIClient.cachedETag(briefingId: "2026-08-21"), "\"abc123\"")

        // No validator means we cannot prove a cached file is current, so the
        // old one must be forgotten rather than reused against new bytes.
        APIClient.rememberETag(nil, briefingId: "2026-08-21")
        XCTAssertNil(APIClient.cachedETag(briefingId: "2026-08-21"))
        clearETag("2026-08-21")
    }

    // MARK: - the actual prefetch win

    /// First download stores the file and its ETag; the second sends
    /// `If-None-Match` and, on 304, returns the cached file without
    /// re-downloading. This is the behaviour PRD F-17 depends on.
    func testSecondDownloadRevalidatesAndReusesTheCachedFile() async throws {
        let briefingId = "2026-08-24"
        clearETag(briefingId)
        defer { clearETag(briefingId) }

        StubURLProtocol.handler = { request in
            if request.value(forHTTPHeaderField: "If-None-Match") == "\"etag-1\"" {
                return (304, [:], Data())
            }
            return (200, ["Content-Type": "audio/wav", "Etag": "\"etag-1\""],
                    Data("first payload".utf8))
        }

        let client = makeClient()
        let first = try await client.downloadAudio(briefingId: briefingId)
        XCTAssertEqual(try Data(contentsOf: first), Data("first payload".utf8))
        XCTAssertEqual(first.pathExtension, "wav", "format comes from Content-Type")
        XCTAssertEqual(APIClient.cachedETag(briefingId: briefingId), "\"etag-1\"")

        let second = try await client.downloadAudio(briefingId: briefingId)

        XCTAssertEqual(second, first, "a 304 must reuse the file already on disk")
        XCTAssertEqual(try Data(contentsOf: second), Data("first payload".utf8))
        XCTAssertEqual(StubURLProtocol.conditionalRequestCount, 1,
                       "the second call must actually ask If-None-Match")

        try? FileManager.default.removeItem(at: first)
    }

    /// A regenerated briefing keeps its id but gets a new ETag, so the
    /// server answers 200 with new bytes and the cached file must be
    /// replaced — not served.
    func testARegeneratedBriefingReplacesTheCachedFile() async throws {
        let briefingId = "2026-08-25"
        clearETag(briefingId)
        defer { clearETag(briefingId) }

        StubURLProtocol.handler = { _ in
            (200, ["Content-Type": "audio/wav", "Etag": "\"etag-1\""], Data("v1".utf8))
        }
        let client = makeClient()
        let first = try await client.downloadAudio(briefingId: briefingId)
        XCTAssertEqual(try Data(contentsOf: first), Data("v1".utf8))

        // `--force` regeneration: same id, different audio, different ETag.
        StubURLProtocol.handler = { _ in
            (200, ["Content-Type": "audio/wav", "Etag": "\"etag-2\""], Data("v2".utf8))
        }
        let second = try await client.downloadAudio(briefingId: briefingId)

        XCTAssertEqual(try Data(contentsOf: second), Data("v2".utf8),
                       "same briefing id must not mean same audio")
        XCTAssertEqual(APIClient.cachedETag(briefingId: briefingId), "\"etag-2\"")

        try? FileManager.default.removeItem(at: second)
    }

    /// A format switch between generations must not leave the old file
    /// where the probe can find it first.
    func testSwitchingFormatRemovesTheStaleSibling() async throws {
        let briefingId = "2026-08-26"
        clearETag(briefingId)
        defer { clearETag(briefingId) }

        StubURLProtocol.handler = { _ in
            (200, ["Content-Type": "audio/wav", "Etag": "\"w\""], Data("wav".utf8))
        }
        let client = makeClient()
        let wav = try await client.downloadAudio(briefingId: briefingId)
        XCTAssertEqual(wav.pathExtension, "wav")

        StubURLProtocol.handler = { _ in
            (200, ["Content-Type": "audio/mp4", "Etag": "\"m\""], Data("m4a".utf8))
        }
        let m4a = try await client.downloadAudio(briefingId: briefingId)

        XCTAssertEqual(m4a.pathExtension, "m4a")
        XCTAssertFalse(FileManager.default.fileExists(atPath: wav.path),
                       "the stale .wav would win the probe next time")

        try? FileManager.default.removeItem(at: m4a)
    }
}

/// Minimal `URLProtocol` stub. Handles the download tasks `downloadAudio`
/// uses as well as ordinary data tasks.
final class StubURLProtocol: URLProtocol {

    /// Returns (statusCode, headers, body) for a request.
    nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, [String: String], Data))?
    nonisolated(unsafe) static var conditionalRequestCount = 0

    static func reset() {
        handler = nil
        conditionalRequestCount = 0
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler, let url = request.url else {
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
            return
        }
        if request.value(forHTTPHeaderField: "If-None-Match") != nil {
            Self.conditionalRequestCount += 1
        }

        let (status, headers, body) = handler(request)
        let response = HTTPURLResponse(
            url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if !body.isEmpty {
            client?.urlProtocol(self, didLoad: body)
        }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
