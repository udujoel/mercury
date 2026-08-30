import Foundation

enum APIError: Error, LocalizedError {
    case badStatus(Int, String)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .badStatus(let code, let body):
            return "Backend returned \(code): \(body)"
        case .decoding(let error):
            return "Could not decode response: \(error)"
        case .transport(let error):
            return "Network error: \(error)"
        }
    }
}

/// Thin `URLSession` wrapper. Async/await only, no completion handlers, no
/// third-party dependency — the API surface is three endpoints.
protocol APIClientProtocol {
    func generateBriefing(length: BriefingLength) async throws -> BriefingManifest
    func generationStatus(briefingId: String) async throws -> GenerationStatus
    func syncServerClock() async
    func fetchManifest(briefingId: String) async throws -> BriefingManifest
    func downloadAudio(briefingId: String) async throws -> URL
    /// Registers (or refreshes) this device's APNs token with the backend
    /// so the server cron can wake it with a silent push. Mirrors
    /// `POST /devices/register` in `backend/app/api/devices.py`.
    func registerDevice(token: String) async throws
}

final class APIClient: APIClientProtocol {

    private let session: URLSession
    private let decoder: JSONDecoder

    init(session: URLSession? = nil) {
        // URLSession's default request timeout is 60 seconds. A real live
        // generation — two Gemini passes plus a TTS call per turn — measured
        // 71 seconds end to end, so the default would cancel the request just
        // before the briefing was ready and surface as an opaque network
        // error. The PRD budgets 90 seconds for tap-to-audio; allow well
        // past that so a slow morning degrades into a wait, not a failure.
        self.session = session ?? {
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 180
            config.timeoutIntervalForResource = 300
            return URLSession(configuration: config)
        }()
        self.decoder = JSONDecoder.mercury()
    }

    func generateBriefing(length: BriefingLength) async throws -> BriefingManifest {
        try await requestGenerate(length: length)
    }

