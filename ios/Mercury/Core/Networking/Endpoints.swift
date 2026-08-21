import Foundation

/// Backend base URL. Configurable via the `API_BASE_URL` build setting
/// (see `Secrets.xcconfig`, gitignored, templated by `Secrets.example.xcconfig`).
/// Defaults to the local dev server so a fresh checkout builds and runs
/// against `uvicorn` on localhost with zero configuration.
enum APIConfig {
    static let fallback = URL(string: "http://localhost:8000")!

    static var baseURL: URL {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
              !raw.isEmpty,
              let url = URL(string: raw),
              // A non-nil URL is NOT enough. xcconfig eats everything after
              // "//" as a comment, so "http://localhost:8000" arrives here as
              // "http:" — which parses cleanly into a URL with a scheme and
              // no host. Requiring a host caught that, but not the next
              // variant: a full URL pasted into the assembled form produced
              // "http://https:", whose host is the literal string "https".
              // Validate what a usable base URL actually looks like.
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host(), !host.isEmpty,
              !["http", "https"].contains(host.lowercased())
        else {
            // Silence here is what made both bugs expensive to find: requests
            // simply went somewhere meaningless and failed as DNS errors.
            print(
                "[Mercury] API_BASE_URL is unusable; falling back to \(fallback). "
                + "Set API_SCHEME and API_HOST separately in Secrets.xcconfig "
                + "— never paste a full URL, since xcconfig treats // as a "
                + "comment."
            )
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
