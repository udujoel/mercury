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
    func fetchManifest(briefingId: String) async throws -> BriefingManifest
    func downloadAudio(briefingId: String) async throws -> URL
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

    // MARK: - Private

    /// `POST /briefing/generate` wraps the manifest in `{status, manifest}`
    /// (see `backend/app/api/briefing.py::GenerateResponse`), unlike the
    /// other two endpoints which return the manifest directly. Decode the
    /// wrapper, don't decode the manifest shape straight off the body.
    private struct GenerateResponseBody: Decodable {
        let status: String
        let manifest: BriefingManifest
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

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.transport(error)
        }

        try Self.validate(response, body: data)

        do {
            return try decoder.decode(GenerateResponseBody.self, from: data).manifest
        } catch {
            throw APIError.decoding(error)
        }
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

    private static func validate(_ response: URLResponse, body: Data = Data()) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let text = String(data: body, encoding: .utf8) ?? ""
            throw APIError.badStatus(http.statusCode, text)
        }
    }
}
