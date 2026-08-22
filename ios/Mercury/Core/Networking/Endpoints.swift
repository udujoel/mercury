import Foundation

/// Backend base URL. Configurable via the `API_BASE_URL` build setting
/// (see `Secrets.xcconfig`, gitignored, templated by `Secrets.example.xcconfig`).
/// Defaults to the local dev server so a fresh checkout builds and runs
/// against `uvicorn` on localhost with zero configuration.
enum APIConfig {
    static let fallback = URL(string: "http://localhost:8000")!

    /// Shared secret for the briefing API, from Secrets.xcconfig (gitignored).
    /// The backend fails closed, so a missing value means every request is
    /// rejected with 401 rather than quietly returning someone else's data.
    static var apiToken: String? {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: "MERCURY_API_TOKEN") as? String,
              !raw.isEmpty,
              !raw.hasPrefix("replace-me")
        else { return nil }
        return raw
    }

    static var baseURL: URL {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
              !raw.isEmpty,
              let url = URL(string: raw),
              // A non-nil URL is NOT enough. xcconfig eats everything after
              // "//" as a comment, so "http://localhost:8000" arrives here as
              // the string "http:". That parses cleanly into a URL with a
              // scheme and no host, so the old `let url = URL(string:)` check
              // passed and every request went to http://briefing/generate and
              // died in DNS. Require an actual host.
              let host = url.host(), !host.isEmpty
        else {
            return fallback
        }
        return url
    }
}

/// The three endpoints the Phase 1 client needs. No third-party networking
/// dependency — plain `URLSession` + `async/await`.
enum Endpoint {
    case generate
    case manifest(briefingId: String)
    case audio(briefingId: String)

    func url(base: URL = APIConfig.baseURL) -> URL {
        switch self {
        case .generate:
            return base.appendingPathComponent("briefing/generate")
        case .manifest(let id):
            return base.appendingPathComponent("briefing/\(id)")
        case .audio(let id):
            return base.appendingPathComponent("briefing/\(id)/audio")
        }
    }

    var method: String {
        switch self {
        case .generate: return "POST"
        case .manifest, .audio: return "GET"
        }
    }
}
