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
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        self.decoder = decoder
    }

    func generateBriefing(length: BriefingLength) async throws -> BriefingManifest {
        try await requestGenerate(length: length)
    }

    func generationStatus(briefingId: String) async throws -> GenerationStatus {
        var request = URLRequest(url: Endpoint.status(briefingId: briefingId).url())
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
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
        var request = URLRequest(url: endpoint.url())
        request.httpMethod = endpoint.method
        Self.authorize(&request)

        do {
            let (tempURL, response) = try await session.download(for: request)
            try Self.validate(response)

            let ext = Self.fileExtension(forMimeType: response.mimeType)
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("\(briefingId)-audio")
                .appendingPathExtension(ext)

            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.removeItem(at: destination)
            }
            try FileManager.default.moveItem(at: tempURL, to: destination)
            return destination
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error)
        }
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
    /// it — see backend/app/core/auth.py. Not private: `VoiceAskClient` uses
    /// the exact same shared-secret scheme against `POST /voice/ask`, which
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
