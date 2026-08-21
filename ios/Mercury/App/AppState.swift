import Foundation
import Observation
import SwiftData

enum LoadState: Equatable {
    case idle
    case loading
    case ready
    case failed(String)

    static func == (lhs: LoadState, rhs: LoadState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.ready, .ready):
            return true
        case (.failed(let a), .failed(let b)):
            return a == b
        default:
            return false
        }
    }
}

/// Root app state: onboarding completion, the current briefing, and the
/// shared playback coordinator. Views observe this rather than talking to
/// services directly.
@Observable
final class AppState {

    var hasCompletedOnboarding: Bool {
        get { UserDefaults.standard.bool(forKey: Self.onboardingKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.onboardingKey) }
    }

    /// The length chosen during onboarding. Sent with every generate request
    /// so the backend can apply the matching word budget (PRD F-18). Stored
    /// rather than held in the onboarding view, which is torn down the moment
    /// onboarding finishes.
    var briefingLength: BriefingLength {
        get {
            guard let raw = UserDefaults.standard.string(forKey: Self.lengthKey),
                  let value = BriefingLength(rawValue: raw)
            else { return .standard }
            return value
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: Self.lengthKey) }
    }

    var manifest: BriefingManifest?
    var loadState: LoadState = .idle
    let playback = PlaybackCoordinator()

    private let api: APIClientProtocol
    private static let onboardingKey = "mercury.hasCompletedOnboarding"
    private static let lengthKey = "mercury.briefingLength"

    init(api: APIClientProtocol = APIClient()) {
        self.api = api
    }

    /// Tapping the greeting card is the primary action of the entire app:
    /// fetch (or generate) the briefing, download its audio, load it into
    /// the playback engine, and start playing — all before the player
    /// screen is shown.
    @MainActor
    func playBriefing() async {
        loadState = .loading
        do {
            let manifest = try await api.generateBriefing(length: briefingLength)
            self.manifest = manifest
            let audioURL = try await api.downloadAudio(briefingId: manifest.briefingId)
            try playback.load(manifest: manifest, audioURL: audioURL)
            playback.play()
            loadState = .ready
        } catch {
            loadState = .failed(error.localizedDescription)
        }
    }
}
