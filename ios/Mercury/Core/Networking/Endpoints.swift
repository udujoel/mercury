import Foundation

/// Backend base URL. Configurable via the `API_BASE_URL` build setting
/// (see `Secrets.xcconfig`, gitignored, templated by `Secrets.example.xcconfig`).
/// Defaults to the local dev server so a fresh checkout builds and runs
/// against `uvicorn` on localhost with zero configuration.
enum APIConfig {
    static var baseURL: URL {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
           !raw.isEmpty,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://localhost:8000")!
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
