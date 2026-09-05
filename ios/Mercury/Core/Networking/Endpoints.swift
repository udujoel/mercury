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
    case status(briefingId: String)
    case audio(briefingId: String)
    /// The browse surface for one morning (ADR-013). A 404 here is ORDINARY:
    /// every briefing generated before the digest existed has none.
    case digest(briefingId: String)
    /// Which mornings are still in the bucket. The window is whatever the
    /// retention sweep has left, so the archive reads its horizon from the
    /// data rather than hardcoding a number that can drift from the
    /// sweeper's.
    case briefingIndex
    /// The settings artifact. The ONE writable object — see the note on the
    /// PUT branch in `worker/src/index.js` for why the read-only property
    /// this narrows is about briefing DATA, not about Google scopes.
    case settings
    /// The ElevenLabs voice library, proxied by the Worker so the API key
    /// never has to live on the phone.
    case voices
    /// Rebuild today's briefing from mail that has arrived since.
    /// POST, and the only route here that costs money to call.
    case regenerate
    // Week 11 addition: hands the APNs device token to the backend so the
    // server cron can send the PRIMARY-layer silent push (ADR-001, PRD 3.6).
    // See backend/app/api/devices.py — guarded by the same X-Mercury-Token
    // dependency as the briefing routes.
    case registerDevice
    /// until the real route lands and can be reconciled against.

    func url(base: URL = APIConfig.baseURL) -> URL {
        switch self {
        case .generate:
            return base.appendingPathComponent("briefing/generate")
        case .manifest(let id):
            return base.appendingPathComponent("briefing/\(id)")
        case .status(let id):
            return base.appendingPathComponent("briefing/\(id)/status")
        case .audio(let id):
            return base.appendingPathComponent("briefing/\(id)/audio")
        case .digest(let id):
            return base.appendingPathComponent("briefing/\(id)/digest")
        case .briefingIndex:
            return base.appendingPathComponent("briefings")
        case .settings:
            return base.appendingPathComponent("settings")
        case .voices:
            return base.appendingPathComponent("voices")
        case .regenerate:
            return base.appendingPathComponent("regenerate")
        case .registerDevice:
            return base.appendingPathComponent("devices/register")
        }
    }

    var method: String {
        switch self {
        case .generate, .registerDevice, .regenerate: return "POST"
        case .manifest, .audio, .status, .digest, .briefingIndex, .settings,
             .voices:
            return "GET"
        }
    }
}