    func generationStatus(briefingId: String) async throws -> GenerationStatus {
        var request = URLRequest(url: Endpoint.status(briefingId: briefingId).url())
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // A cached "generating" would make `waitForBriefing` poll a frozen
        // answer until it times out seven minutes later.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        Self.authorize(&request)
        do {
            let (data, response) = try await session.data(for: request)
            try Self.validate(response, body: data)
            return try decoder.decode(GenerationStatus.self, from: data)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error)
        }
    }

    func fetchManifest(briefingId: String) async throws -> BriefingManifest {
        try await request(.manifest(briefingId: briefingId))
    }

    /// Downloads the briefing audio to a local temp file and returns its
    /// URL. The file extension is derived from the response's `Content-Type`
    /// (the backend may emit `.wav` when ffmpeg is unavailable, or `.m4a`
    /// when it is) — never hardcoded, and never guessed from a filename the
    /// server doesn't send.
    func downloadAudio(briefingId: String) async throws -> URL {
        let endpoint = Endpoint.audio(briefingId: briefingId)
        let directory = try Self.audioCacheDirectory()
        let cached = Self.cachedAudioURL(briefingId: briefingId, in: directory)

        var request = URLRequest(url: endpoint.url())
        request.httpMethod = endpoint.method
        // Revalidation is managed here rather than left to `URLCache`:
        // download tasks and the URL cache interact inconsistently, and the
        // whole point is to know whether we were served fresh bytes or told
        // to keep what we have.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        Self.authorize(&request)

        // The ONE thing that makes reusing a cached file safe. `--force`
        // regeneration reuses the same briefing id (see the `--force` /
        // `--briefing-id` work in the backend), so an id match alone proves
        // nothing about whether the audio changed. The Worker already sends
        // an ETag on every audio response; ask it to tell us.
        //
        // Only ask when the file we would keep is worth keeping. Sending the
        // header for a truncated or zero-byte cache would earn a 304 whose
        // body is empty by definition, and installing THAT is how a bad
        // download becomes permanent.
        let revalidating = cached.map(Self.isUsable) ?? false
        if revalidating, let etag = Self.cachedETag(briefingId: briefingId) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }

        do {
            let (tempURL, response) = try await session.download(for: request)
            let http = response as? HTTPURLResponse

            if http?.statusCode == 304 {
                try? FileManager.default.removeItem(at: tempURL)
                guard revalidating, let cached else {
                    // A 304 we did not ask for. The body is empty, so there
                    // is nothing to install and nothing to fall back on.
                    throw APIError.badStatus(
                        304,
                        "the audio was reported unchanged, but there is no "
                            + "usable copy on this device"
                    )
                }
                // Unchanged: keep the ~4MB already on disk.
                return cached
            }

            try Self.validate(response)

            let ext = Self.fileExtension(forMimeType: response.mimeType)
            let destination = Self.audioURL(briefingId: briefingId, ext: ext, in: directory)

            // A regeneration can switch format (the backend falls back to
            // WAV without ffmpeg). Drop the stale sibling so `cachedAudioURL`
            // cannot later find the wrong one.
            for other in Self.knownAudioExtensions where other != ext {
                try? FileManager.default.removeItem(
                    at: Self.audioURL(briefingId: briefingId, ext: other, in: directory)
                )
            }

            try Self.install(tempURL, at: destination)
            Self.rememberETag(http?.value(forHTTPHeaderField: "Etag"), briefingId: briefingId)
            return destination
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error)
        }
    }

    // MARK: - Audio cache

    /// Both formats the backend can serve, in the same order as
    /// `KNOWN_AUDIO_FORMATS` in `backend/app/synthesis/storage.py` and the
    /// probe loop in `worker/src/index.js`.
    static let knownAudioExtensions = ["m4a", "wav"]

    /// Caches, not tmp.
    ///
    /// The background prefetch (PRD F-17) downloads overnight so the morning
    /// tap is instant. That only works if the file is still there hours
    /// later, and the system reclaims `tmp` far more eagerly than `Caches`.
    /// Both are purgeable under storage pressure, which is correct: a missing
    /// cache file simply means downloading again.
    static func audioCacheDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ).appendingPathComponent("Briefings", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base
    }

    static func audioURL(briefingId: String, ext: String, in directory: URL) -> URL {
        directory.appendingPathComponent("\(briefingId)-audio").appendingPathExtension(ext)
    }

    /// Probes BOTH known extensions: which one exists depends on whether the
    /// server had ffmpeg when it generated, which the client cannot know
    /// ahead of asking.
    /// Whether a cached file is worth trusting.
    ///
    /// Without this a truncated or zero-byte install was permanent: the ETag
    /// recorded alongside it still matched, so every later request answered
    /// 304 and returned the broken file, and nothing would ever fetch the
    /// bytes again. The cache poisoned itself and the player got an
    /// unplayable file every morning. A few hundred bytes is well under any
    /// real briefing and well over an empty file.
    static let minimumUsableAudioBytes = 1_024

    static func isUsable(_ url: URL) -> Bool {
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size]) as? Int
        return (size ?? 0) >= minimumUsableAudioBytes
    }

    static func cachedAudioURL(briefingId: String, in directory: URL) -> URL? {
        for ext in knownAudioExtensions {
            let candidate = audioURL(briefingId: briefingId, ext: ext, in: directory)
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        }
        return nil
    }

    /// Moves a freshly downloaded file into place with no window in which
    /// the destination is missing.
    ///
    /// The old sequence was `removeItem(at:)` then `moveItem(at:to:)`, which
    /// leaves a gap between the two calls. Two downloads of the same briefing
    /// — which tapping the greeting card twice used to start — could
    /// interleave there: one removes, the other moves, the first then moves
    /// onto an existing file and throws. `replaceItemAt` closes the gap.
    static func install(_ source: URL, at destination: URL) throws {
        if FileManager.default.fileExists(atPath: destination.path) {
            // replaceItemAt returns the URL of the resulting item, which is
            // not contractually the destination passed in. Same-directory
            // replacement makes them coincide today, but discarding it and
            // assuming otherwise would surface as a missing file at playback
            // rather than as an error here.
            let replaced = try FileManager.default.replaceItemAt(destination, withItemAt: source)
            if let replaced, replaced != destination {
                try? FileManager.default.removeItem(at: destination)
                try FileManager.default.moveItem(at: replaced, to: destination)
            }
            return
        }
        do {
            try FileManager.default.moveItem(at: source, to: destination)
        } catch CocoaError.fileWriteFileExists {
            // Lost the race between the check above and the move.
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: source)
        }
    }

    private static func etagKey(_ briefingId: String) -> String {
        "mercury.audioETag.\(briefingId)"
    }

    static func cachedETag(briefingId: String) -> String? {
        UserDefaults.standard.string(forKey: etagKey(briefingId))
    }

    static func rememberETag(_ etag: String?, briefingId: String) {
        guard let etag, !etag.isEmpty else {
            // No validator means we cannot prove a cached file is current,
            // so forget any old one rather than revalidate against a stale
            // ETag and be told 304 about the wrong bytes.
            UserDefaults.standard.removeObject(forKey: etagKey(briefingId))
            return
        }
        UserDefaults.standard.set(etag, forKey: etagKey(briefingId))
    }

    func registerDevice(token: String) async throws {
        var urlRequest = URLRequest(url: Endpoint.registerDevice.url())
        urlRequest.httpMethod = Endpoint.registerDevice.method
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.httpBody = try? JSONEncoder().encode(RegisterDeviceRequestBody(deviceToken: token))
        Self.authorize(&urlRequest)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.transport(error)
        }
        try Self.validate(response, body: data)
    }

    /// Learns the timezone the server names briefing ids in, so locally
    /// derived ids match. Best effort: a failure here just leaves the last
    /// known value (or the device zone) in place. See `BriefingClock`.
    func syncServerClock() async {
        var request = URLRequest(url: APIConfig.baseURL.appendingPathComponent("health"))
        request.httpMethod = "GET"
        guard let (data, _) = try? await session.data(for: request),
              let payload = try? JSONDecoder().decode([String: String].self, from: data),
              let identifier = payload["timezone"]
        else { return }
        BriefingClock.remember(identifier: identifier)
    }

    // MARK: - Private

    /// Mirrors `RegisterRequest` in backend/app/api/devices.py.
    private struct RegisterDeviceRequestBody: Encodable {
        let deviceToken: String

        enum CodingKeys: String, CodingKey {
            case deviceToken = "device_token"
        }
    }

    /// `POST /briefing/generate` wraps the manifest in `{status, manifest}`
    /// (see `backend/app/api/briefing.py::GenerateResponse`), unlike the
    /// other two endpoints which return the manifest directly. Decode the
    /// wrapper, don't decode the manifest shape straight off the body.
    /// `POST /briefing/generate` returns 202 with just an id — the manifest
    /// is fetched separately once the job reports ready.
    private struct GenerateResponseBody: Decodable {
        let status: String
        let briefingId: String

        enum CodingKeys: String, CodingKey {
            case status
            case briefingId = "briefing_id"
        }
    }

    /// Body of `POST /briefing/generate`. Mirrors `GenerateRequest` in
    /// backend/app/api/briefing.py.
    private struct GenerateRequestBody: Encodable {
        let length: BriefingLength
    }

    private func requestGenerate(length: BriefingLength) async throws -> BriefingManifest {
        var urlRequest = URLRequest(url: Endpoint.generate.url())
        urlRequest.httpMethod = Endpoint.generate.method
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        // Sending no body at all made FastAPI reject this with a 422: the
        // request model was a required parameter. The server now tolerates an
        // empty body, but the client still has a real preference to send.
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.httpBody = try? JSONEncoder().encode(GenerateRequestBody(length: length))
        Self.authorize(&urlRequest)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.transport(error)
        }

        try Self.validate(response, body: data)

        // The POST now returns 202 with just a briefing id — generation runs
        // as a background job server-side. Holding the connection open used to
        // produce `Error 524 origin_response_timeout` through the tunnel,
        // because a full live run measures ~127s against Cloudflare's hard
        // 120s proxy read timeout. Poll until it is ready, then fetch.
        let started: GenerateResponseBody
        do {
            started = try decoder.decode(GenerateResponseBody.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
        return try await waitForBriefing(briefingId: started.briefingId)
    }

    /// Polls until generation finishes, then returns the manifest.
    ///
    /// `timeoutSeconds` is generous on purpose: a full live run with the
    /// four-pass script chain and per-turn TTS measured 127 seconds, and a
    /// slow morning could be worse. The failure mode we are avoiding is
    /// giving up on a briefing that was seconds from ready.
    private func waitForBriefing(
        briefingId: String,
        pollInterval: Duration = .seconds(2),
        timeoutSeconds: Double = 420
    ) async throws -> BriefingManifest {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while Date() < deadline {
            let status = try await generationStatus(briefingId: briefingId)
            if status.isReady {
                return try await fetchManifest(briefingId: briefingId)
            }
            if status.isFailed {
                throw APIError.badStatus(500, status.error ?? "generation failed")
            }
            try? await Task.sleep(for: pollInterval)
        }
        throw APIError.badStatus(504, "Generation did not finish within \(Int(timeoutSeconds))s.")
    }

    /// Maps the response `Content-Type` to a file extension. Falls back to
    /// `.m4a` (the intended production format) only when the server sends
    /// no usable media type at all.
    private static func fileExtension(forMimeType mimeType: String?) -> String {
        switch mimeType {
        case "audio/wav", "audio/x-wav", "audio/wave":
            return "wav"
        case "audio/mp4", "audio/m4a", "audio/aac":
            return "m4a"
        default:
            return "m4a"
        }
    }

    private func request(_ endpoint: Endpoint) async throws -> BriefingManifest {
        var urlRequest = URLRequest(url: endpoint.url())
        urlRequest.httpMethod = endpoint.method
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        // NEVER serve a manifest from cache on freshness alone.
        //
        // `worker/src/index.js` sends `cache-control: private, max-age=3600`,
        // so URLSession's default policy would hand back an hour-old manifest
        // without asking the server anything. A `--force` regeneration REUSES
        // the briefing id, so that stale manifest describes different audio:
        // every turn timing and every chapter boundary lands wrong against
        // the file that actually plays, and the transcript drifts further
        // the longer you listen. A manifest is tens of kilobytes; the round
        // trip is cheap and the alternative is silently broken.
        urlRequest.cachePolicy = .reloadIgnoringLocalCacheData
        Self.authorize(&urlRequest)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.transport(error)
        }

        try Self.validate(response, body: data)

        do {
            return try decoder.decode(BriefingManifest.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    /// Attaches the shared secret to a request. Every briefing route requires
    /// carries transcript and inbox-derived context and must be guarded by
    /// the same `require_api_token` dependency per the phase brief's
    /// security posture section.
    static func authorize(_ request: inout URLRequest) {
        guard let token = APIConfig.apiToken else {
            print(
                "[Mercury] MERCURY_API_TOKEN is not set in Secrets.xcconfig; "
                + "briefing requests will be rejected with 401. Copy the value "
                + "from backend/.env."
            )
            return
        }
        request.setValue(token, forHTTPHeaderField: "X-Mercury-Token")
    }

    private static func validate(_ response: URLResponse, body: Data = Data()) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let text = String(data: body, encoding: .utf8) ?? ""
            throw APIError.badStatus(http.statusCode, text)
        }
    }
}


/// Mirrors `StatusResponse` in backend/app/api/briefing.py.
struct GenerationStatus: Decodable, Sendable {
    let briefingId: String
    let status: String   // generating | ready | failed | unknown
    let elapsedS: Double?
    let error: String?

    var isReady: Bool { status == "ready" }
    var isFailed: Bool { status == "failed" }

    enum CodingKeys: String, CodingKey {
        case briefingId = "briefing_id"
        case status
        case elapsedS = "elapsed_s"
        case error
    }
}


// MARK: - Date decoding

/// Parses the two ISO 8601 shapes the backend can produce.
///
/// `backend/app/synthesis/stitch.py` stamps `generated_at` with
/// `datetime.now(UTC)` and pydantic's `model_dump_json` serialises it at
/// microsecond precision — a real manifest in `backend/out/` reads
/// `"2026-08-23T10:49:22.073032Z"`.
///
/// `JSONDecoder`'s built-in `.iso8601` strategy is VERSION DEPENDENT here,
/// which is the whole reason this exists:
///
/// - Through iOS 17 — this app's deployment target — it is backed by
///   `ISO8601DateFormatter` with `.withInternetDateTime` only, an option
///   set that rejects fractional seconds outright. Measured directly:
///   `ISO8601DateFormatter(.withInternetDateTime).date(from:)` returns nil
///   for the string above. On iOS 17 every real manifest fetch fails to
///   decode.
/// - On the swift-foundation rewrite (iOS 18+) the same strategy parses it
///   and keeps full precision. Measured: 1787482162.073032.
///
/// So the bug is invisible on any simulator installed on this machine
/// (iOS 26.5 and 27.0 are the only runtimes present) and fatal on the
/// oldest OS the project claims to support. A `.custom` strategy removes
/// the version dependence entirely rather than betting on which Foundation
/// the device ships.
///
/// Both forms are accepted deliberately: a timestamp landing exactly on a
/// whole second serialises without a fractional part, so a parser that only
/// handled microseconds would fail on a one-in-a-million morning.
///
/// Resolution is MILLISECONDS: `.withFractionalSeconds` reads three
/// fractional digits and discards the rest, so `.073032` becomes `.073`.
/// `generated_at` dates and orders one briefing per day, so this is
/// irrelevant in practice — but it is a real difference from the iOS 18+
/// built-in, and `testTimestampParserAcceptsBothWireShapes` pins it so it
/// stays a decision rather than a surprise.
enum ISO8601Timestamp {

    private static let withFractionalSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let wholeSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func date(from text: String) -> Date? {
        withFractionalSeconds.date(from: text) ?? wholeSeconds.date(from: text)
    }
}

extension JSONDecoder {

    /// The one decoder configuration the app uses for backend JSON.
    ///
    /// Exposed (rather than configured privately inside `APIClient.init`)
    /// so tests decode fixtures through exactly what production uses.
    /// `ManifestDecodingTests` previously built its own `JSONDecoder` with
    /// its own `.iso8601` strategy, which meant the decoding tests could
    /// not have caught a wrong strategy in `APIClient` even with a faithful
    /// fixture — they were testing a copy.
    static func mercury() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = ISO8601Timestamp.date(from: text) else {
                throw DecodingError.dataCorruptedError(
                    in: container,
                    debugDescription: "Expected an ISO 8601 timestamp, got \"\(text)\"."
                )
            }
            return date
        }
        return decoder
    }
}
